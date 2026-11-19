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


def _self_test() -> None:
    """Build the splash and the main window (every tab), then close: run by
    the build (Stravinsky.exe --self-test) so a release that can't start is
    never published. Exit code 0 = OK."""
    from Stravinsky_QDA.ui.scaling import enable_dpi_awareness
    enable_dpi_awareness()
    from Stravinsky_QDA.ui.splash import Splash
    splash = Splash()
    splash.update()
    splash.destroy()
    from Stravinsky_QDA.ui.app import QDAApp
    app = QDAApp()
    for tab in app.main_tabs.tabs():
        app.main_tabs.select(tab)
        app.update()
    app.destroy()  # not app.close(): leave the user's saved layout alone
    print("self-test OK")


if __name__ == "__main__":
    log_path = _redirect_output()
    try:
        if "--self-test" in sys.argv:
            _self_test()
            sys.exit(0)
        from Stravinsky_QDA.main import main
        main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        if "--self-test" in sys.argv:
            sys.exit(1)
        where = f"\n\nDetalhes em:\n{log_path}" if log_path else ""
        _show_crash(f"O Stravinsky encontrou um erro e vai fechar.{where}")
        sys.exit(1)
