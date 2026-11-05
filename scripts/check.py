"""
Safety check that every automated run must pass before pushing.

  python scripts/check.py

Fails (exit code 1) if:
  • a pick refers to a product that doesn't exist, or has a bad date
  • a pick contains a price or percentage (Amazon only allows prices from its API)
  • the site doesn't build
  • any page has a broken internal link
  • any Amazon link is missing the tracking ID or rel="sponsored"
  • the RSS feed (which becomes the email) links to Amazon
  • Amazon images or prices appear without live API data
"""

from __future__ import annotations

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
products = yaml.safe_load((ROOT / "data" / "products.yaml").read_text()) or []
picks = yaml.safe_load((ROOT / "data" / "picks.yaml").read_text()) or []
ids = {p["id"] for p in products}
tag = cfg["amazon"]["tracking_id"]

# ── data checks ─────────────────────────────────────────────
if len(ids) != len(products):
    fail("duplicate product id in products.yaml")
for p in products:
    asin = (p.get("asin") or "").strip()
    if asin and not re.fullmatch(r"[A-Z0-9]{10}", asin):
        fail(f"product {p['id']}: ASIN '{asin}' isn't 10 letters/digits")

# Past prices are allowed ONLY as sale_history entries that name a source and
# link to it. Amazon's own prices may never be typed in by hand.
for p in products:
    for i, h in enumerate(p.get("sale_history") or []):
        where = f"{p['id']} sale_history entry {i + 1}"
        for key in ("date", "price", "source", "url"):
            if not h.get(key):
                fail(f"{where}: missing '{key}'. Every past price needs a date, price, source and link.")
        if h.get("price") is not None and not isinstance(h["price"], (int, float)):
            fail(f"{where}: price must be a plain number, e.g. 588")
        if h.get("was") is not None and not isinstance(h["was"], (int, float)):
            fail(f"{where}: was must be a plain number")
        url = str(h.get("url") or "")
        if url and not url.startswith("https://"):
            fail(f"{where}: url must start with https://")
        if "amazon." in url.lower():
            fail(f"{where}: the source must be a published report, not an Amazon page. "
                 "Amazon prices may only come from its API.")
        if h.get("date") and not isinstance(h["date"], date):
            try:
                date.fromisoformat(str(h["date"]))
            except ValueError:
                fail(f"{where}: date must be YYYY-MM-DD")

price_words = re.compile(r"(\$\s?\d|AU\$|A\$|\d\s?%|\bper ?cent\b|\bhalf price\b)", re.I)
for i, pk in enumerate(picks):
    where = f"picks.yaml entry {i + 1}"
    if pk.get("product") not in ids:
        fail(f"{where}: unknown product '{pk.get('product')}'")
    for key in ("date", "ends"):
        if pk.get(key) and not isinstance(pk[key], date):
            try:
                date.fromisoformat(str(pk[key]))
            except ValueError:
                fail(f"{where}: {key} must be YYYY-MM-DD")
    if not pk.get("headline"):
        fail(f"{where}: missing headline")
    text = f"{pk.get('headline', '')} {pk.get('note', '')}"
    if price_words.search(text):
        fail(f"{where}: contains a price or percentage; describe the deal in words")

# ── build ───────────────────────────────────────────────────
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build.py")], capture_output=True, text=True)
print(r.stdout.strip())
if r.returncode != 0:
    fail(f"build failed:\n{r.stderr[-1500:]}")

# ── output checks ───────────────────────────────────────────
site = ROOT / "site"
if site.exists() and not errors:
    base = urlparse(cfg["site"]["url"]).path.rstrip("/")
    live = (ROOT / "build" / "live_prices.json").exists()
    for f in site.rglob("*.html"):
        h = f.read_text(encoding="utf-8")
        rel = f.relative_to(site)
        if not live and re.search(r'class="price__now"', h):
            fail(f"{rel}: shows a price without live API data")
        if not live and "m.media-amazon.com" in h:
            fail(f"{rel}: shows an Amazon image without live API data")
        for m in re.finditer(r'<a [^>]*href="(https://www\.amazon\.com\.au[^"]+)"[^>]*>', h):
            if f"tag={tag}" not in m.group(1):
                fail(f"{rel}: Amazon link without tracking ID: {m.group(1)[:80]}")
            if "sponsored" not in m.group(0):
                fail(f"{rel}: Amazon link missing rel=sponsored")
        for href in re.findall(r'(?:href|src)="(/[^"#?]*)', h):
            if base and not href.startswith(base + "/"):
                fail(f"{rel}: link outside the site path: {href}")
                continue
            t = site / href[len(base) + 1:] if base else site / href.lstrip("/")
            if not (t.is_file() or (t / "index.html").is_file()):
                fail(f"{rel}: broken link {href}")
    feed = (site / "feed.xml").read_text(encoding="utf-8")
    try:
        ET.fromstring(feed)
    except ET.ParseError as e:
        fail(f"feed.xml is not valid XML: {e}")
    if "amazon." in feed.lower():
        fail("feed.xml links to Amazon; emails must link only to this site")

if errors:
    print("\nCHECK FAILED:")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
print("All checks passed.")
