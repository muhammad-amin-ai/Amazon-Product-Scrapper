"""
product_parser.py
------------------
Handles extraction of DETAILED PRODUCT SPECIFICATIONS from an individual
Amazon product page (as opposed to scraper.py, which only handles the
search RESULTS page).

Why is this separate from scraper.py?
Amazon's product detail pages have a completely different HTML structure
than search results pages. Mixing both types of parsing into one file
would make that file large, harder to test, and harder to fix when only
one of the two page types changes. Separating them means each file has
a single, clear job.

STRATEGY:
Amazon shows technical specs in a few different possible layouts
depending on the product/category:
    1. A "Technical Details" / "Product information" table
       (rows of <tr><th>Label</th><td>Value</td></tr>)
    2. A bullet-point "Feature bullets" list (less structured, but often
       contains RAM/CPU/Storage info in plain text)

We try strategy #1 first (more reliable, structured), and fall back to
strategy #2 if a value wasn't found. Any spec we still can't find gets
filled with config.NOT_AVAILABLE later.
"""

import logging
import re
import time

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config

logger = logging.getLogger(__name__)

# Maps the many different label wordings Amazon uses across categories/
# sellers to our single, standardized column names. This is the key trick
# that makes parsing robust: sellers phrase things differently
# ("CPU Model" vs "Processor" vs "Processor Type"), but we want ONE
# consistent column in the final spreadsheet.
SPEC_LABEL_MAP = {
    "Brand": "Brand",
    "Manufacturer": "Brand",
    "Series": "Model",
    "Model Name": "Model",
    "Item model number": "Model Number",
    "Model Number": "Model Number",
    "Part Number": "Model Number",
    "Processor": "Processor",
    "CPU Model": "Processor",
    "Processor Type": "Processor",
    "Processor Speed": "Processor",
    # --- RAM-only labels ---------------------------------------------
    # NOTE: "Memory Storage Capacity" and "Computer Memory Size" are
    # deliberately ambiguous on real Amazon listings -- sellers reuse
    # them for BOTH RAM and total storage depending on category. We map
    # them to RAM here as a first guess, but `_classify_memory_row()`
    # (below) re-checks the VALUE text of every RAM/Storage row and
    # reclassifies it if the value clearly describes the other one.
    # This is the actual root-cause fix for the "Storage saved as RAM"
    # bug -- label-only mapping was never reliable enough on its own.
    "RAM": "RAM",
    "Installed RAM": "RAM",
    "RAM Size": "RAM",
    "RAM Memory Installed Size": "RAM",
    "Memory Storage Capacity": "RAM",
    "Computer Memory Size": "RAM",
    "Memory": "RAM",
    "Installed Memory": "RAM",
    "System Memory": "RAM",
    "Memory Size": "RAM",
    # --- Storage-only labels ------------------------------------------
    "Hard Drive Size": "Storage Size",
    "Hard Disk Size": "Storage Size",
    "Storage": "Storage Size",
    "SSD Capacity": "Storage Size",
    "Hard Drive": "Storage Size",
    "Hard Disk Description": "Storage Size",
    "Internal Storage": "Storage Size",
    "Storage Capacity": "Storage Size",
    "Total Storage": "Storage Size",
    "Graphics Coprocessor": "GPU",
    "Graphics Card Description": "GPU",
    "Graphics Chipset Brand": "GPU",
    "GPU": "GPU",
    "Graphics Card Ram Size": "GPU",
    "Screen Size": "Display Size",
    "Standing screen display size": "Display Size",
    "Display Resolution Maximum": "Resolution",
    "Resolution": "Resolution",
    "Screen Resolution": "Resolution",
    "Refresh Rate": "Refresh Rate",
    "Operating System": "Operating System",
    "OS": "Operating System",
    "Item Weight": "Weight",
    "Color": "Color",
    "Colour": "Color",
    "Availability": "Availability",
    "Warranty": "Warranty",
    "Warranty Description": "Warranty",
    "Warranty & Support": "Warranty",
}

# Keywords used to double-check the VALUE text of a row that was labeled
# RAM or Storage. Amazon sellers are inconsistent about which label they
# put memory info under, so the label alone is not trustworthy -- we
# also look at what the value actually says before accepting it.
RAM_VALUE_KEYWORDS = re.compile(
    r"\b(RAM|DDR3|DDR4|DDR5|LPDDR3|LPDDR4|LPDDR4X|LPDDR5|LPDDR5X|SDRAM)\b",
    re.IGNORECASE,
)
STORAGE_VALUE_KEYWORDS = re.compile(
    r"\b(SSD|HDD|NVME|PCIe\s?SSD|M\.?2\s?SSD|eMMC|Hard\s?Drive|Flash\s?Memory\s?Solid\s?State)\b",
    re.IGNORECASE,
)


def _classify_memory_row(standardized_key: str, value: str) -> str:
    """
    Root-cause fix for the "Storage getting saved into RAM" bug.

    Amazon (and third-party sellers) do not use "RAM" vs "Storage Size"
    labels consistently -- the SAME label ("Memory Storage Capacity",
    "Computer Memory Size", etc.) can describe either one depending on
    category/seller. Trusting the label alone was the root cause of
    storage values landing in the RAM column.

    This function re-checks the VALUE text whenever a row was tentatively
    classified as "RAM" or "Storage" and flips the classification if the
    value clearly belongs to the other field.

    Args:
        standardized_key: the column name SPEC_LABEL_MAP produced
                           ("RAM" or "Storage Size" -- anything else is
                           returned unchanged).
        value: the raw text extracted from the page for this row.

    Returns:
        str: the (possibly corrected) standardized column name.
    """
    if standardized_key not in ("RAM", "Storage Size"):
        return standardized_key

    has_storage_word = bool(STORAGE_VALUE_KEYWORDS.search(value))
    has_ram_word = bool(RAM_VALUE_KEYWORDS.search(value))

    if standardized_key == "RAM" and has_storage_word and not has_ram_word:
        return "Storage Size"
    if standardized_key == "Storage Size" and has_ram_word and not has_storage_word:
        return "RAM"

    return standardized_key


def fetch_product_page(driver: webdriver.Chrome, url: str, max_attempts: int = 3) -> str | None:
    """
    Load a single product detail page and return its HTML.

    Confirmed cause of intermittent "everything Not Available" rows: for a
    handful of products in a long run, Amazon occasionally serves a
    CAPTCHA/robot-check page or the request simply times out (a normal,
    expected part of scraping at scale, not a selector bug -- the SAME
    code correctly extracts full specs for the vast majority of products
    in the same run). Rather than giving up after one failed attempt, we
    now retry a couple of times with a short backoff pause, which
    resolves most of these transient failures.

    Args:
        driver: an active Selenium WebDriver (reused across products for
                efficiency instead of opening a new browser every time).
        url: the product's URL.
        max_attempts: how many times to try before giving up.

    Returns:
        str | None: page HTML, or None if all attempts failed.
    """
    if not url or url == config.NOT_AVAILABLE:
        logger.warning("Skipping product with missing URL.")
        return None

    for attempt in range(1, max_attempts + 1):
        try:
            driver.get(url)
            WebDriverWait(driver, config.ELEMENT_WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.ID, "productTitle"))
            )
            time.sleep(config.REQUEST_DELAY_SECONDS)  # polite delay
            return driver.page_source

        except TimeoutException:
            # Distinguish a CAPTCHA/robot-check block from a plain slow
            # page load -- this makes the log far more useful for
            # diagnosing WHY a product failed, instead of just "timed out".
            page_snippet = driver.page_source[:1000].lower() if driver.page_source else ""
            if "captcha" in page_snippet or "robot check" in page_snippet or "something went wrong" in page_snippet:
                logger.warning(
                    "Amazon served a CAPTCHA/robot-check page (attempt %d/%d): %s",
                    attempt, max_attempts, url,
                )
            else:
                logger.warning(
                    "Timed out loading product page (attempt %d/%d): %s",
                    attempt, max_attempts, url,
                )

        except WebDriverException as exc:
            logger.warning(
                "Error loading product page (attempt %d/%d) %s: %s",
                attempt, max_attempts, url, exc,
            )

        if attempt < max_attempts:
            backoff_seconds = config.REQUEST_DELAY_SECONDS * attempt
            logger.info("Retrying in %d seconds...", backoff_seconds)
            time.sleep(backoff_seconds)

    logger.error("Giving up on product after %d failed attempts: %s", max_attempts, url)
    return None



# Build a normalized (lowercase, colon-stripped) version of the label
# map once, at import time, so lookups are resilient to label variations
# like "Brand:" vs "Brand" vs "BRAND".
_NORMALIZED_LABEL_MAP = {
    label.lower().rstrip(":").strip(): standardized
    for label, standardized in SPEC_LABEL_MAP.items()
}


def _lookup_label(raw_label: str) -> str | None:
    """Case-insensitive, colon-tolerant lookup into SPEC_LABEL_MAP."""
    key = raw_label.lower().rstrip(":").strip()
    return _NORMALIZED_LABEL_MAP.get(key)


def _parse_spec_table(soup: BeautifulSoup) -> dict:
    """
    Strategy 1: Parse Amazon's structured spec tables.

    Amazon uses SEVERAL different table formats depending on category and
    page template, and these have changed over time:
        (a) "Technical Details" table:  <tr><th>Label</th><td>Value</td></tr>
        (b) "Product overview" table:   <tr><td>Label</td><td>Value</td></tr>
            (id="productOverview_feature_div" -- NO <th> at all, which is
            why an earlier version of this parser missed it completely)
        (c) "Detail bullets" list (handled separately in
            _parse_detail_bullets, since it's <li>, not <table>)

    To stay resilient, we don't just target one known table -- we scan
    EVERY table on the page and treat the first two cells of each row as
    label/value, regardless of whether they're <th> or <td>. Only rows
    whose label matches something in SPEC_LABEL_MAP are kept, so scanning
    broadly doesn't pull in irrelevant tables.

    Returns:
        dict: standardized spec name -> value (only for specs found).
    """
    specs = {}

    # Prioritize known containers first (more trustworthy), then fall
    # back to scanning every table on the page as a safety net.
    priority_selectors = [
        "table#productDetails_techSpec_section_1",
        "table#productDetails_techSpec_section_2",
        "table#productDetails_db_sections",           # "Additional Information" table
        "table.prodDetTable",
        "div#productOverview_feature_div table",       # "Product overview" (no <th>)
        "div#detailBulletsWrapper_feature_div table",   # some variant pages
        "table.a-normal-table",
    ]

    tables_to_scan = []
    for selector in priority_selectors:
        found = soup.select(selector)
        tables_to_scan.extend(found)

    # Fallback: scan literally every table as a last resort, in case
    # Amazon uses a container/class combination we haven't seen yet.
    all_tables = soup.find_all("table")
    for table in all_tables:
        if table not in tables_to_scan:
            tables_to_scan.append(table)

    for table in tables_to_scan:
        rows = table.select("tr")
        for row in rows:
            # Grab ALL cells in the row, whether they're <th> or <td>.
            # This is the key fix: we no longer require a <th> to exist.
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue

            label = cells[0].get_text(strip=True)
            value = cells[1].get_text(strip=True)

            if not label or not value:
                continue

            standardized_key = _lookup_label(label)
            if not standardized_key:
                continue

            # Root-cause fix: never trust "RAM"/"Storage" labels blindly --
            # verify against the actual value text first.
            standardized_key = _classify_memory_row(standardized_key, value)

            if standardized_key not in specs:
                specs[standardized_key] = value
                logger.debug(
                    "Spec table match: '%s' -> %s = '%s'", label, standardized_key, value
                )

    return specs


def _parse_detail_bullets(soup: BeautifulSoup) -> dict:
    """
    Strategy 1b: Some Amazon pages use a "detail bullets" list instead of
    a table, formatted as:
        <li><span class="a-text-bold">Processor: </span><span>Intel i7</span></li>

    Returns:
        dict: standardized spec name -> value.
    """
    specs = {}
    bullets = soup.select("div#detailBullets_feature_div li")

    for bullet in bullets:
        spans = bullet.select("span")
        if len(spans) < 2:
            continue

        label = spans[0].get_text(strip=True)
        value = spans[1].get_text(strip=True)

        standardized_key = _lookup_label(label)
        if not standardized_key:
            continue

        standardized_key = _classify_memory_row(standardized_key, value)

        if standardized_key not in specs:
            specs[standardized_key] = value
            logger.debug(
                "Detail bullet match: '%s' -> %s = '%s'", label, standardized_key, value
            )

    return specs


def _parse_feature_bullets(soup: BeautifulSoup) -> dict:
    """
    Strategy 2 (fallback): Scan the plain-text "About this item" bullet
    list for spec-like keywords using regex. This is less reliable than
    a structured table, but often catches RAM/Storage/CPU info when the
    seller didn't fill out the structured tech-spec table at all.

    Returns:
        dict: standardized spec name -> value (best-effort only).
    """
    specs = {}
    bullet_container = soup.select_one("div#feature-bullets")
    if not bullet_container:
        return specs

    text = bullet_container.get_text(" ", strip=True)

    patterns = {
        # RAM: a GB figure immediately tied to RAM/Memory wording, or an
        # explicit DDR figure (e.g. "16GB DDR5 RAM", "16GB LPDDR5X Memory").
        "RAM": r"(\d{1,3}\s?GB)(?:\s?(?:DDR\d\w?|LPDDR\d\w?))?\s?(?:RAM|Memory)\b",
        # Storage: a GB/TB figure tied to SSD/HDD/NVMe/eMMC wording.
        "Storage Size": r"(\d{1,4}\s?(?:GB|TB))\s?(?:NVMe\s?)?(?:PCIe\s?)?(?:M\.?2\s?)?(?:SSD|HDD|eMMC|Storage)\b",
        "Processor": r"(Intel\s?Core\s?i[3579][\w\-]*|AMD\s?Ryzen\s?[\w\-]*|Apple\s?M[123]\w*)",
        "Display Size": r"(\d{1,2}\.?\d?\s?(?:inch|-inch|\"))",
        "Refresh Rate": r"(\d{2,3}\s?Hz)",
        "Weight": r"(\d{1,2}\.?\d{0,2}\s?(?:lbs|pounds|kg))",
    }

    for spec_name, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            standardized_key = _classify_memory_row(spec_name, value)
            specs.setdefault(standardized_key, value)
            logger.debug(
                "Feature-bullet fallback match: %s = '%s'", standardized_key, value
            )

    return specs


def _parse_review_count(soup: BeautifulSoup) -> dict:
    """
    Extract the customer review count from the PRODUCT DETAIL page.

    Confirmed via direct DevTools inspection of a live Amazon page:
        <span id="acrCustomerReviewText" aria-label="2,287 Reviews"
              class="a-size-small">(2,287)</span>

    This element is Amazon's standard, long-standing review-count widget
    on product detail pages (distinct from the listing-page version
    scraper.py handles) -- so it's a very reliable source. We prefer the
    aria-label (cleaner, no parentheses) and fall back to the visible
    text if aria-label is missing for some reason.

    Returns:
        dict: {"Review Count": "2,287"} if found, otherwise {}.
    """
    specs = {}
    review_span = soup.select_one("#acrCustomerReviewText")

    if review_span:
        source_text = review_span.get("aria-label") or review_span.get_text(strip=True)
        match = re.search(r"[\d,]+", source_text)
        if match:
            specs["Review Count"] = match.group()

    return specs


def _parse_availability(soup: BeautifulSoup) -> dict:
    """
    Extract stock availability text, e.g. "In Stock", "Only 3 left in
    stock", "Currently unavailable". Lives in div#availability on almost
    every Amazon product page, but was never being read before.
    """
    specs = {}
    availability_div = soup.select_one("div#availability span")
    if availability_div:
        text = availability_div.get_text(strip=True)
        if text:
            specs["Availability"] = text
    return specs


def _parse_selected_variant(soup: BeautifulSoup) -> dict:
    """
    Extract the currently-selected Color/Size variant when the product
    page offers a variation swatch selector (div#variation_color_name,
    div#variation_size_name, etc.). Amazon renders the SELECTED option's
    label separately from the swatch buttons themselves, which is why a
    naive scrape of the swatch grid usually returns nothing useful.
    """
    specs = {}
    color_label = soup.select_one(
        "div#variation_color_name span.selection, "
        "div#variation_color_name .a-color-base"
    )
    if color_label:
        text = color_label.get_text(strip=True)
        if text:
            specs["Color"] = text
    return specs


def parse_product_specs(html: str) -> dict:
    """
    Main entry point for this file. Combines all parsing strategies into
    one final, standardized specs dictionary for a single product.

    Args:
        html: full page source of a product detail page.

    Returns:
        dict: keys are standardized spec names (Brand, Model, Processor,
              RAM, Storage Size, GPU, Display Size, Resolution,
              Operating System, Weight, Review Count). Missing
              specs are simply absent from the dict here —
              data_cleaner.py is responsible for filling in
              "Not Available" for any missing keys before export.
    """
    soup = BeautifulSoup(html, "html.parser")

    specs = {}
    # Merge results from all strategies. Earlier strategies take priority
    # (we only fill in a key if it isn't already set) since structured
    # tables are more trustworthy than regex guesses on free text.
    for parser_fn in (
        _parse_spec_table,
        _parse_detail_bullets,
        _parse_feature_bullets,
        _parse_review_count,
        _parse_availability,
        _parse_selected_variant,
    ):
        try:
            result = parser_fn(soup)
            for key, value in result.items():
                specs.setdefault(key, value)
        except Exception as exc:
            logger.warning("Spec parser %s failed: %s", parser_fn.__name__, exc)

    # Debug-level visibility into exactly which fields were / weren't
    # found for this product -- turn on DEBUG logging (see main.py's
    # setup_logging) to see this during troubleshooting runs.
    missing = [c for c in config.FINAL_COLUMNS if c not in specs and c not in
               ("Product Title", "Current Price", "Review Count", "Product URL",
                "Image URL", "Scraped Date", "Rating")]
    logger.debug("Specs found: %s", sorted(specs.keys()))
    logger.debug("Specs still missing after all strategies: %s", missing)

    return specs
