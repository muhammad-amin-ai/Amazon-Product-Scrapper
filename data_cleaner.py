"""
data_cleaner.py
----------------
Pure data-cleaning functions. "Pure" means: no Selenium, no BeautifulSoup,
no file I/O, no network calls. Every function here takes plain Python
data in (strings, dicts, lists) and returns cleaned data out.

WHY THIS MATTERS:
Keeping this file free of side effects (no browser, no disk writes) makes
it easy to:
  - Test in isolation (just call the function with sample input).
  - Reuse later (e.g. if you build a database or API version of this
    project, this exact file can be reused unchanged).

This file is responsible for:
  1. Trimming/normalizing text (extra spaces, inconsistent casing).
  2. Standardizing RAM/Storage formatting (e.g. "16 GB RAM" -> "16GB").
  3. Making sure every record has every expected column
     (missing values become "Not Available" -- never blank).
  4. Removing duplicate products (same Product URL appearing twice,
     which commonly happens with sponsored listings repeating).
"""

import logging
import re
from datetime import datetime

import config

logger = logging.getLogger(__name__)


def clean_text(value: str) -> str:
    """
    Strip leading/trailing whitespace and collapse internal multiple
    spaces/newlines into a single space.

    Example:
        "  16   GB   RAM \\n" -> "16 GB RAM"
    """
    if not value or not isinstance(value, str):
        return config.NOT_AVAILABLE

    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned if cleaned else config.NOT_AVAILABLE


def normalize_ram(value: str) -> str:
    """
    Normalize RAM values into a consistent "NNGB" format.

    Examples:
        "16 GB RAM"        -> "16GB"
        "16GB DDR5"        -> "16GB"
        "8 GB"             -> "8GB"
    """
    if not value or value == config.NOT_AVAILABLE:
        return config.NOT_AVAILABLE

    match = re.search(r"(\d{1,3})\s?GB", value, re.IGNORECASE)
    if match:
        return f"{match.group(1)}GB"

    return clean_text(value)


def normalize_storage(value: str) -> str:
    """
    Normalize storage values into a consistent "NNN(GB/TB) TYPE" format.

    Examples:
        "512 GB SSD Storage" -> "512GB SSD"
        "1 TB HDD"           -> "1TB HDD"
        "256GB"              -> "256GB"
    """
    if not value or value == config.NOT_AVAILABLE:
        return config.NOT_AVAILABLE

    size_match = re.search(r"(\d{1,4})\s?(GB|TB)", value, re.IGNORECASE)
    type_match = re.search(r"(SSD|HDD|eMMC)", value, re.IGNORECASE)

    if size_match:
        size_str = f"{size_match.group(1)}{size_match.group(2).upper()}"
        if type_match:
            return f"{size_str} {type_match.group(1).upper()}"
        return size_str

    return clean_text(value)


def normalize_price(value: str) -> str:
    """
    Clean up a price string while PRESERVING its actual currency symbol
    (e.g. "$", "PKR", "AED") rather than assuming USD. Amazon sometimes
    localizes displayed currency based on detected shopper region, even
    on the .com domain, so forcing "$" here would silently mislabel a
    PKR/AED/etc. price as USD. Falls back to "Not Available" for
    anything unparseable (e.g. "Currently unavailable").
    """
    if not value or value == config.NOT_AVAILABLE:
        return config.NOT_AVAILABLE

    # Capture whatever non-numeric currency marker precedes the number
    # (could be "$", "PKR ", "AED ", etc.) plus the numeric amount.
    match = re.search(r"([^\d]*)([\d,]+\.?\d{0,2})", value)
    if not match:
        return config.NOT_AVAILABLE

    currency = match.group(1).strip() or "$"
    amount = match.group(2)

    # Multi-letter currency codes (PKR, AED, USD...) read better with a
    # space before the amount; single-character symbols ($, £, €) don't.
    separator = " " if len(currency) > 1 else ""
    return f"{currency}{separator}{amount}"


def normalize_rating(value: str) -> str:
    """
    Extract just the numeric rating out of Amazon's verbose string.

    Example:
        "4.5 out of 5 stars" -> "4.5"
    """
    if not value or value == config.NOT_AVAILABLE:
        return config.NOT_AVAILABLE

    match = re.search(r"(\d\.?\d?)\s?out of\s?5", value, re.IGNORECASE)
    if match:
        return match.group(1)

    return clean_text(value)


def normalize_review_count(value: str) -> str:
    """
    Strip commas/text from review counts so the column stores a clean
    number as a string (e.g. "1,204 ratings" -> "1204").
    """
    if not value or value == config.NOT_AVAILABLE:
        return config.NOT_AVAILABLE

    match = re.search(r"([\d,]+)", value)
    if match:
        return match.group(1).replace(",", "")

    return config.NOT_AVAILABLE


def fill_missing_fields(record: dict) -> dict:
    """
    Guarantee that `record` contains every column defined in
    config.FINAL_COLUMNS. Any column not already present is added with
    the value config.NOT_AVAILABLE.

    This is what enforces the project rule: "No empty cells should exist."

    Args:
        record: a single product's data dict (may be missing keys).

    Returns:
        dict: a new dict containing every FINAL_COLUMNS key.
    """
    complete_record = {}
    for column in config.FINAL_COLUMNS:
        value = record.get(column)
        if value is None or (isinstance(value, str) and not value.strip()):
            complete_record[column] = config.NOT_AVAILABLE
        else:
            complete_record[column] = value
    return complete_record


def remove_duplicates(records: list[dict], key: str = "Product URL") -> list[dict]:
    """
    Remove duplicate product records based on a unique key (Product URL
    by default, since two different listings should never share a URL).

    Args:
        records: list of product dicts.
        key: dictionary key to check uniqueness against.

    Returns:
        list[dict]: records with duplicates removed (first occurrence kept).
    """
    seen_keys = set()
    unique_records = []

    for record in records:
        identifier = record.get(key)

        # If the identifier itself is missing/unavailable, we can't safely
        # deduplicate on it -- keep the record rather than risk dropping
        # a legitimate, unique product.
        if not identifier or identifier == config.NOT_AVAILABLE:
            unique_records.append(record)
            continue

        if identifier not in seen_keys:
            seen_keys.add(identifier)
            unique_records.append(record)
        else:
            logger.debug("Duplicate product skipped: %s", identifier)

    removed_count = len(records) - len(unique_records)
    if removed_count:
        logger.info("Removed %d duplicate product(s).", removed_count)

    return unique_records


def clean_dataset(records: list[dict]) -> list[dict]:
    """
    Run the FULL cleaning pipeline over a list of merged product records
    (listing info + specs, already combined by main.py).

    Pipeline steps, in order:
        1. Clean/normalize individual text fields.
        2. Add the "Scraped Date" field.
        3. Fill any missing columns with "Not Available".
        4. Remove duplicate products by Product URL.

    Args:
        records: raw merged product dicts.

    Returns:
        list[dict]: fully cleaned, schema-complete records, ready for export.
    """
    scraped_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cleaned = []

    for record in records:
        working = dict(record)  # avoid mutating the caller's data

        # Text cleanup
        for text_field in (
            "Product Title", "Brand", "Model", "Model Number", "Processor",
            "GPU", "Display Size", "Resolution", "Refresh Rate",
            "Operating System", "Weight", "Color", "Availability", "Warranty",
        ):
            if text_field in working:
                working[text_field] = clean_text(working[text_field])

        # Specialized normalization
        if "RAM" in working:
            working["RAM"] = normalize_ram(working["RAM"])
        if "Storage Size" in working:
            working["Storage Size"] = normalize_storage(working["Storage Size"])
        if "Current Price" in working:
            working["Current Price"] = normalize_price(working["Current Price"])
        if "Rating" in working:
            working["Rating"] = normalize_rating(working["Rating"])
        if "Review Count" in working:
            working["Review Count"] = normalize_review_count(working["Review Count"])

        working["Scraped Date"] = scraped_date

        # Guarantee every expected column exists (no blank cells).
        working = fill_missing_fields(working)
        cleaned.append(working)

    cleaned = remove_duplicates(cleaned, key="Product URL")

    logger.info("Cleaning complete. %d unique product(s) ready for export.", len(cleaned))
    return cleaned
