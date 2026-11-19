"""Entry script frozen into Stravinsky.exe (see stravinsky.spec).

The application is a package (relative imports), so the exe needs a tiny
top-level script that imports it. A windowed exe has no console: output goes
to a log file, and a crash at start-up is shown in a message box instead of
silently closing.
"""
import os
import sys
import traceback
from pathlib import Path

LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Stravinsky"


def _redirect_output() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / "stravinsky.log"
        log = open(log_path, "w", encoding="utf-8", buffering=1)  # noqa: SIM115 -- open for the app's lifetime
        sys.stdout = sys.stderr = log
        return log_path
    except OSError:
        return None


def _show_crash(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Stravinsky", message)
        root.destroy()
    except Exception:  # noqa: BLE001 -- last resort, nothing else to try
        pass


if __name__ == "__main__":
    log_path = _redirect_output()
    try:
        from Stravinsky_QDA.main import main
        main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        where = f"\n\nDetalhes em:\n{log_path}" if log_path else ""
        _show_crash(f"O Stravinsky encontrou um erro e vai fechar.{where}")
        sys.exit(1)
