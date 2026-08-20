"""
pipeline.py
-----------
Orchestration layer: coordinates scraper.py -> product_parser.py ->
data_cleaner.py -> database.py / exporter.py, in order.

This is what used to be main.py's run_pipeline(). It moved here so that
BOTH main.py (which now just launches the GUI) and gui.py (which needs
to trigger a scrape from a button click, on a background thread) can
call the exact same orchestration logic -- no duplication.

This file intentionally contains almost NO scraping/parsing/cleaning
logic itself -- it only coordinates the other modules and makes sure one
broken product never crashes the whole run.
"""

import logging

import config
import scraper
import product_parser
import data_cleaner
import database
import exporter

logger = logging.getLogger(__name__)


class InvalidInputError(ValueError):
    """Raised when the user-supplied keyword/page count fails validation."""


def validate_inputs(keyword: str, pages_text: str) -> tuple[str, int]:
    """
    Validate raw user input (as typed into the GUI or a CLI prompt)
    before any scraping starts.

    Args:
        keyword: the search keyword, as typed (may have extra whitespace).
        pages_text: the page-count field, as typed (still a string --
            the whole point is we haven't trusted it as a number yet).

    Returns:
        tuple[str, int]: the cleaned keyword and validated page count.

    Raises:
        InvalidInputError: with a user-friendly message describing
            exactly what's wrong, for any of: empty keyword, non-numeric
            pages, zero, or negative pages.
    """
    clean_keyword = (keyword or "").strip()
    if not clean_keyword:
        raise InvalidInputError("Search keyword cannot be empty.")

    try:
        pages = int(str(pages_text).strip())
    except (TypeError, ValueError):
        raise InvalidInputError("Number of pages must be a whole number.")

    if pages <= 0:
        raise InvalidInputError("Number of pages must be greater than zero.")

    return clean_keyword, pages


def run_pipeline(
    keyword: str,
    max_pages: int,
    progress_callback=None,
    status_callback=None,
) -> dict:
    """
    Execute the full scrape -> parse -> clean -> store pipeline.

    Args:
        keyword: search keyword (already validated).
        max_pages: number of search-result pages to scrape (already
            validated, > 0).
        progress_callback: optional callable(current: int, total: int)
            invoked as each product's detail page finishes -- lets a GUI
            drive a progress bar without this module knowing a GUI
            exists.
        status_callback: optional callable(message: str) invoked with
            short human-readable status updates ("Scraping page 2 of 5...").

    Returns:
        dict: summary with keys "products_found", "products_saved",
            "csv_path", "excel_path" -- used by the GUI to show a
            completion message.
    """
    def _status(message: str) -> None:
        logger.info(message)
        if status_callback:
            status_callback(message)

    _status(f"Starting scrape for '{keyword}' ({max_pages} page(s))...")

    # -----------------------------------------------------------------
    # STAGE 1: Scrape search-result listings
    # -----------------------------------------------------------------
    listings = scraper.scrape_listings(keyword, max_pages)

    if not listings:
        _status("No listings were found. Nothing to scrape.")
        return {"products_found": 0, "products_saved": 0, "csv_path": None, "excel_path": None}

    _status(f"Found {len(listings)} product(s). Fetching details...")

    if len(listings) > config.MAX_PRODUCTS_TO_SCRAPE:
        logger.warning(
            "Found %d products, capping at MAX_PRODUCTS_TO_SCRAPE=%d.",
            len(listings), config.MAX_PRODUCTS_TO_SCRAPE,
        )
        listings = listings[: config.MAX_PRODUCTS_TO_SCRAPE]

    # -----------------------------------------------------------------
    # STAGE 2: Visit each product page and extract detailed specs
    # -----------------------------------------------------------------
    driver = scraper.create_driver()
    merged_records = []

    try:
        total = len(listings)
        for index, listing in enumerate(listings, start=1):
            product_url = listing.get("Product URL", config.NOT_AVAILABLE)

            try:
                html = product_parser.fetch_product_page(driver, product_url)
                specs = product_parser.parse_product_specs(html) if html else {}
            except Exception as exc:
                # A failure on ONE product must never stop the whole run.
                logger.error("Failed to parse product %s: %s", product_url, exc, exc_info=True)
                specs = {}

            merged_records.append({**listing, **specs})

            if progress_callback:
                progress_callback(index, total)
            _status(f"Processed {index}/{total} products...")
    finally:
        driver.quit()

    # -----------------------------------------------------------------
    # STAGE 3: Clean, normalize, and deduplicate
    # -----------------------------------------------------------------
    cleaned_records = data_cleaner.clean_dataset(merged_records)

    # -----------------------------------------------------------------
    # STAGE 4: Store in MySQL (primary storage) + export CSV/Excel (backup)
    # -----------------------------------------------------------------
    _status("Saving to database...")
    saved_count = database.insert_products(cleaned_records, keyword=keyword)

    _status("Exporting CSV/Excel backup...")
    exporter.export_data(cleaned_records, keyword)

    _status(
        f"Done. {len(cleaned_records)} product(s) processed, "
        f"{saved_count} saved to database."
    )

    return {
        "products_found": len(cleaned_records),
        "products_saved": saved_count,
        "csv_path": config.csv_path_for(keyword),
        "excel_path": config.excel_path_for(keyword),
    }
