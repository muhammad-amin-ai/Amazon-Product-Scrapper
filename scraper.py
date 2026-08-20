"""
scraper.py
----------
Handles all browser automation against Amazon's SEARCH RESULTS pages.

Responsibilities of this file (and ONLY this file):
    1. Launch and configure a Selenium Chrome WebDriver.
    2. Search Amazon for a given keyword.
    3. Walk through pagination (page 1 -> page 2 -> page 3 ...).
    4. Extract "listing-level" data from each results page:
       Title, Price, Rating, Review Count, Product URL, Image URL.

This file does NOT open individual product pages for detailed specs —
that's product_parser.py's job. Keeping these separate means each file
has exactly one reason to change: if Amazon changes the SEARCH page
layout, you edit scraper.py. If they change the PRODUCT page layout,
you edit product_parser.py. They never interfere with each other.

NOTE ON RELIABILITY:
Amazon frequently changes its HTML structure and actively tries to detect
bots. The CSS selectors below reflect a commonly-seen Amazon layout at the
time of writing, but you may need to update them if scraping stops working.
Every extraction call is wrapped in error handling so ONE broken selector
never crashes the whole run -- it just logs a warning and fills in
"Not Available" for that field.
"""

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    NoSuchElementException,
)
from webdriver_manager.chrome import ChromeDriverManager

import config

# Module-level logger. main.py configures logging globally; every other
# file just asks for a logger named after itself so log lines are traceable
# back to the file that produced them.
logger = logging.getLogger(__name__)


def create_driver() -> webdriver.Chrome:
    """
    Build and return a configured Selenium Chrome WebDriver.

    Why a dedicated function instead of inlining this in main.py?
    Because driver setup is a "detail" — main.py should just be able to
    say `driver = create_driver()` without knowing about Chrome options,
    headless flags, or user-agent strings.

    Returns:
        selenium.webdriver.Chrome: a ready-to-use browser instance.
    """
    options = Options()

    # "eager" makes driver.get() return as soon as the DOM is
    # interactive, instead of waiting for every image/ad/tracking
    # script on the page to finish loading (the default "normal"
    # strategy). Amazon's listing pages are heavy with both, so on a
    # slower connection driver.get() itself can blow past
    # PAGE_LOAD_TIMEOUT before the page is even fully rendered -- even
    # though the search-result elements we actually need are already
    # present in the DOM by then. WebDriverWait below still waits for
    # that specific element, so we're not losing any real reliability.
    options.page_load_strategy = "eager"

    if config.HEADLESS:
        # "--headless=new" is the modern headless mode for Chrome.
        options.add_argument("--headless=new")

    # These flags improve stability, especially in containerized/CI
    # environments where a full display server isn't available.
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"user-agent={config.USER_AGENT}")

    # Reduces some (not all) obvious signs that this is an automated browser.
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # webdriver-manager automatically downloads/caches the correct
    # chromedriver binary for the installed Chrome version, so the user
    # never has to manually manage driver executables.
    service = Service(ChromeDriverManager().install())

    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)

    logger.info("Chrome WebDriver created (headless=%s).", config.HEADLESS)
    return driver


def _save_debug_screenshot(driver: webdriver.Chrome, keyword: str) -> None:
    """
    Save a screenshot of whatever the browser is currently showing, so a
    failed run can be diagnosed after the fact (CAPTCHA page, Amazon
    "unusual traffic" block, a genuinely blank/slow page, or a layout
    change breaking our selectors) without having to reproduce the
    failure live with HEADLESS=False.

    Best-effort only: if this fails for any reason, we log it and move
    on rather than letting a debugging aid crash the actual scrape.
    """
    try:
        Path(config.LOGS_DIR).mkdir(parents=True, exist_ok=True)
        safe_keyword = "".join(c if c.isalnum() else "_" for c in keyword)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = Path(config.LOGS_DIR) / f"debug_{safe_keyword}_{timestamp}.png"
        driver.save_screenshot(str(screenshot_path))
        logger.info("Saved debug screenshot to %s", screenshot_path)
    except Exception as exc:  # noqa: BLE001 - debugging aid must never crash the scrape
        logger.warning("Could not save debug screenshot: %s", exc)


def search_amazon(driver: webdriver.Chrome, keyword: str) -> bool:
    """
    Search Amazon for `keyword`, retrying a few times if it fails.

    A single timeout/block is often a one-off -- a slow network blip or
    a transient CAPTCHA -- rather than a permanent block, so retrying
    with a short pause in between catches most of those without any
    manual re-run. Only the LISTING search is retried here (not every
    page or every product detail fetch) since that's the step that
    actually failed in practice.

    Args:
        driver: an active Selenium WebDriver.
        keyword: the search term (e.g. "wireless mouse").

    Returns:
        bool: True if the search page loaded successfully on any
            attempt, False if every attempt failed.
    """
    for attempt in range(1, config.SEARCH_MAX_ATTEMPTS + 1):
        if _search_amazon_once(driver, keyword):
            return True

        if attempt < config.SEARCH_MAX_ATTEMPTS:
            logger.info(
                "Retrying search for '%s' in %d second(s) (attempt %d/%d)...",
                keyword, config.SEARCH_RETRY_DELAY, attempt + 1, config.SEARCH_MAX_ATTEMPTS,
            )
            time.sleep(config.SEARCH_RETRY_DELAY)

    logger.error(
        "Search for '%s' failed after %d attempt(s).",
        keyword, config.SEARCH_MAX_ATTEMPTS,
    )
    return False


def _search_amazon_once(driver: webdriver.Chrome, keyword: str) -> bool:
    """
    Navigate the browser to Amazon's search results for `keyword`.

    Args:
        driver: an active Selenium WebDriver.
        keyword: the search term (e.g. "wireless mouse").

    Returns:
        bool: True if the search page loaded successfully, False otherwise.
    """
    search_url = f"{config.AMAZON_SEARCH_URL}?k={keyword.replace(' ', '+')}"

    try:
        driver.get(search_url)
        # Wait until at least one search-result container is present.
        # This is more reliable than a fixed time.sleep() because it
        # reacts to actual page readiness instead of guessing.
        WebDriverWait(driver, config.ELEMENT_WAIT_TIMEOUT).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'div[data-component-type="s-search-result"]')
            )
        )
        logger.info("Search page loaded for keyword: '%s'", keyword)
        return True

    except TimeoutException:
        logger.error(
            "Timed out waiting for search results for keyword '%s'. "
            "Amazon may have changed its layout, shown a CAPTCHA, or "
            "blocked this request.",
            keyword,
        )
        _save_debug_screenshot(driver, keyword)
        return False

    except WebDriverException as exc:
        logger.error("WebDriver error while searching '%s': %s", keyword, exc)
        return False


def _safe_extract_text(element, selector: str) -> str:
    """
    Small helper: try to find `selector` inside `element` and return its
    stripped text. Returns config.NOT_AVAILABLE if anything goes wrong.

    This helper exists so we don't repeat the same try/except block
    a dozen times inside extract_listing_data().
    """
    try:
        found = element.select_one(selector)
        if found:
            text = found.get_text(strip=True)
            return text if text else config.NOT_AVAILABLE
        return config.NOT_AVAILABLE
    except Exception:
        return config.NOT_AVAILABLE


def extract_listing_data(html: str) -> list[dict]:
    """
    Parse ONE search-results page and extract basic info for every product
    card on it.

    Args:
        html: the full page source of an Amazon search results page.

    Returns:
        list[dict]: one dict per product with keys:
            title, price, rating, review_count, product_url, image_url
    """
    soup = BeautifulSoup(html, "html.parser")
    products = []

    # Each product card on an Amazon search page is a
    # div[data-component-type="s-search-result"].
    result_cards = soup.select('div[data-component-type="s-search-result"]')
    logger.info("Found %d product cards on this page.", len(result_cards))

    for card in result_cards:
        try:
            # --- Title ---
            # Title text lives inside <h2>, regardless of which layout
            # variant wraps it (see Product URL note below for details).
            title = _safe_extract_text(card, "h2 span")
            if title == config.NOT_AVAILABLE:
                title = _safe_extract_text(card, "h2")

            # --- Price (whole + fraction combined, e.g. $699.99) ---
            # IMPORTANT: don't hardcode "$" -- Amazon sometimes localizes
            # the displayed currency (e.g. PKR) based on the shopper's
            # detected region, even on the .com domain. We read whatever
            # currency symbol Amazon actually rendered
            # (span.a-price-symbol), and only fall back to "$" if that
            # symbol span genuinely isn't present.
            price_symbol_tag = card.select_one("span.a-price-symbol")
            price_symbol = price_symbol_tag.get_text(strip=True) if price_symbol_tag else "$"
            # Multi-letter currency codes (PKR, AED, USD...) read better
            # with a space before the amount; single-char symbols don't.
            price_separator = " " if len(price_symbol) > 1 else ""

            price_whole = card.select_one("span.a-price-whole")
            price_fraction = card.select_one("span.a-price-fraction")
            if price_whole:
                price = f"{price_symbol}{price_separator}{price_whole.get_text(strip=True)}"
                if price_fraction:
                    price += f".{price_fraction.get_text(strip=True)}"
            else:
                # Fallback: Amazon often hides the FULL price string (for
                # screen readers) in span.a-price > span.a-offscreen, even
                # on cards that don't split it into separate whole/fraction
                # spans (common for "multiple options" / variant listings).
                offscreen_price = card.select_one("span.a-price span.a-offscreen")
                price = offscreen_price.get_text(strip=True) if offscreen_price else config.NOT_AVAILABLE

            # --- Rating (e.g. "4.5 out of 5 stars") ---
            rating = _safe_extract_text(card, "span.a-icon-alt")

            # --- Review count ---
            # Confirmed from direct inspection of Amazon's real markup:
            # #acrCustomerReviewText  e.g. <span id="acrCustomerReviewText"
            # aria-label="2,287 Reviews" class="a-size-small">(2,287)</span>
            # We try THIS exact, verified selector FIRST, then fall back to
            # more generic strategies for cases where this specific ID
            # isn't present on a given card.
            review_count = config.NOT_AVAILABLE

            # Strategy A (verified): the exact element confirmed via
            # DevTools inspection on the live site.
            acr_span = card.select_one("#acrCustomerReviewText")
            if acr_span:
                digits_match = re.search(
                    r"[\d,]+", acr_span.get("aria-label", "") or acr_span.get_text(strip=True)
                )
                if digits_match:
                    review_count = digits_match.group()

            # Strategy B: the visible review-count link itself
            # (<a href="#customerReviews">1,532</a>).
            if review_count == config.NOT_AVAILABLE:
                review_link = card.select_one('a[href*="customerReviews"]')
                if review_link:
                    digits_match = re.search(r"[\d,]+", review_link.get_text(strip=True))
                    if digits_match:
                        review_count = digits_match.group()

            # Strategy C: an aria-label whose value CONTAINS a number,
            # e.g. aria-label="1,532" or aria-label="1,532 ratings".
            # We use re.search (not fullmatch) so trailing words like
            # "ratings"/"Reviews" don't cause a miss, and we explicitly
            # skip any aria-label that looks like the star-rating text
            # ("4.5 out of 5 stars") so the two never get confused.
            if review_count == config.NOT_AVAILABLE:
                for span in card.select("span[aria-label]"):
                    aria_value = (span.get("aria-label") or "").strip()
                    if not aria_value or "out of" in aria_value.lower():
                        continue
                    digits_match = re.search(r"[\d,]+", aria_value)
                    if digits_match:
                        review_count = digits_match.group()
                        break

            # Strategy D: fallback -- a bare a-size-base span sitting next
            # to the rating icon, in case none of the above matched.
            if review_count == config.NOT_AVAILABLE:
                candidate = card.select_one("span.a-size-base.s-underline-text")
                if candidate:
                    digits_match = re.search(r"[\d,]+", candidate.get_text(strip=True))
                    if digits_match:
                        review_count = digits_match.group()

            # --- Product URL ---
            # Amazon has used TWO different layouts over time:
            #   Older:  <h2> <a href="...">Title</a> </h2>
            #   Newer:  <a href="..."> <h2>Title</h2> </a>
            # We try both, plus a generic fallback, so this keeps working
            # even if Amazon flips the nesting again in the future.
            h2_tag = card.select_one("h2")
            link_tag = None

            if h2_tag:
                # Newer layout: the <a> is a PARENT of the <h2>.
                link_tag = h2_tag.find_parent("a")
                if not link_tag:
                    # Older layout: the <a> is a CHILD inside the <h2>.
                    link_tag = h2_tag.find("a")

            if not link_tag:
                # Last-resort fallback: any title-recipe container with a link.
                link_tag = card.select_one('div[data-cy="title-recipe"] a[href]')

            if link_tag and link_tag.get("href"):
                product_url = urljoin(config.AMAZON_BASE_URL, link_tag["href"])
            else:
                product_url = config.NOT_AVAILABLE

            # --- Image URL ---
            img_tag = card.select_one("img.s-image")
            image_url = img_tag["src"] if img_tag and img_tag.get("src") else config.NOT_AVAILABLE

            products.append(
                {
                    "Product Title": title,
                    "Current Price": price,
                    "Rating": rating,
                    "Review Count": review_count,
                    "Product URL": product_url,
                    "Image URL": image_url,
                }
            )

        except Exception as exc:
            # A single broken card should never stop the whole page from
            # being processed. Log it and move to the next card.
            logger.warning("Skipped one product card due to error: %s", exc)
            continue

    return products


def go_to_next_page(driver: webdriver.Chrome) -> bool:
    """
    Click Amazon's "Next" pagination button, if it exists and is enabled.

    Returns:
        bool: True if navigation to the next page succeeded, False if
              there is no next page (end of pagination) or it failed.
    """
    try:
        next_button = driver.find_element(
            By.CSS_SELECTOR, "a.s-pagination-next"
        )

        # Amazon disables the "Next" button on the last page by adding
        # the class "s-pagination-disabled" instead of removing the link.
        classes = next_button.get_attribute("class") or ""
        if "s-pagination-disabled" in classes:
            logger.info("Reached the last page of search results.")
            return False

        next_button.click()

        WebDriverWait(driver, config.ELEMENT_WAIT_TIMEOUT).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'div[data-component-type="s-search-result"]')
            )
        )
        return True

    except NoSuchElementException:
        logger.info("No 'Next' button found — assuming last page.")
        return False
    except TimeoutException:
        logger.error("Timed out loading the next page.")
        return False
    except WebDriverException as exc:
        logger.error("Error navigating to next page: %s", exc)
        return False


def scrape_listings(keyword: str, max_pages: int) -> list[dict]:
    """
    Full orchestration for the "listing" scraping stage:
    search -> extract page 1 -> go to page 2 -> extract -> ... up to max_pages.

    This is the ONE function main.py needs to call from this file.

    Args:
        keyword: search term to look up on Amazon.
        max_pages: maximum number of result pages to walk through.

    Returns:
        list[dict]: combined basic listing data from all scraped pages.
    """
    all_products = []
    driver = create_driver()

    try:
        if not search_amazon(driver, keyword):
            logger.error("Aborting scrape: initial search failed.")
            return all_products

        for page_num in range(1, max_pages + 1):
            logger.info("Scraping page %d of max %d...", page_num, max_pages)

            page_products = extract_listing_data(driver.page_source)
            all_products.extend(page_products)
            logger.info(
                "Page %d complete. %d products extracted so far (total).",
                page_num,
                len(all_products),
            )

            # Be polite: pause briefly before the next request/page load.
            time.sleep(config.REQUEST_DELAY_SECONDS)

            if page_num < max_pages:
                moved = go_to_next_page(driver)
                if not moved:
                    break  # No more pages available.

    finally:
        # Always close the browser, even if something above raised an
        # exception. Leaving Chrome processes running is a common source
        # of memory leaks in scraping scripts.
        driver.quit()
        logger.info("Browser closed.")

    return all_products