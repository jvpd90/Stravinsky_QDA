"""Crisp rendering on scaled (high-DPI) displays.

Without DPI awareness, Windows renders the app at 96 DPI and stretches the
bitmap, which makes all text blurry on displays set to 125%, 150%, ... With
it, Tk draws at the real resolution: fonts (given in points) scale on their
own, but sizes given in pixels do not -- wrap them in ``px()``, which
converts a size designed at 100% into the current scale.

``enable_dpi_awareness()`` must run before the first Tk window is created.
"""
from __future__ import annotations

import sys

_scale: float | None = None


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system-DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # Windows 7/8 fallback
        except Exception:
            pass


def scale() -> float:
    """Display scale factor (1.0 at 100%, 1.25 at 125%, ...).

    Stays 1.0 when the process isn't DPI aware, because Windows then reports
    96 DPI -- so ``px()`` is correct either way.
    """
    global _scale
    if _scale is None:
        _scale = 1.0
        if sys.platform == "win32":
            try:
                import ctypes
                _scale = ctypes.windll.user32.GetDpiForSystem() / 96
            except Exception:
                pass
    return _scale


def px(value: float) -> int:
    """A pixel size designed at 100% scale, converted to the current scale."""
    return int(round(value * scale()))
