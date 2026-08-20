"""
database.py
------------
All MySQL access for the project lives here, and nowhere else. Scraper,
parser, and cleaner code never imports mysql.connector directly -- they
hand a list of cleaned dicts to insert_products() and this file handles
everything about turning that into rows in a table.

WHY THIS MATTERS (single responsibility):
    - scraper.py / product_parser.py / data_cleaner.py know nothing
      about SQL.
    - If the project later swaps MySQL for PostgreSQL or SQLite, only
      this file needs to change.
    - Every query here is parameterized (%s placeholders) -- no string
      formatting into SQL, which is what prevents SQL injection.

TABLE DESIGN:
    One row per scraped product. `product_url` is UNIQUE, so re-scraping
    the same listing (e.g. a daily price-tracking run) updates that
    product's row instead of creating a duplicate -- see
    _UPSERT_SQL's "ON DUPLICATE KEY UPDATE" clause.
"""

import logging

import mysql.connector
from mysql.connector import Error as MySQLError

import config

logger = logging.getLogger(__name__)

# Maps each config.FINAL_COLUMNS entry (the dict keys used everywhere
# else in the pipeline) to its snake_case MySQL column name. Keeping this
# mapping explicit (rather than auto-lowercasing) makes the DB schema
# readable on its own, independent of how the Python side names things.
COLUMN_DB_MAP = {
    "Product Title": "product_title",
    "Brand": "brand",
    "Model": "model",
    "Model Number": "model_number",
    "Current Price": "price",
    "Rating": "rating",
    "Review Count": "review_count",
    "Processor": "processor",
    "RAM": "ram",
    "Storage Size": "storage_size",
    "GPU": "gpu",
    "Display Size": "display_size",
    "Resolution": "resolution",
    "Refresh Rate": "refresh_rate",
    "Operating System": "operating_system",
    "Weight": "weight",
    "Color": "color",
    "Availability": "availability",
    "Warranty": "warranty",
    "Product URL": "product_url",
    "Image URL": "image_url",
    "Scraped Date": "scraped_at",
}

_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS products (
        id INT AUTO_INCREMENT PRIMARY KEY,
        keyword VARCHAR(255) NOT NULL,
        product_title TEXT,
        brand VARCHAR(255),
        model VARCHAR(255),
        model_number VARCHAR(255),
        price VARCHAR(50),
        rating VARCHAR(10),
        review_count VARCHAR(20),
        processor VARCHAR(255),
        ram VARCHAR(50),
        storage_size VARCHAR(100),
        gpu VARCHAR(255),
        display_size VARCHAR(50),
        resolution VARCHAR(100),
        refresh_rate VARCHAR(20),
        operating_system VARCHAR(100),
        weight VARCHAR(50),
        color VARCHAR(100),
        availability VARCHAR(255),
        warranty VARCHAR(255),
        product_url VARCHAR(512) NOT NULL,
        image_url TEXT,
        page_number INT,
        scraped_at DATETIME,
        UNIQUE KEY uq_product_url (product_url(191))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# ON DUPLICATE KEY UPDATE: if this product_url was already scraped
# before, refresh its data (price/rating/etc. may have changed) instead
# of silently skipping it or raising a duplicate-key error.
_UPSERT_SQL = """
    INSERT INTO products (
        keyword, product_title, brand, model, model_number, price,
        rating, review_count, processor, ram, storage_size, gpu,
        display_size, resolution, refresh_rate, operating_system,
        weight, color, availability, warranty, product_url, image_url,
        page_number, scraped_at
    ) VALUES (
        %(keyword)s, %(product_title)s, %(brand)s, %(model)s,
        %(model_number)s, %(price)s, %(rating)s, %(review_count)s,
        %(processor)s, %(ram)s, %(storage_size)s, %(gpu)s,
        %(display_size)s, %(resolution)s, %(refresh_rate)s,
        %(operating_system)s, %(weight)s, %(color)s, %(availability)s,
        %(warranty)s, %(product_url)s, %(image_url)s, %(page_number)s,
        %(scraped_at)s
    )
    ON DUPLICATE KEY UPDATE
        product_title = VALUES(product_title),
        brand = VALUES(brand),
        model = VALUES(model),
        model_number = VALUES(model_number),
        price = VALUES(price),
        rating = VALUES(rating),
        review_count = VALUES(review_count),
        processor = VALUES(processor),
        ram = VALUES(ram),
        storage_size = VALUES(storage_size),
        gpu = VALUES(gpu),
        display_size = VALUES(display_size),
        resolution = VALUES(resolution),
        refresh_rate = VALUES(refresh_rate),
        operating_system = VALUES(operating_system),
        weight = VALUES(weight),
        color = VALUES(color),
        availability = VALUES(availability),
        warranty = VALUES(warranty),
        image_url = VALUES(image_url),
        page_number = VALUES(page_number),
        scraped_at = VALUES(scraped_at);
"""


# Columns that used to exist in earlier versions of this schema and are
# no longer part of the product data model. Listed here so
# _migrate_schema() can clean up any database that was created before
# this change, without requiring the user to manually run SQL.
_OBSOLETE_COLUMNS = ["battery", "battery_capacity", "memory_type", "discount", "original_price"]

# Every non-PK, non-UNIQUE column that CURRENT_TABLE_SQL expects, mapped
# to its SQL type. _migrate_schema() diffs this against whatever columns
# an EXISTING table actually has and ADD COLUMNs whatever is missing.
# This is what CREATE TABLE IF NOT EXISTS can't do on its own -- that
# statement is a no-op once the table already exists, even if its
# schema is older than what the code now expects. Keep this in sync
# with _CREATE_TABLE_SQL whenever a column is added there.
_EXPECTED_COLUMNS = {
    "keyword": "VARCHAR(255) NOT NULL",
    "product_title": "TEXT",
    "brand": "VARCHAR(255)",
    "model": "VARCHAR(255)",
    "model_number": "VARCHAR(255)",
    "price": "VARCHAR(50)",
    "rating": "VARCHAR(10)",
    "review_count": "VARCHAR(20)",
    "processor": "VARCHAR(255)",
    "ram": "VARCHAR(50)",
    "storage_size": "VARCHAR(100)",
    "gpu": "VARCHAR(255)",
    "display_size": "VARCHAR(50)",
    "resolution": "VARCHAR(100)",
    "refresh_rate": "VARCHAR(20)",
    "operating_system": "VARCHAR(100)",
    "weight": "VARCHAR(50)",
    "color": "VARCHAR(100)",
    "availability": "VARCHAR(255)",
    "warranty": "VARCHAR(255)",
    "product_url": "VARCHAR(512) NOT NULL",
    "image_url": "TEXT",
    "page_number": "INT",
    "scraped_at": "DATETIME",
}


def _get_server_connection():
    """Connect to the MySQL server WITHOUT selecting a database yet --
    needed once, up front, to run CREATE DATABASE IF NOT EXISTS."""
    return mysql.connector.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
    )


def get_connection():
    """
    Open a connection to the project's MySQL database (config.MYSQL_DATABASE).

    Raises:
        mysql.connector.Error: if the server is unreachable or credentials
            are wrong. Callers should catch this and show a friendly
            message rather than letting a raw traceback surface.
    """
    return mysql.connector.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
    )


def _migrate_schema(connection) -> None:
    """
    Bring an EXISTING `products` table (created by an older version of
    this project) up to date with the current schema, without requiring
    the user to run any manual SQL.

    Runs on every startup, right after CREATE TABLE IF NOT EXISTS -- both
    are safe no-ops on a table that's already current, so this never
    slows down or risks a normal run.

    Handles:
        - Adding any column that _EXPECTED_COLUMNS defines but an older
          version of the table doesn't have yet (e.g. a field added to
          the data model after the table was first created -- CREATE
          TABLE IF NOT EXISTS is a no-op on a table that already
          exists, so this is the only thing that catches it).
        - Dropping columns that no longer exist in the data model
          (battery, battery_capacity, memory_type, discount,
          original_price).
        - Renaming the old `storage` column to `storage_size` (from the
          schema version before Storage was split out explicitly).
    """
    cursor = connection.cursor()
    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'products'",
        (config.MYSQL_DATABASE,),
    )
    existing_columns = {row[0] for row in cursor.fetchall()}

    for column, sql_type in _EXPECTED_COLUMNS.items():
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE products ADD COLUMN {column} {sql_type};")
            logger.info("Schema migration: added missing column '%s'.", column)

    for column in _OBSOLETE_COLUMNS:
        if column in existing_columns:
            cursor.execute(f"ALTER TABLE products DROP COLUMN {column};")
            logger.info("Schema migration: dropped obsolete column '%s'.", column)

    if "storage" in existing_columns and "storage_size" not in existing_columns:
        cursor.execute(
            "ALTER TABLE products CHANGE COLUMN storage storage_size VARCHAR(100);"
        )
        logger.info("Schema migration: renamed column 'storage' -> 'storage_size'.")

    connection.commit()
    cursor.close()


def init_database() -> None:
    """
    Create the database and the `products` table if they don't exist yet.

    Safe to call on every app startup -- CREATE DATABASE/TABLE IF NOT
    EXISTS are no-ops when they're already there. This is what makes the
    app "just work" on a fresh MySQL install with zero manual setup.
    """
    connection = _get_server_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS {config.MYSQL_DATABASE} "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        )
        connection.commit()
        cursor.close()
        logger.info("Database '%s' ready.", config.MYSQL_DATABASE)
    finally:
        connection.close()

    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(_CREATE_TABLE_SQL)
        connection.commit()
        cursor.close()
        logger.info("Table 'products' ready.")

        _migrate_schema(connection)
    finally:
        connection.close()


def _record_to_row(record: dict, keyword: str, page_number: int) -> dict:
    """Translate one cleaned pipeline record (English column names) into
    the %(name)s-keyed dict the SQL above expects (snake_case names)."""
    row = {"keyword": keyword, "page_number": page_number}
    for human_name, db_name in COLUMN_DB_MAP.items():
        row[db_name] = record.get(human_name, config.NOT_AVAILABLE)
    return row


def insert_products(records: list[dict], keyword: str, page_number: int = 0) -> int:
    """
    Insert (or update, if product_url already exists) a batch of cleaned
    product records.

    Args:
        records: cleaned, schema-complete product dicts (data_cleaner.py's
            output -- same shape used for CSV/Excel export).
        keyword: the search keyword this batch was scraped under.
        page_number: which search-results page these came from (0 if
            not tracked per-page).

    Returns:
        int: number of rows successfully written. Returns 0 (and logs
            the error) instead of raising, so a DB hiccup never crashes
            the whole scraping run -- callers can still fall back to
            CSV/Excel export.
    """
    if not records:
        return 0

    try:
        connection = get_connection()
    except MySQLError as exc:
        logger.error("Could not connect to MySQL: %s", exc)
        return 0

    written = 0
    try:
        cursor = connection.cursor()
        rows = [_record_to_row(r, keyword, page_number) for r in records]
        cursor.executemany(_UPSERT_SQL, rows)
        connection.commit()
        written = cursor.rowcount
        cursor.close()
        logger.info("Inserted/updated %d product row(s) in MySQL.", len(rows))
    except MySQLError as exc:
        logger.error("MySQL insert failed: %s", exc)
        connection.rollback()
    finally:
        connection.close()

    return written