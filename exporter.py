"""
exporter.py
-----------
Final stage of the pipeline: takes fully-cleaned product records and
writes them to disk as a CSV file and an Excel (.xlsx) file.

This is the ONLY file that should import pandas/openpyxl for writing
output. If a future version of this project adds a database or an API,
that would live in a NEW file (e.g. db_exporter.py) sitting next to this
one -- main.py would just call whichever exporter(s) it needs. This file
never has to change for that upgrade to happen.
"""

import logging

import pandas as pd
from openpyxl.utils import get_column_letter

import config

logger = logging.getLogger(__name__)


def _to_dataframe(records: list[dict]) -> pd.DataFrame:
    """
    Convert a list of cleaned product dicts into a pandas DataFrame with
    columns in the exact order defined by config.FINAL_COLUMNS.

    Enforcing column order here (rather than trusting dict insertion
    order) guarantees the exported file always looks the same, even if
    upstream code changes the order it builds dicts in.
    """
    df = pd.DataFrame(records)

    # Make sure every expected column exists, even if `records` was empty
    # or a column was somehow missing entirely (defensive programming).
    for column in config.FINAL_COLUMNS:
        if column not in df.columns:
            df[column] = config.NOT_AVAILABLE

    return df[config.FINAL_COLUMNS]


def export_to_csv(records: list[dict], keyword: str, output_path: str = None) -> str:
    """
    Export cleaned records to a CSV file.

    Args:
        records: list of cleaned, schema-complete product dicts.
        keyword: the search keyword this batch was scraped under --
            used to build the default filename (e.g. "wireless mouse"
            -> data/wireless_mouse_products.csv). Never hardcoded.
        output_path: destination path (defaults to
            config.csv_path_for(keyword)).

    Returns:
        str: the path the file was written to.
    """
    output_path = output_path or config.csv_path_for(keyword)
    df = _to_dataframe(records)

    try:
        # utf-8-sig ensures Excel/Google Sheets correctly display special
        # characters (accents, currency symbols) instead of showing garbled text.
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
        logger.info("CSV export complete: %s (%d rows).", output_path, len(df))
        return output_path
    except OSError as exc:
        logger.error("Failed to write CSV to %s: %s", output_path, exc)
        raise


def export_to_excel(records: list[dict], keyword: str, output_path: str = None) -> str:
    """
    Export cleaned records to a formatted Excel (.xlsx) file, with
    column widths auto-adjusted to fit their content for readability.

    Args:
        records: list of cleaned, schema-complete product dicts.
        keyword: the search keyword this batch was scraped under --
            used to build the default filename. Never hardcoded.
        output_path: destination path (defaults to
            config.excel_path_for(keyword)).

    Returns:
        str: the path the file was written to.
    """
    output_path = output_path or config.excel_path_for(keyword)
    df = _to_dataframe(records)

    try:
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Products")

            worksheet = writer.sheets["Products"]

            # Auto-fit each column's width based on its longest value,
            # capped so a huge URL doesn't blow the sheet out to 300
            # characters wide.
            for i, column in enumerate(df.columns, start=1):
                max_content_length = df[column].astype(str).map(len).max()
                header_length = len(str(column))
                best_width = max(max_content_length, header_length) + 2
                worksheet.column_dimensions[get_column_letter(i)].width = min(best_width, 60)

            # Freeze the header row so it stays visible while scrolling
            # through a long product list -- a small touch that makes the
            # file noticeably nicer to work with in Excel/Google Sheets.
            worksheet.freeze_panes = "A2"

        logger.info("Excel export complete: %s (%d rows).", output_path, len(df))
        return output_path

    except OSError as exc:
        logger.error("Failed to write Excel file to %s: %s", output_path, exc)
        raise


def export_data(records: list[dict], keyword: str) -> None:
    """
    Convenience wrapper: exports the same cleaned data to BOTH CSV and
    Excel in one call. This is the function pipeline.py actually calls.

    Args:
        records: list of cleaned, schema-complete product dicts.
        keyword: the search keyword this batch was scraped under --
            used to build both output filenames dynamically.
    """
    if not records:
        logger.warning("No records to export. Skipping CSV/Excel export.")
        return

    export_to_csv(records, keyword)
    export_to_excel(records, keyword)
    logger.info("Export finished. %d product record(s) saved.", len(records))
