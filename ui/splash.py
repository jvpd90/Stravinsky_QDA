"""Startup splash screen: light card with the picture on the left, the app name,
a status line and an animated progress bar on the right.

Deliberately imports nothing heavy so it can appear instantly, before the main
UI (pandas, scipy, matplotlib, ...) is imported.

To use your own picture, drop a PNG at ``assets/splash.png`` (next to ``help/``).
It is scaled down to fit; without it a simple drawn logo is shown instead.
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

from .scaling import px

APP_NAME = "Stravinsky"
APP_SUBTITLE = "Qualitative Data Analysis"

# Sizes are designed at 100% display scale; px() adapts them to the current one.
WIDTH, HEIGHT = px(520), px(300)
IMAGE_MAX = px(210)  # max width/height of the picture, in pixels

# Palette: neutral light card, blue accent (same as ui/theme.py).
BG_TOP = "#fbfbfd"
BG_BOTTOM = "#f1f3f8"
BORDER = "#e5e7eb"
FG = "#111827"
MUTED = "#6b7280"
SUBTLE = "#9ca3af"
ACCENT = "#3b74d9"
TRACK = "#eceef3"

FONT = "Segoe UI"
FONT_BOLD = "Segoe UI Semibold"

# Layout
IMAGE_CX = px(130)
TEXT_X = px(248)
BAR_X0, BAR_X1, BAR_Y = TEXT_X, WIDTH - px(50), px(196)
BAR_SEGMENT = px(80)        # length of the moving part of the bar
BAR_PERIOD = 1.6        # seconds per sweep
FADE_SECONDS = 0.2


def _assets_dir() -> Path:
    # Inside a PyInstaller bundle, data files live under sys._MEIPASS.
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates = [Path(base) / "assets", Path(base) / "QDA_Claude" / "assets"]
    else:
        candidates = []
    candidates.append(Path(__file__).resolve().parent.parent / "assets")
    for path in candidates:
        if path.is_dir():
            return path
    return candidates[-1]


SPLASH_IMAGE = _assets_dir() / "splash.png"
ICON_ICO = _assets_dir() / "icon.ico"
ICON_PNG = _assets_dir() / "icon.png"


def apply_window_icon(root: tk.Tk) -> None:
    """Set the title-bar/taskbar icon on ``root`` and, by default, all its dialogs."""
    try:
        if sys.platform == "win32" and ICON_ICO.is_file():
            root.iconbitmap(default=str(ICON_ICO))
        elif ICON_PNG.is_file():
            root._icon_image = tk.PhotoImage(master=root, file=str(ICON_PNG))
            root.iconphoto(True, root._icon_image)
    except tk.TclError:
        pass


def _mix(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _ease_in_out(t: float) -> float:
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


class Splash(tk.Tk):
    """Borderless, centred window shown while the application loads."""

    def __init__(self):
        super().__init__()
        apply_window_icon(self)  # taskbar entry while loading
        self.overrideredirect(True)  # no title bar / borders
        self.attributes("-topmost", True)
        self._set_alpha(0.0)
        x = (self.winfo_screenwidth() - WIDTH) // 2
        y = (self.winfo_screenheight() - HEIGHT) // 2
        self.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        self.configure(bg=BG_TOP)

        native_frame = self._apply_native_frame()
        self.canvas = tk.Canvas(self, width=WIDTH, height=HEIGHT, bg=BG_TOP,
                                highlightthickness=0 if native_frame else 1,
                                highlightbackground=BORDER)
        self.canvas.pack(fill="both", expand=True)
        c = self.canvas

        self._draw_background()

        self._image = self._load_image()
        if self._image is not None:
            c.create_image(IMAGE_CX, HEIGHT // 2, image=self._image)
        else:
            self._draw_default_logo(IMAGE_CX, HEIGHT // 2)

        c.create_text(TEXT_X, px(112), text=APP_NAME, anchor="w", fill=FG,
                      font=(FONT_BOLD, 24))
        c.create_text(TEXT_X + px(1), px(148), text=APP_SUBTITLE, anchor="w", fill=MUTED,
                      font=(FONT, 11))

        # Indeterminate progress bar: rounded track + an eased moving segment.
        c.create_line(BAR_X0, BAR_Y, BAR_X1, BAR_Y, fill=TRACK, width=px(4), capstyle="round")
        self._bar = c.create_line(BAR_X0, BAR_Y, BAR_X0, BAR_Y, fill=ACCENT, width=4,
                                  capstyle="round", state="hidden")
        self._status = c.create_text(TEXT_X + px(1), BAR_Y + px(22), text="Starting…", anchor="w",
                                     fill=SUBTLE, font=(FONT, 9))

        self._started = time.monotonic()
        self._faded_in = False
        self._job: str | None = None
        self._animate()
        self.update()

    # ------------------------------------------------------------------ setup
    def _set_alpha(self, value: float) -> None:
        try:
            self.attributes("-alpha", value)
        except tk.TclError:
            pass

    def _apply_native_frame(self) -> bool:
        """Windows 11: rounded corners + drop shadow drawn by the system.

        Returns False where this isn't available (Windows 10 and older, other
        platforms), in which case a thin border is drawn instead.
        """
        if sys.platform != "win32":
            return False
        try:
            import ctypes

            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            dwm = ctypes.windll.dwmapi

            def set_attr(attr: int, value: int) -> int:
                v = ctypes.c_int(value)
                return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

            DWMWA_NCRENDERING_POLICY, DWMNCRP_ENABLED = 2, 2
            DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
            DWMWA_BORDER_COLOR = 34
            if set_attr(DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND) != 0:
                return False
            set_attr(DWMWA_NCRENDERING_POLICY, DWMNCRP_ENABLED)
            r, g, b = (int(BORDER[i:i + 2], 16) for i in (1, 3, 5))
            set_attr(DWMWA_BORDER_COLOR, r | (g << 8) | (b << 16))  # COLORREF = 0x00BBGGRR

            class MARGINS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_int) for n in ("left", "right", "top", "bottom")]

            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARGINS(1, 1, 1, 1)))
            return True
        except Exception:
            return False

    def _draw_background(self) -> None:
        """Flat top that fades into a slightly cooler grey towards the bottom."""
        start = HEIGHT // 2
        for y in range(start, HEIGHT):
            t = (y - start) / (HEIGHT - start)
            self.canvas.create_line(0, y, WIDTH, y, fill=_mix(BG_TOP, BG_BOTTOM, t))

    def _load_image(self) -> tk.PhotoImage | None:
        if not SPLASH_IMAGE.is_file():
            return None
        try:
            from PIL import Image, ImageTk  # optional: smoother scaling, more formats
            img = Image.open(SPLASH_IMAGE)
            img.thumbnail((IMAGE_MAX, IMAGE_MAX), Image.LANCZOS)
            return ImageTk.PhotoImage(img, master=self)
        except ImportError:
            pass
        except Exception:
            return None
        try:
            img = tk.PhotoImage(master=self, file=str(SPLASH_IMAGE))
        except tk.TclError:
            return None
        factor = max(1, -(-max(img.width(), img.height()) // IMAGE_MAX))  # ceil division
        return img.subsample(factor) if factor > 1 else img

    def _draw_default_logo(self, cx: int, cy: int):
        """Placeholder: a document with highlighted 'coded' segments."""
        c = self.canvas
        w, h = px(74), px(92)
        left, top = cx - w // 2, cy - h // 2
        c.create_rectangle(left + 4, top + 4, left + w + 4, top + h + 4, fill=BORDER, width=0)
        c.create_rectangle(left, top, left + w, top + h, fill="#ffffff", outline=BORDER)
        lines = [(14, 1.0, None), (26, 0.8, "#f6c453"), (38, 1.0, None),
                 (50, 0.65, "#5b8def"), (62, 0.9, None), (74, 0.7, ACCENT)]
        for dy, frac, colour in lines:
            x0, x1 = left + 10, left + 10 + int((w - 20) * frac)
            if colour:
                c.create_rectangle(x0 - 3, top + dy - 5, x1 + 3, top + dy + 5, fill=colour, width=0)
            c.create_line(x0, top + dy, x1, top + dy, fill=SUBTLE, width=2)

    # -------------------------------------------------------------- behaviour
    def set_status(self, text: str) -> None:
        self.canvas.itemconfigure(self._status, text=text)

    def _animate(self):
        elapsed = time.monotonic() - self._started

        # Fade in.
        if not self._faded_in:
            if elapsed < FADE_SECONDS:
                self._set_alpha(elapsed / FADE_SECONDS)
            else:
                self._set_alpha(1.0)
                self._faded_in = True

        # Eased sweep, clipped to the track.
        phase = _ease_in_out((elapsed % BAR_PERIOD) / BAR_PERIOD)
        centre = BAR_X0 - BAR_SEGMENT / 2 + (BAR_X1 - BAR_X0 + BAR_SEGMENT) * phase
        a = max(BAR_X0, centre - BAR_SEGMENT / 2)
        b = min(BAR_X1, centre + BAR_SEGMENT / 2)
        if b - a < 2:
            self.canvas.itemconfigure(self._bar, state="hidden")
        else:
            self.canvas.coords(self._bar, a, BAR_Y, b, BAR_Y)
            self.canvas.itemconfigure(self._bar, state="normal")

        self._job = self.after(16, self._animate)

    def close(self):
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        # Short blocking fade-out; the main window is created right after.
        steps = 10
        for i in range(steps, -1, -1):
            self._set_alpha(i / steps)
            self.update()
            time.sleep(FADE_SECONDS / steps)
        self.destroy()
