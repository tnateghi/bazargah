"""Chromium launch options and window sizing for Playwright on Windows."""

from __future__ import annotations

import sys

CHROMIUM_ARGS = [
    "--start-maximized",
    "--window-position=0,0",
    "--force-device-scale-factor=1",
]

# Playwright launch: viewport=None alone can still behave like 1280×720 until resize.
# no_viewport=True lets the page track the real window size.
LAUNCH_VIEWPORT_KWARGS = {"no_viewport": True, "viewport": None}


def get_work_area_size() -> tuple[int, int]:
    """Primary monitor work area (excludes taskbar on Windows)."""
    if sys.platform == "win32":
        try:
            import ctypes

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = RECT()
            # SPI_GETWORKAREA
            if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):
                w = int(rect.right - rect.left)
                h = int(rect.bottom - rect.top)
                if w > 400 and h > 300:
                    return w, h
            user32 = ctypes.windll.user32
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        except Exception:
            pass
    return 1920, 1080


def _cdp_window_id(page):
    cdp = page.context.new_cdp_session(page)
    return cdp, cdp.send("Browser.getWindowForTarget")["windowId"]


def maximize_window(page) -> None:
    """
    Size the OS window to the work area (more reliable than 'maximized' alone for
    Playwright viewport sync on Windows).
    """
    w, h = get_work_area_size()
    try:
        cdp, window_id = _cdp_window_id(page)
        cdp.send(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {
                    "left": 0,
                    "top": 0,
                    "width": w,
                    "height": h,
                    "windowState": "normal",
                },
            },
        )
    except Exception:
        try:
            cdp, window_id = _cdp_window_id(page)
            cdp.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "maximized"}},
            )
        except Exception:
            pass


def sync_page_layout_to_window(page) -> None:
    """After resizing the window, nudge Chromium/Playwright to use full content area."""
    page.wait_for_timeout(350)
    try:
        page.evaluate(
            """() => {
              window.dispatchEvent(new Event('resize'));
              if (window.visualViewport) {
                window.visualViewport.dispatchEvent(new Event('resize'));
              }
            }"""
        )
    except Exception:
        pass

    # If a fixed viewport is still active (smaller than the window), expand it.
    try:
        vs = page.viewport_size
    except Exception:
        vs = None
    if not vs:
        return

    try:
        inner = page.evaluate(
            "() => ({ w: window.innerWidth, h: window.innerHeight })"
        )
    except Exception:
        return

    iw, ih = int(inner.get("w") or 0), int(inner.get("h") or 0)
    if iw < 100 or ih < 100:
        w, h = get_work_area_size()
        chrome = 90
        page.set_viewport_size({"width": w, "height": max(720, h - chrome)})
        return

    if vs["width"] < iw - 20 or vs["height"] < ih - 20:
        page.set_viewport_size({"width": iw, "height": ih})


def prepare_browser_page(page) -> None:
    """Maximize window and make page content fill it."""
    maximize_window(page)
    sync_page_layout_to_window(page)
