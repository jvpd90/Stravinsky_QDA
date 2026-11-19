"""Entry point. Run with: python -m Stravinsky_QDA.main"""
import threading
import time

from .ui.scaling import enable_dpi_awareness

enable_dpi_awareness()  # before any window exists, so Tk renders crisply

from .ui.splash import Splash  # noqa: E402

MIN_SPLASH_SECONDS = 10 # keep the splash visible at least this long


def main():
    splash = Splash()
    started = time.monotonic()
    loaded: dict = {"status": "Loading modules…"}

    def load():
        # The heavy imports (pandas, scipy, matplotlib, ...) happen here, off the
        # Tk thread, so the splash keeps animating. No widgets are created here.
        # Progress is reported through loaded["status"]; the Tk thread displays it.
        try:
            import pandas, scipy, matplotlib  # noqa: F401  (warm the slow imports)
            loaded["status"] = "Preparing workspace…"
            from .ui.app import QDAApp
            loaded["app_class"] = QDAApp
            loaded["status"] = "Ready"
        except BaseException as exc:  # re-raised on the main thread below
            loaded["error"] = exc

    worker = threading.Thread(target=load, daemon=True)
    worker.start()

    def wait_for_load():
        splash.set_status(loaded["status"])
        if worker.is_alive() or time.monotonic() - started < MIN_SPLASH_SECONDS:
            splash.after(50, wait_for_load)
        else:
            splash.quit()

    splash.after(50, wait_for_load)
    splash.mainloop()
    splash.close()

    if "error" in loaded:
        raise loaded["error"]
    loaded["app_class"]().mainloop()


if __name__ == "__main__":
    main()
