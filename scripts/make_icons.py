"""Render the site icons from static/logo.svg.

Run this only when the logo changes:

    python scripts/make_icons.py

Like the share card, this is deliberately NOT part of the daily build: the
icons are identical on every run and rendering them needs a headless browser
the publish workflow does not install. The committed PNGs are what ship.

Google needs two different things and they are easy to confuse:
  * the favicon it shows beside a search result, which must be square, at
    least 48x48, crawlable, and linked from the page head; and
  * the Organization logo in structured data, which should be a square image
    of the logo itself (not the wide social share card).
Both come out of this script.
"""
import asyncio
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "static" / "logo.svg"
OUT = ROOT / "static"

# name -> pixel size. Google recommends favicons be a multiple of 48.
SIZES = {
    "favicon-16.png": 16,
    "favicon-32.png": 32,
    "favicon-48.png": 48,
    "favicon-96.png": 96,
    "apple-touch-icon.png": 180,
    "icon-192.png": 192,
    "logo-512.png": 512,
}
ICO_SIZES = (16, 32, 48)


async def render() -> None:
    from playwright.async_api import async_playwright
    from PIL import Image

    svg = SRC.read_text(encoding="utf-8")
    page_html = (
        "<!doctype html><meta charset=utf-8>"
        "<style>html,body{margin:0;padding:0;background:transparent}"
        "svg{display:block;width:100vw;height:100vh}</style>" + svg
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for name, size in SIZES.items():
            page = await browser.new_page(
                viewport={"width": size, "height": size}, device_scale_factor=1
            )
            await page.set_content(page_html)
            await page.screenshot(path=str(OUT / name), omit_background=True)
            await page.close()
            print(f"  {name} ({size}x{size})")
        await browser.close()

    # A real multi-resolution favicon.ico. Browsers and crawlers that ignore
    # the <link> tags fall back to /favicon.ico at the site root, so build.py
    # copies this one there too.
    base = Image.open(OUT / "logo-512.png").convert("RGBA")
    base.save(
        OUT / "favicon.ico",
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
    )
    print(f"  favicon.ico ({', '.join(f'{s}x{s}' for s in ICO_SIZES)})")


if __name__ == "__main__":
    asyncio.run(render())
    print("Done. Commit the PNGs; the build copies static/ as-is.")
