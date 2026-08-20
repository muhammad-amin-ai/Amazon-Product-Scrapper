"""
config.py
---------
Central configuration file for the Amazon Product Scraper.

WHY THIS FILE EXISTS:
Instead of scattering "magic values" (like a search keyword, a page limit,
or a file path) throughout the codebase, we keep them all here. This is a
core software engineering principle: separate SETTINGS from LOGIC.

Benefits:
- Want to scrape a different keyword? Change ONE line here.
- Want to scrape 10 pages instead of 5? Change ONE line here.
- No need to hunt through scraper.py or main.py to find hardcoded values.

Nothing in this file performs any scraping logic. It only defines values.
"""

import os
import re

from dotenv import load_dotenv

# Load variables from a .env file in the project root (if present) into
# the process environment. Safe to call even if .env doesn't exist --
# os.getenv() below just falls back to its default in that case.
load_dotenv()

# ---------------------------------------------------------------------------
# SEARCH SETTINGS
# ---------------------------------------------------------------------------
# NOTE: There is intentionally NO hardcoded search keyword or page count
# here. Both are supplied at runtime by whoever calls pipeline.run_pipeline()
# (the GUI, today) -- see pipeline.validate_inputs(). This is what makes the
# scraper work for ANY Amazon product ("laptop", "wireless mouse", "SSD",
# ...) instead of being tied to one category.

# Amazon's base domain. Kept separate so it's easy to switch marketplaces
# (amazon.com, amazon.co.uk, amazon.in, etc.) without touching scraper.py.
AMAZON_BASE_URL = "https://www.amazon.com"
AMAZON_SEARCH_URL = f"{AMAZON_BASE_URL}/s"

# ---------------------------------------------------------------------------
# BROWSER / SELENIUM SETTINGS
# ---------------------------------------------------------------------------

# Run Chrome without a visible window. Set to False while debugging so you
# can SEE what the browser is doing (very useful when Amazon changes layout).
HEADLESS = False

# How long (seconds) Selenium waits for a page to finish loading before
# giving up. Prevents the scraper from hanging forever on a slow/broken page.
# Paired with page_load_strategy="eager" in scraper.create_driver(), which
# already stops waiting once the DOM is interactive rather than waiting for
# every image/ad/script -- this is just a safety margin on top of that.
PAGE_LOAD_TIMEOUT = 45

# How long (seconds) Selenium waits for a specific element to appear before
# deciding it's genuinely missing (used with WebDriverWait).
ELEMENT_WAIT_TIMEOUT = 10

# How many times to attempt the initial Amazon search before giving up
# (1 = no retry, just the original attempt). A timeout/block on this
# step is often a one-off network blip or transient CAPTCHA, so retrying
# a couple of times catches most of those automatically.
SEARCH_MAX_ATTEMPTS = 3

# How long (seconds) to wait between search retries. Retrying instantly
# tends to hit the same block/slowdown again -- a short pause gives it
# a chance to clear.
SEARCH_RETRY_DELAY = 5

# Polite delay (seconds) between requests/page loads. Scraping too fast is
# the #1 reason sites block you. This also just reduces load on the target
# site, which is good etiquette.
REQUEST_DELAY_SECONDS = 2

# A realistic desktop User-Agent string. Many sites block requests that
# look like they're coming from a bot (e.g. Selenium's default UA string).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# FILE PATHS
# ---------------------------------------------------------------------------

# Base directory of the project (folder this config.py lives in).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

LOG_FILE = os.path.join(LOGS_DIR, "scraper.log")

# Create these folders automatically so a fresh `git clone` + run doesn't
# crash on "no such file or directory" the first time logging or export
# tries to write into them.
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)


def _slugify(keyword: str) -> str:
    """Turn a free-text search keyword into a filesystem-safe slug.

    Examples:
        "wireless mouse"  -> "wireless_mouse"
        "SSD 1TB!"        -> "ssd_1tb"
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", keyword.strip().lower()).strip("_")
    return slug or "product"


def csv_path_for(keyword: str) -> str:
    """Build the CSV output path for a given search keyword, e.g.
    "wireless mouse" -> data/wireless_mouse_products.csv. Filenames are
    always derived from what the user actually searched for -- never a
    hardcoded product name -- so the scraper works for any category."""
    return os.path.join(DATA_DIR, f"{_slugify(keyword)}_products.csv")


def excel_path_for(keyword: str) -> str:
    """Excel equivalent of csv_path_for() -- see that docstring."""
    return os.path.join(DATA_DIR, f"{_slugify(keyword)}_products.xlsx")

# ---------------------------------------------------------------------------
# DATA SCHEMA
# ---------------------------------------------------------------------------

# The exact, final column order for the exported CSV/Excel file.
# Both data_cleaner.py and exporter.py rely on this list, so every record
# always has the same shape and column order — no surprises in the output.
FINAL_COLUMNS = [
    "Product Title",
    "Brand",
    "Model",
    "Model Number",
    "Current Price",
    "Rating",
    "Review Count",
    "Processor",
    "RAM",
    "Storage Size",
    "GPU",
    "Display Size",
    "Resolution",
    "Refresh Rate",
    "Operating System",
    "Weight",
    "Color",
    "Availability",
    "Warranty",
    "Product URL",
    "Image URL",
    "Scraped Date",
]

# Value used whenever a field could not be found on the page.
# Keeping this as a constant means every file uses the exact same string
# (no risk of "Not Available" vs "N/A" vs "not available" inconsistencies).
NOT_AVAILABLE = "Not Available"

# ---------------------------------------------------------------------------
# MYSQL DATABASE SETTINGS
# ---------------------------------------------------------------------------

# All values are read from a local .env file (see .env.example) so real
# credentials never get committed to GitHub. Defaults below only cover
# non-secret values (host/port/db name) -- MYSQL_PASSWORD has no default
# on purpose, so a missing .env fails loudly instead of silently trying
# an empty password.
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "amazon_scraper")

# ---------------------------------------------------------------------------
# DEBUGGING
# ---------------------------------------------------------------------------

# When True, main.py sets the root log level to DEBUG instead of INFO,
# which surfaces the per-field "Specs found / Specs still missing" trace
# lines added throughout product_parser.py. Turn this on temporarily
# whenever you need to see exactly WHERE a field is being lost for a
# specific product. Leave False for normal runs -- DEBUG logging is
# verbose and will make logs/scraper.log large very quickly.
DEBUG_MODE = False

# ---------------------------------------------------------------------------
# SAFETY LIMITS
# ---------------------------------------------------------------------------

# Maximum number of product detail pages to visit in one run. This is a
# safety net independent of MAX_PAGES, useful while testing so you don't
# accidentally trigger 200 page loads on a typo.
MAX_PRODUCTS_TO_SCRAPE = 200