# Amazon Product Scraper

A desktop application that scrapes **any product category** from Amazon
(laptops, keyboards, mice, monitors, mobiles, headphones, and more),
extracts detailed specifications from each product's page, cleans and
normalizes the data, and stores it in a MySQL database (with CSV/Excel
export as a backup) — all driven from a simple Tkinter GUI.

## Features

- **Desktop GUI** — enter any search keyword and page count, click
  Start, watch a live progress bar and status messages. No product is
  hardcoded anywhere — search "laptop" today, "wireless mouse" tomorrow.
- **Two-stage scraping** — search-results listings first, then a full
  visit to each product's detail page for specs (RAM, storage,
  processor, GPU, and more).
- **Robust spec extraction** — scans multiple page sections (spec
  tables, "Additional Information", detail bullets, "About this item")
  with fallback strategies, so real listings return far fewer
  "Not Available" fields than a single-selector scraper would.
- **RAM vs. Storage safeguard** — Amazon sellers label these
  inconsistently across categories; the parser double-checks each
  value's actual text (looking for `DDR4/DDR5/LPDDR...` vs.
  `SSD/HDD/NVMe/eMMC`) before assigning it to a column, instead of
  trusting the label alone.
- **Single, accurate price field** — "Current Price" always holds the
  actual amount the customer pays right now, regardless of whether the
  listing shows a discount.
- **MySQL storage** — database and table are created automatically on
  first run (and an existing older table is auto-migrated to the
  current schema); every row is tagged with the `keyword` it was
  scraped under, so one table cleanly holds multiple product
  categories. Re-scraping a product updates its existing row instead
  of creating a duplicate (`ON DUPLICATE KEY UPDATE` on `product_url`).
- **Dynamic CSV / Excel export** — every run writes a backup file named
  after what you searched for, e.g. `wireless_mouse_products.csv`,
  `monitor_products.xlsx` — never a hardcoded filename.
- **Resilient by design** — a failure on one product (timeout, CAPTCHA,
  missing element) is logged and skipped; it never crashes the run.
- **Logging** — every run logs to both the console and
  `logs/scraper.log`, including an end-of-run "field completeness"
  report showing what percentage of records have a real value for each
  column.

## Technologies Used

- **Python 3.10+**
- **Selenium** — browser automation
- **BeautifulSoup4 + lxml** — HTML parsing
- **MySQL** (`mysql-connector-python`) — persistent storage
- **Tkinter** — desktop GUI (Python standard library)
- **pandas + openpyxl** — CSV/Excel export
- **python-dotenv** — environment-based configuration

## Folder Structure

```
amazon_scraper/
├── main.py              # Entry point — launches the GUI
├── gui.py                # Tkinter desktop interface
├── pipeline.py            # Orchestrates scrape -> parse -> clean -> store
├── scraper.py              # Search-results (listing) page scraping
├── product_parser.py        # Product detail page parsing (specs)
├── data_cleaner.py           # Pure normalization/cleaning functions
├── database.py                # MySQL connection, schema, upserts
├── exporter.py                 # CSV/Excel export
├── config.py                    # Central settings (incl. .env loading)
├── logger_config.py              # Centralized logging setup
├── requirements.txt
├── .env.example                  # Template for your local .env
├── .gitignore
├── data/                          # CSV/Excel output (git-ignored)
└── logs/                          # Log files (git-ignored)
```

## Installation

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd amazon_scraper
   ```

2. **Create a virtual environment** (recommended)
   ```bash
   python3 -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

   > **Linux users:** Tkinter ships with Python on Windows/macOS, but on
   > some Linux distros it's a separate system package:
   > `sudo apt-get install python3-tk`

4. **Chrome browser** must be installed — `webdriver-manager` downloads
   the matching ChromeDriver automatically, no manual setup needed.

## MySQL Setup

1. Install and start MySQL Server locally (or point at a remote instance).
2. Copy the environment template and fill in your credentials:
   ```bash
   cp .env.example .env
   ```
   ```env
   MYSQL_HOST=localhost
   MYSQL_PORT=3306
   MYSQL_USER=root
   MYSQL_PASSWORD=your_mysql_password_here
   MYSQL_DATABASE=amazon_scraper
   ```
3. That's it — the app creates the `amazon_scraper` database and the
   `products` table automatically on first launch. If you have an
   existing table from an older version of this project, it is
   automatically migrated to the current schema on startup as well.
   No manual SQL needed either way.

## How to Run

```bash
python main.py
```

This opens the desktop window:

1. Type **any** search keyword — laptop, wireless mouse, mechanical
   keyboard, monitor, SSD, mobile, headphones, printer, whatever you
   want to scrape
2. Enter the number of search-result pages to scrape, e.g. `5`
3. Click **Start Scraping**
4. Watch the progress bar and status line update live
5. On completion, you'll see a summary with the count of products saved
   and the paths to the CSV/Excel backup files (named after your
   keyword, e.g. `data/monitor_products.csv`)

Invalid input (empty keyword, zero/negative/non-numeric page count) is
rejected with a clear error message before any scraping starts. Both
fields show gray hint text until you click in and type — that hint
text is never treated as real input.

## Example Usage

```
Search Product: wireless mouse
Pages: 3

Status: Found 54 product(s). Fetching details...
Status: Processed 54/54 products...
Status: Saving to database...
Status: Done. 51 product(s) processed, 51 saved to database.
```

Output files: `data/wireless_mouse_products.csv`,
`data/wireless_mouse_products.xlsx`

Query the results directly in MySQL — every row is tagged with the
`keyword` it was scraped under, so multiple categories coexist cleanly
in the same table:

```sql
SELECT product_title, brand, ram, storage_size, price
FROM amazon_scraper.products
WHERE keyword = 'wireless mouse'
ORDER BY scraped_at DESC;
```

## Data Fields

| Field | Notes |
|---|---|
| Product Title, Brand, Model, Model Number | |
| Current Price | actual selling price, discount or not |
| Rating, Review Count | |
| Processor, RAM, Storage Size, GPU | Storage Size always includes capacity + type when available, e.g. `512GB SSD` |
| Display Size, Resolution, Refresh Rate | |
| Operating System, Weight, Color | |
| Availability, Warranty | |
| Product URL, Image URL, Scraped Date | |

Fields that can't be reliably found are stored as `Not Available` —
the scraper never guesses or inserts inaccurate data just to fill a
column.

## Future Improvements

- Scheduled/automated daily runs for price-history tracking
- A results table view inside the GUI (not just a completion summary)
- Export filters (e.g. only products under a given price)
- Multi-keyword batch scraping in one run
- Dockerized MySQL + app for one-command setup
