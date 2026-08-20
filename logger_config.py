"""
logger_config.py
-----------------
Centralized logging setup for the whole project.

WHY THIS FILE EXISTS:
Logging setup used to live inline inside main.py's setup_logging().
Now that the app has two entry points that both need logging configured
identically (gui.py and any future CLI script), that setup belongs in
its own reusable module instead of being duplicated or GUI code having
to import main.py just to get logging.

Every other file just does `logger = logging.getLogger(__name__)` and
inherits whatever configuration setup_logging() applied here.
"""

import logging
import os

import config


def setup_logging() -> None:
    """
    Configure logging ONCE, globally, for the whole project.

    Logs go to BOTH the console (so you can watch progress live) and to
    logs/scraper.log (so you have a permanent record after the run ends).
    Respects config.DEBUG_MODE to toggle verbose per-field trace logging
    on/off without touching this file.
    """
    os.makedirs(config.LOGS_DIR, exist_ok=True)

    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    log_level = logging.DEBUG if config.DEBUG_MODE else logging.INFO

    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[
            # mode="w" overwrites scraper.log at the start of every run,
            # so the file only ever holds the MOST RECENT run's logs
            # instead of growing forever across every run ever made.
            # This only affects the log file -- it has no effect on
            # exported CSV/Excel files or the MySQL database, which are
            # written by exporter.py / database.py and are untouched by
            # logging setup.
            logging.FileHandler(config.LOG_FILE, mode="w", encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,  # safe to call setup_logging() more than once (e.g. in tests)
    )