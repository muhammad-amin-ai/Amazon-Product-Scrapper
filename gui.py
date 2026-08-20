"""
gui.py
------
Desktop interface for the Amazon Product Scraper, built with Tkinter
(Python's built-in GUI toolkit -- no extra install required).

ARCHITECTURE NOTE:
Scraping takes a long time (Selenium page loads, polite delays between
requests). Running it directly on Tkinter's main thread would freeze the
whole window for the entire scrape. So run_pipeline() runs on a
background thread, and communicates back to the UI thread through a
thread-safe queue.Queue -- the UI polls that queue every 100ms via
root.after() and applies whatever updates are waiting. This is the
standard, safe pattern for combining Tkinter with background work;
Tkinter widgets must only ever be touched from the main thread.
"""

import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import logger_config
import pipeline
import database

logger = logging.getLogger(__name__)


def _add_placeholder(entry: ttk.Entry, placeholder: str) -> None:
    """
    Show light-gray hint text in an empty Entry widget (Tkinter has no
    built-in placeholder support). The hint clears itself the moment the
    user clicks in or types, and reappears if they leave it empty.

    IMPORTANT: `_get_real_value()` below is what makes this safe -- it
    checks the widget's placeholder flag before treating the text as
    real input, so the hint itself can never be accidentally submitted
    as if the user had typed "e.g. wireless mouse" as their keyword.
    """
    entry.insert(0, placeholder)
    entry.config(foreground="grey")
    entry.is_placeholder = True  # type: ignore[attr-defined]

    def on_focus_in(_event) -> None:
        if entry.is_placeholder:  # type: ignore[attr-defined]
            entry.delete(0, tk.END)
            entry.config(foreground="black")
            entry.is_placeholder = False  # type: ignore[attr-defined]

    def on_focus_out(_event) -> None:
        if not entry.get().strip():
            entry.insert(0, placeholder)
            entry.config(foreground="grey")
            entry.is_placeholder = True  # type: ignore[attr-defined]

    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)


def _get_real_value(entry: ttk.Entry) -> str:
    """Return the user's actual typed text, or "" if the field still
    shows its placeholder hint (see _add_placeholder)."""
    if getattr(entry, "is_placeholder", False):
        return ""
    return entry.get()


class ScraperApp:
    """Main application window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Amazon Product Scraper")
        self.root.geometry("480x320")
        self.root.resizable(False, False)

        # Thread-safe channel: background thread puts (kind, payload)
        # tuples on here; _poll_queue() (main thread) consumes them.
        self._events: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._worker: threading.Thread | None = None

        self._build_widgets()
        self.root.after(100, self._poll_queue)

    def _build_widgets(self) -> None:
        padding = {"padx": 12, "pady": 6}

        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        ttk.Label(frame, text="Search Product:").grid(row=0, column=0, sticky="w", **padding)
        self.keyword_entry = ttk.Entry(frame, width=32)
        self.keyword_entry.grid(row=0, column=1, **padding)
        _add_placeholder(self.keyword_entry, "e.g. wireless mouse, keyboard, SSD")

        ttk.Label(frame, text="Pages:").grid(row=1, column=0, sticky="w", **padding)
        self.pages_entry = ttk.Entry(frame, width=32)
        self.pages_entry.grid(row=1, column=1, **padding)
        _add_placeholder(self.pages_entry, "e.g. 5")

        self.start_button = ttk.Button(frame, text="Start Scraping", command=self._on_start)
        self.start_button.grid(row=2, column=0, columnspan=2, pady=(10, 6))

        self.progress = ttk.Progressbar(frame, orient="horizontal", length=380, mode="determinate")
        self.progress.grid(row=3, column=0, columnspan=2, pady=6)

        self.status_label = ttk.Label(frame, text="Ready.", wraplength=400, justify="left")
        self.status_label.grid(row=4, column=0, columnspan=2, sticky="w", **padding)

    # -- UI event handlers ------------------------------------------------

    def _on_start(self) -> None:
        keyword_raw = _get_real_value(self.keyword_entry)
        pages_raw = _get_real_value(self.pages_entry)

        try:
            keyword, pages = pipeline.validate_inputs(keyword_raw, pages_raw)
        except pipeline.InvalidInputError as exc:
            messagebox.showerror("Invalid Input", str(exc))
            return

        self.start_button.config(state="disabled")
        self.progress.config(value=0, maximum=100)
        self.status_label.config(text="Starting...")

        self._worker = threading.Thread(
            target=self._run_scrape, args=(keyword, pages), daemon=True
        )
        self._worker.start()

    def _run_scrape(self, keyword: str, pages: int) -> None:
        """Runs on the BACKGROUND thread. Never touch Tkinter widgets
        directly here -- only ever push events onto self._events."""

        def on_progress(current: int, total: int) -> None:
            self._events.put(("progress", (current, total)))

        def on_status(message: str) -> None:
            self._events.put(("status", message))

        try:
            result = pipeline.run_pipeline(
                keyword, pages, progress_callback=on_progress, status_callback=on_status
            )
            self._events.put(("done", result))
        except Exception as exc:
            logger.critical("Scraping run failed: %s", exc, exc_info=True)
            self._events.put(("error", str(exc)))

    # -- Queue polling (MAIN thread only) ---------------------------------

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._poll_queue)

    def _handle_event(self, kind: str, payload: object) -> None:
        if kind == "progress":
            current, total = payload
            self.progress.config(maximum=max(total, 1), value=current)
        elif kind == "status":
            self.status_label.config(text=str(payload))
        elif kind == "done":
            result = payload
            self.start_button.config(state="normal")
            self.progress.config(value=self.progress["maximum"])
            summary = (
                f"Finished! {result['products_found']} product(s) processed, "
                f"{result['products_saved']} saved to database.\n"
                f"CSV: {result['csv_path']}\nExcel: {result['excel_path']}"
            )
            self.status_label.config(text=summary)
            messagebox.showinfo("Scraping Complete", summary)
        elif kind == "error":
            self.start_button.config(state="normal")
            self.status_label.config(text=f"Error: {payload}")
            messagebox.showerror(
                "Scraping Failed",
                f"Something went wrong during scraping:\n\n{payload}",
            )


def launch() -> None:
    """Entry point called by main.py."""
    logger_config.setup_logging()

    root = tk.Tk()
    root.withdraw()  # hide the main window until DB init succeeds

    try:
        database.init_database()
    except Exception as exc:
        logger.critical("Database initialization failed: %s", exc, exc_info=True)
        messagebox.showerror(
            "Database Connection Failed",
            "Could not connect to MySQL. Please check your .env settings "
            f"and make sure MySQL is running.\n\nDetails: {exc}",
        )
        root.destroy()
        return

    root.deiconify()
    ScraperApp(root)
    root.mainloop()
