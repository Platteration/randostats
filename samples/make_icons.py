"""Rasterise the app icon to the PNG sizes iOS and Android insist on.

Run once when `randostats/static/icon.svg` changes; the PNGs are committed so
there is no build step and no image dependency at install time.

    python samples/make_icons.py

Needs Playwright's Chromium, which is already used for the browser tests.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "randostats" / "static"
SIZES = (180, 192, 512)  # apple-touch-icon, Android home screen, splash/maskable


def _chromium() -> str | None:
    """Use an already-installed Chromium if there is one, rather than making
    the caller download a second copy."""
    for candidate in (Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome"),
                      Path("/opt/pw-browsers/chromium/chrome-linux/chrome")):
        if candidate.exists():
            return str(candidate)
    return None


async def render() -> None:
    from playwright.async_api import async_playwright

    svg = (STATIC / "icon.svg").read_text(encoding="utf-8")
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_chromium())
        try:
            for size in SIZES:
                page = await browser.new_page(viewport={"width": size, "height": size},
                                              device_scale_factor=1)
                await page.set_content(
                    f'<body style="margin:0">{svg.replace("512", str(size), 2)}</body>')
                await page.screenshot(path=str(STATIC / f"icon-{size}.png"), omit_background=True)
                await page.close()
                print(f"wrote icon-{size}.png")
        finally:
            await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(render())
    except ImportError:
        print("Playwright is needed to rasterise the icons: pip install playwright", file=sys.stderr)
        raise SystemExit(1)
