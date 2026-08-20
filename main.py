"""
main.py
-------
Entry point of the Amazon Product Scraper desktop application.

All of this file's previous responsibility (scrape -> parse -> clean ->
export orchestration) now lives in pipeline.py, and logging setup now
lives in logger_config.py. This file's only job is to launch the GUI.

Usage:
    python main.py
"""

import gui


if __name__ == "__main__":
    gui.launch()
