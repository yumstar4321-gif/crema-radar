"""
Daily deal check (phase 2).

Asks Amazon's Creators API for the current offer on every product in
data/products.yaml that has an ASIN, works out which are genuine deals,
and writes build/live_prices.json for build.py to use.

The file is never committed: Amazon only allows prices that are under
24 hours old, so it is regenerated on every run.

Usage
  python scripts/fetch_deals.py            # real API (needs the 3 secrets below)
  python scripts/fetch_deals.py --sample   # offline test using fixtures/sample_response.json

Environment variables (set as GitHub Actions secrets):
  AMAZON_CLIENT_ID, AMAZON_CLIENT_SECRET
  AMAZON_TRACKING_ID   optional; overrides config.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from creators_api import CreatorsApi, CreatorsApiError  # noqa: E402

OUT = ROOT / "build" / "live_prices.json"
TRUSTED_BASES = {"WAS_PRICE", "LOWEST_PRICE", "LOWEST_PRICE_STRIKETHROUGH"}
GOOD_STOCK = {"IN_STOCK", "IN_STOCK_SCARCE"}


def g(d, *keys, default=None):
    """Safe nested get that tolerates camelCase or PascalCase keys."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, d.get(k[:1].upper() + k[1:]))
        if d is None:
            return default
    return d


def best_listing(item: dict) -> dict | None:
    listings = g(item, "offersV2", "listings", default=[]) or []
    new = [l for l in listings if (g(l, "condition", "value") or "New") == "New"]
    for l in new:
        if g(l, "isBuyBoxWinner"):
            return l
    return new[0] if new else None


def assess(item: dict, cfg: dict) -> dict:
    """Turn one API item into the compact record the site renders."""
    listing = best_listing(item) or {}
    price = g(listing, "price", default={}) or {}
    basis = g(price, "savingBasis", default={}) or {}
    savings = g(price, "savings", default={}) or {}
    deal = g(listing, "dealDetails")
    stock = g(listing, "availability", "type") or "UNKNOWN"

    pct = g(savings, "percentage") or 0
    basis_type = g(basis, "savingBasisType") or ""
    threshold = cfg["deals"]["min_discount_percent"]
    if basis_type == "LIST_PRICE" and not cfg["deals"].get("trust_list_price", False):
        threshold += 10

    is_deal = bool(
        listing and stock in GOOD_STOCK and not g(listing, "violatesMAP")
        and (pct >= threshold or (deal and pct >= cfg["deals"]["min_discount_percent"]))
    )

    return {
        "asin": item.get("asin"),
        "title": g(item, "itemInfo", "title", "displayValue"),
        "url": item.get("detailPageURL"),
        "image": g(item, "images", "primary", "medium", "url"),
        "price": g(price, "money", "displayAmount"),
        "price_amount": g(price, "money", "amount"),
        "was": g(basis, "money", "displayAmount"),
        "was_label": g(basis, "savingBasisTypeLabel") or "",
        "was_type": basis_type,
        "was_trusted": basis_type in TRUSTED_BASES,
        "saving": g(savings, "money", "displayAmount"),
        "saving_pct": pct,
        "stock": stock,
        "seller": g(listing, "merchantInfo", "name"),
        "deal_badge": g(deal, "badge") if deal else None,
        "deal_ends": g(deal, "endTime") if deal else None,
        "deal_access": g(deal, "accessType") if deal else None,
        "deal_claimed": g(deal, "percentClaimed") if deal else None,
        "lightning": g(listing, "type") == "LIGHTNING_DEAL",
        "map_hidden": bool(g(listing, "violatesMAP")),
        "is_deal": is_deal,
        "sample_product": item.get("sampleProductId"),  # test fixture only
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="use fixtures/sample_response.json instead of the API")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    products = yaml.safe_load((ROOT / "data" / "products.yaml").read_text()) or []
    asins = [p["asin"].strip() for p in products if (p.get("asin") or "").strip()]

    if args.sample:
        body = json.loads((ROOT / "fixtures" / "sample_response.json").read_text())
        container = body.get("itemsResult") or body.get("itemResults") or {}
        items, errors = container.get("items") or [], body.get("errors") or []
    else:
        cid, secret = os.getenv("AMAZON_CLIENT_ID"), os.getenv("AMAZON_CLIENT_SECRET")
        tag = os.getenv("AMAZON_TRACKING_ID") or cfg["amazon"]["tracking_id"]
        if not (cid and secret):
            print("No API keys set: skipping live prices. The site builds without them.")
            return 0
        if not asins:
            print("No ASINs in data/products.yaml yet: nothing to check.")
            return 0
        try:
            api = CreatorsApi(cid, secret, tag, marketplace=cfg["amazon"]["marketplace"])
            items, errors = api.get_items(asins)
        except (CreatorsApiError, requests.RequestException, KeyError, ValueError) as e:
            # Never fail the whole build: the site just shows "check price" buttons.
            print(f"Live prices skipped: {e}")
            OUT.unlink(missing_ok=True)
            return 0

    records = {r["asin"]: r for r in (assess(i, cfg) for i in items) if r["asin"]}
    for e in errors:
        print(f"  ! {g(e, 'code')}: {g(e, 'message')}")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample": bool(args.sample),
        "items": records,
    }, indent=2))

    deals = [r for r in records.values() if r["is_deal"]]
    print(f"Checked {len(records)} products, {len(deals)} deals found.")
    for d in sorted(deals, key=lambda r: -r["saving_pct"]):
        print(f"  - {d['saving_pct']}% off  {d['price']}  {d['title'][:60] if d['title'] else d['asin']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
