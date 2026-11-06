"""
Builds the static site into ./site

  python scripts/build.py                 # normal build
  python scripts/build.py --allow-sample  # local preview with fake sample prices

Reads:
  config.yaml, data/products.yaml, data/picks.yaml, content/guides/*.md
  build/live_prices.json   (optional, written by fetch_deals.py)
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site"
LIVE = ROOT / "build" / "live_prices.json"

DEFAULT_SHAPE = {"machines": "espresso", "grinders": "grinder",
                 "pods": "pod", "accessories": "scale"}

CATEGORIES = {
    "machines": ("Espresso machines", "Manual, semi-automatic and bean-to-cup machines."),
    "grinders": ("Grinders", "Burr grinders, electric and hand. The upgrade that matters most."),
    "pods": ("Pod machines", "Capsule machines for zero-fuss coffee."),
    "accessories": ("Accessories", "Scales, jugs and the small things that make a big difference."),
}


# ── loading ─────────────────────────────────────────────────
def load_yaml(path: Path, default):
    if not path.exists():
        return default
    return yaml.safe_load(path.read_text(encoding="utf-8")) or default


def load_guides(base: str) -> list[dict]:
    guides = []
    md = markdown.Markdown(extensions=["extra", "sane_lists", "smarty"])
    for f in sorted((ROOT / "content" / "guides").glob("*.md")):
        raw = f.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
        meta, body = (yaml.safe_load(m.group(1)), m.group(2)) if m else ({}, raw)
        if meta.get("draft"):
            continue
        md.reset()
        body_html = prefix_links(md.convert(body), base)
        guides.append({
            "slug": f.stem,
            "title": meta.get("title", f.stem.replace("-", " ").title()),
            # Optional: a shorter, search-facing title when the headline on the
            # page is too long for a search result. Falls back to the headline.
            "seo_title": meta.get("seo_title") or meta.get("title", f.stem),
            "description": meta.get("description", ""),
            "date": to_date(meta.get("date")),
            "featured": bool(meta.get("featured")),
            "html": body_html,
        })
    guides.sort(key=lambda g: g["date"], reverse=True)
    return guides


def load_live(cfg: dict, allow_sample: bool, now: datetime) -> dict | None:
    if not LIVE.exists():
        return None
    data = json.loads(LIVE.read_text())
    if data.get("sample") and not allow_sample:
        print("Ignoring SAMPLE prices (use --allow-sample for local previews only).")
        return None
    fetched = datetime.fromisoformat(data["fetched_at"])
    age_h = (now - fetched).total_seconds() / 3600
    if age_h > cfg["deals"]["max_price_age_hours"] and not data.get("sample"):
        print(f"Live prices are {age_h:.1f}h old: hiding them (Amazon allows 24h max).")
        return None
    return data


# ── helpers ─────────────────────────────────────────────────
def to_date(v) -> date:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        return date.fromisoformat(v)
    return date.today()


def prefix_links(body_html: str, base: str) -> str:
    """Make root-relative links in Markdown work when hosted under a sub-path."""
    return re.sub(r'href="/(?!/)', f'href="{base}/', body_html)


def black_friday_context(today: date) -> dict:
    """Dates for the Black Friday page, worked out rather than hardcoded.

    Black Friday is the day after the fourth Thursday of November, so it
    moves every year. Once this year's Cyber Monday has passed the page
    rolls forward to next year's, which keeps the URL worth linking to
    instead of going stale every December.
    """
    def bf_for(year: int) -> date:
        thursdays = [d for i in range(1, 31)
                     if (d := date(year, 11, i)).weekday() == 3]
        return thursdays[3] + timedelta(days=1)

    bf = bf_for(today.year)
    if today > bf + timedelta(days=3):          # past Cyber Monday: next year
        bf = bf_for(today.year + 1)
    cyber = bf + timedelta(days=3)
    return {
        "year": bf.year,
        "date": bf,
        "cyber": cyber,
        "days_until": (bf - today).days,
        # The sale period in practice, not just the Friday.
        "live": bf - timedelta(days=7) <= today <= cyber,
        # Whether the masthead should carry a link to it.
        "in_season": bf - timedelta(days=45) <= today <= cyber + timedelta(days=5),
    }


def expand_amazon_links(html: str, product_by_id: dict, cfg: dict) -> str:
    """Turn [text](amazon:product-id) in a guide into a tagged Amazon link.

    Guides recommend things; without this every recommendation costs the
    reader two clicks to act on, which is where buyers are lost. The link is
    built by the same function the product pages use, so it always carries
    the tracking ID and rel="sponsored".
    """
    def sub(m: re.Match) -> str:
        p = product_by_id.get(m.group(1))
        if not p:
            print(f"  ! a guide links to amazon:{m.group(1)}, which is not in products.yaml")
            return m.group(0)
        return (f'href="{amazon_link(p, p.get("live"), cfg)}"'
                f' rel="sponsored nofollow noopener" target="_blank"')

    return re.sub(r'href="amazon:([a-z0-9-]+)"', sub, html)


def amazon_link(product: dict, live_item: dict | None, cfg: dict) -> str:
    tag = cfg["amazon"]["tracking_id"]
    host = "https://" + cfg["amazon"]["marketplace"]
    if live_item and live_item.get("url"):
        return live_item["url"]                      # Amazon's own tagged link
    if (product.get("asin") or "").strip():
        return f"{host}/dp/{product['asin'].strip()}?tag={tag}"
    return f"{host}/s?k={quote_plus(product.get('search') or product['name'])}&tag={tag}"


def fmt_local(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    return local.strftime("%-I:%M %p %Z, %-d %b %Y").replace("AM", "am").replace("PM", "pm")


# ── build ───────────────────────────────────────────────────
def build(allow_sample: bool = False, local: bool = False) -> None:
    cfg = load_yaml(ROOT / "config.yaml", {})
    tz = ZoneInfo(cfg["site"].get("timezone", "Australia/Sydney"))
    now = datetime.now(timezone.utc)
    today = now.astimezone(tz).date()

    site_url = cfg["site"]["url"].rstrip("/")
    base = "" if local else urlparse(site_url).path.rstrip("/")   # "" or "/crema-radar"

    products = load_yaml(ROOT / "data" / "products.yaml", [])
    picks_raw = load_yaml(ROOT / "data" / "picks.yaml", [])
    guides = load_guides(base)
    live = load_live(cfg, allow_sample, now)

    # Attach live data and links to products
    live_items = (live or {}).get("items", {})
    by_sample = {v.get("sample_product"): v for v in live_items.values() if v.get("sample_product")}
    for p in products:
        asin = (p.get("asin") or "").strip()
        item = live_items.get(asin) if asin else None
        if item is None and live and live.get("sample"):
            item = by_sample.get(p["id"])
        p["live"] = item
        p["link"] = amazon_link(p, item, cfg)
        p["has_asin"] = bool(asin)
        p["url"] = f"{base}/product/{p['id']}/"
        p["category_name"] = CATEGORIES.get(p["category"], (p["category"].title(), ""))[0]
        # Your own (or brand-licensed) photo, from static/products/
        img = (p.get("image") or "").strip()
        p["image_url"] = None
        if img:
            if (ROOT / "static" / "products" / img).is_file():
                p["image_url"] = f"{base}/static/products/{img}"
            else:
                print(f"  ! image '{img}' for {p['id']} not found in static/products/")
        p["shape"] = (p.get("shape") or "").strip() or DEFAULT_SHAPE.get(p["category"], "espresso")

        # Past prices reported by a named source. Never Amazon's own data.
        hist = []
        for h in (p.get("sale_history") or []):
            missing = [k for k in ("date", "price", "source", "url") if not h.get(k)]
            if missing:
                print(f"  ! {p['id']}: sale_history entry missing {', '.join(missing)}, skipped")
                continue
            d = to_date(h["date"])
            pct = None
            if h.get("was") and h["was"] > h["price"]:
                pct = round((h["was"] - h["price"]) / h["was"] * 100)
            hist.append({**h, "date": d, "saving_pct": pct,
                         "when": d.strftime("%-d %b %Y"),
                         "when_short": d.strftime("%b %Y")})
        hist.sort(key=lambda h: h["date"], reverse=True)
        p["sale_history"] = hist
        # Colour and variant suffixes push <title> past the ~60 characters a
        # search result shows. They stay in the H1, out of the title.
        p["short_name"] = re.sub(r"\s*\([^)]*\)", "", p["name"]).strip()
        p["best_deal"] = min(hist, key=lambda h: h["price"]) if hist else None

        # When it last had a big discount, e.g. "October 2026"
        p["last_deal_date"] = None
        if p.get("last_deal"):
            try:
                p["last_deal_date"] = datetime.strptime(str(p["last_deal"]), "%B %Y").date()
            except ValueError:
                print(f"  ! last_deal for {p['id']} should look like 'October 2026'")
    product_by_id = {p["id"]: p for p in products}
    for g in guides:
        g["html"] = expand_amazon_links(g["html"], product_by_id, cfg)

    # Products discounted on Amazon AU in roughly the last 4 months: likely to drop again
    recent_droppers = sorted(
        [p for p in products if p["last_deal_date"] and (today - p["last_deal_date"]).days <= 125
         and not (p["live"] and p["live"]["is_deal"])],
        key=lambda p: p["last_deal_date"], reverse=True,
    )

    # Live deals (phase 2)
    live_deals = sorted(
        [p for p in products if p["live"] and p["live"]["is_deal"]],
        key=lambda p: -p["live"]["saving_pct"],
    )

    # Hand-picked deals (phase 1)
    picks = []
    for pk in picks_raw:
        prod = product_by_id.get(pk.get("product"))
        if not prod:
            print(f"  ! pick refers to unknown product '{pk.get('product')}', skipped")
            continue
        ends = to_date(pk["ends"]) if pk.get("ends") else None
        if ends and ends < today:
            continue
        picks.append({**pk, "date": to_date(pk["date"]), "ends": ends, "p": prod})
    picks.sort(key=lambda x: x["date"], reverse=True)

    price_time = fmt_local(datetime.fromisoformat(live["fetched_at"]), tz) if live else None
    is_sample = bool(live and live.get("sample"))

    env = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True, lstrip_blocks=True,
    )

    def meta_title(core: str, limit: int = 60) -> str:
        """Page title for <title>, with the brand appended only if it fits.

        Google truncates search-result titles at roughly 60 characters, and a
        truncated title loses the words that earn the click. The brand is the
        least useful part, so it is the part that goes.
        """
        core = " ".join(str(core).split())
        full = f"{core} | {cfg['site']['name']}"
        return full if len(full) <= limit else core

    env.globals["meta_title"] = meta_title
    common = dict(
        cfg=cfg, site=cfg["site"], base=base, site_url=site_url, year=today.year,
        today=today, price_time=price_time, is_sample=is_sample, bf=black_friday_context(today),
        categories=CATEGORIES, newsletter=cfg.get("newsletter", {}), today_month=today.month,
        disclosure="As an Amazon Associate I earn from qualifying purchases.",
    )

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copytree(ROOT / "static", OUT / "static")

    pages: list[str] = []

    def render(tpl: str, path: str, **ctx):
        dest = OUT / path.strip("/") / "index.html" if not path.endswith(".html") else OUT / path.strip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(env.get_template(tpl).render(**common, page_path=path, **ctx), encoding="utf-8")
        if path.endswith("/"):
            pages.append(path)

    # Homepage: don't repeat products already shown in the top section
    shown = {p["id"] for p in live_deals[:6]} | {pk["p"]["id"] for pk in picks[:6]}
    if not live_deals and not picks:
        shown |= {p["id"] for p in recent_droppers[:6]}
    # Products with a recorded price first: a "Not recorded yet" card sitting
    # beside priced ones reads as missing data rather than as a watchlist.
    featured = sorted(
        (p for p in products
         if p["category"] in ("machines", "grinders") and p["id"] not in shown),
        key=lambda p: (p.get("best_deal") is None,),
    )[:4]
    shown |= {p["id"] for p in featured}
    # Accessories earn the same commission rate on a far shorter decision, so
    # they belong on the homepage, not only behind a category link.
    accessories = sorted(
        (p for p in products if p["category"] == "accessories" and p["id"] not in shown),
        key=lambda p: (p.get("best_deal") is None,),
    )[:4]
    render("index.html", "/", live_deals=live_deals[:6], picks=picks[:6], recent_droppers=recent_droppers[:6],
           guides=[g for g in guides if g["featured"]][:3] or guides[:3], featured=featured,
           accessories=accessories)
    render("deals.html", "/deals/", live_deals=live_deals, picks=picks, recent_droppers=recent_droppers)
    # A permanent URL, reused every year, so links to it keep their value.
    render("black_friday.html", "/black-friday/", live_deals=live_deals, picks=picks,
           recent_droppers=recent_droppers, accessories=accessories)
    render("guides.html", "/guides/", guides=guides)
    for g in guides:
        render("guide.html", f"/guides/{g['slug']}/", guide=g)
    for key, (name, desc) in CATEGORIES.items():
        items = [p for p in products if p["category"] == key]
        if items:
            render("category.html", f"/category/{key}/", cat_key=key, cat_name=name, cat_desc=desc, items=items)
    for p in products:
        related = [q for q in products if q["category"] == p["category"] and q["id"] != p["id"]][:3]
        p_picks = [x for x in picks if x["p"]["id"] == p["id"]]
        render("product.html", f"/product/{p['id']}/", p=p, related=related, p_picks=p_picks)
    render("about.html", "/about/")
    render("privacy.html", "/privacy/")
    render("404.html", "404.html")

    # RSS feed: newsletter tools turn this into the daily email.
    # Every link points at THIS site, never straight to Amazon
    # (Amazon doesn't allow affiliate links in emails).
    feed_items = []
    if live_deals and not is_sample:
        lines = "".join(
            f"<li><a href=\"{site_url}/product/{p['id']}/\">{html.escape(p['name'])}</a>: "
            f"{p['live']['saving_pct']}% off</li>" for p in live_deals[:10]
        )
        feed_items.append({
            "title": f"Today's coffee gear deals: {today.strftime('%-d %B %Y')}",
            "link": f"{site_url}/deals/",
            "guid": f"{site_url}/deals/#{today.isoformat()}",
            "date": now,
            "html": f"<p>{len(live_deals)} deals on our watchlist today.</p><ul>{lines}</ul>"
                    f"<p><a href=\"{site_url}/deals/\">See all deals with today's prices</a></p>",
        })
    for pk in picks[:15]:
        feed_items.append({
            "title": pk["headline"],
            "link": f"{site_url}/product/{pk['p']['id']}/",
            "guid": f"{site_url}/pick/{pk['date'].isoformat()}/{pk['p']['id']}",
            "date": datetime.combine(pk["date"], datetime.min.time(), tz),
            "html": f"<p>{html.escape(pk.get('note', ''))}</p>"
                    f"<p><a href=\"{site_url}/product/{pk['p']['id']}/\">View the deal</a></p>",
        })
    for g in guides[:10]:
        feed_items.append({
            "title": g["title"], "link": f"{site_url}/guides/{g['slug']}/",
            "guid": f"{site_url}/guides/{g['slug']}/",
            "date": datetime.combine(g["date"], datetime.min.time(), tz),
            "html": f"<p>{html.escape(g['description'])}</p>",
        })
    feed_items.sort(key=lambda i: i["date"], reverse=True)
    for i in feed_items:
        i["pubdate"] = format_datetime(i["date"])
    (OUT / "feed.xml").write_text(env.get_template("feed.xml").render(**common, items=feed_items,
                                  build_date=format_datetime(now)), encoding="utf-8")

    # SEO plumbing
    urls = "".join(f"<url><loc>{site_url}{p[len(base):] if base and p.startswith(base) else p}</loc></url>" for p in pages)
    (OUT / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {site_url}/sitemap.xml\n")
    (OUT / ".nojekyll").write_text("")

    # Crawlers and older browsers that ignore the <link rel="icon"> tags fall
    # back to /favicon.ico at the site root, so it has to exist there too.
    ico = ROOT / "static" / "favicon.ico"
    if ico.is_file():
        shutil.copyfile(ico, OUT / "favicon.ico")
    else:
        print("  ! static/favicon.ico missing — run scripts/make_icons.py")

    (OUT / "site.webmanifest").write_text(json.dumps({
        "name": cfg["site"]["name"],
        "short_name": cfg["site"]["name"],
        "icons": [
            {"src": f"{base}/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": f"{base}/static/logo-512.png", "sizes": "512x512", "type": "image/png"},
        ],
        "theme_color": "#2b1d14",
        "background_color": "#fbf6ef",
        "display": "standalone",
        "start_url": f"{base}/",
    }, indent=2))

    # GitHub Pages reads CNAME from the published branch. The publish force-pushes,
    # so without this the custom domain would be dropped on every run.
    domain = (cfg["site"].get("custom_domain") or "").strip().lstrip("https://").rstrip("/")
    if domain and not local:
        (OUT / "CNAME").write_text(domain + "\n")
        if domain not in site_url:
            print(f"  ! custom_domain is '{domain}' but site.url is '{site_url}' — update url to match")

    print(f"Built {len(pages)} pages into ./site "
          f"({len(live_deals)} live deals, {len(picks)} picks, {len(guides)} guides"
          f"{', SAMPLE PRICES' if is_sample else ''}).")


def make_double_clickable() -> None:
    """Rewrite root links (/deals/) to relative file links (../deals/index.html)
    so the site opens straight from a folder by double-clicking index.html."""
    def fix(match: re.Match, depth: int) -> str:
        attr, path = match.group(1), match.group(2)
        path, _, frag = path.partition("#")
        path = path.lstrip("/")
        if path == "" or path.endswith("/"):
            path += "index.html"
        rel = "../" * depth + path
        return f'{attr}="{rel}{"#" + frag if frag else ""}"'
    for f in OUT.rglob("*.html"):
        depth = len(f.relative_to(OUT).parts) - 1
        html_text = f.read_text(encoding="utf-8")
        html_text = re.sub(r'(href|src)="(/(?!/)[^"]*)"', lambda m: fix(m, depth), html_text)
        f.write_text(html_text, encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sample", action="store_true", help="show fake fixture prices (local preview only)")
    ap.add_argument("--local", action="store_true", help="build for previewing at http://localhost:8000/")
    ap.add_argument("--preview", action="store_true", help="build a folder you can open by double-clicking index.html")
    a = ap.parse_args()
    build(a.allow_sample, a.local or a.preview)
    if a.preview:
        make_double_clickable()
        print("Preview ready: open site/index.html in your browser.")
    sys.exit(0)
