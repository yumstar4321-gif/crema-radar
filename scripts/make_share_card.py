"""Render the social share card (static/share.jpg) from scripts/share_card.html.

Run this only when the card design or wording changes:

    python scripts/make_share_card.py

It is deliberately NOT part of the daily build: the card is identical on every
run, and rendering it needs a headless browser the publish workflow does not
install. The committed static/share.jpg is what ships.
"""
import asyncio
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "scripts" / "share_card.html"
OUT = ROOT / "static" / "share.jpg"


async def render() -> None:
    from playwright.async_api import async_playwright
    from PIL import Image

    tmp = ROOT / "static" / "_share_tmp.png"
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        # 1200x630 is the size Facebook, LinkedIn, Slack and X expect.
        page = await browser.new_page(
            viewport={"width": 1200, "height": 630}, device_scale_factor=1
        )
        await page.goto(SRC.as_uri())
        await page.wait_for_timeout(600)          # let webfonts settle
        await page.screenshot(path=str(tmp))
        await browser.close()

    Image.open(tmp).convert("RGB").save(
        OUT, "JPEG", quality=88, optimize=True, progressive=True
    )
    tmp.unlink()
    print(f"Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    asyncio.run(render())
