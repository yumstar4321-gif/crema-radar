"""
Minimal client for Amazon's Creators API (the replacement for PA-API 5).

Docs: https://affiliate-program.amazon.com/creatorsapi/docs/en-us/introduction

Australia uses the Far East (FE) credential region:
  token endpoint  https://api.amazon.co.jp/auth/o2/token
  marketplace     www.amazon.com.au

Access needs an approved Associates AU account AND at least 10 qualifying
sales in the past 30 days. Until then every call fails with
AssociateNotEligible, which this client reports clearly.
"""

from __future__ import annotations

import time
from typing import Iterable

import requests

API_BASE = "https://creatorsapi.amazon"
TOKEN_ENDPOINTS = {
    "NA": "https://api.amazon.com/auth/o2/token",
    "EU": "https://api.amazon.co.uk/auth/o2/token",
    "FE": "https://api.amazon.co.jp/auth/o2/token",  # JP, SG, AU
}

# Everything the deal check needs, and nothing more.
DEAL_RESOURCES = [
    "itemInfo.title",
    "images.primary.medium",
    "offersV2.listings.price",
    "offersV2.listings.availability",
    "offersV2.listings.condition",
    "offersV2.listings.dealDetails",
    "offersV2.listings.merchantInfo",
    "offersV2.listings.isBuyBoxWinner",
    "offersV2.listings.type",
]

MAX_IDS_PER_CALL = 10       # GetItems limit
MIN_SECONDS_BETWEEN = 1.1   # stay under 1 request/second


class CreatorsApiError(RuntimeError):
    pass


class CreatorsApi:
    def __init__(self, client_id: str, client_secret: str, partner_tag: str,
                 marketplace: str = "www.amazon.com.au", region: str = "FE",
                 timeout: int = 20):
        if not (client_id and client_secret and partner_tag):
            raise CreatorsApiError("Missing client_id, client_secret or partner_tag.")
        self.client_id = client_id
        self.client_secret = client_secret
        self.partner_tag = partner_tag
        self.marketplace = marketplace
        self.token_url = TOKEN_ENDPOINTS[region]
        self.timeout = timeout
        self._token: str | None = None
        self._token_expires = 0.0
        self._last_call = 0.0
        self.session = requests.Session()

    # ── auth ────────────────────────────────────────────────
    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        r = self.session.post(
            self.token_url,
            json={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "creatorsapi::default",
            },
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise CreatorsApiError(f"Token request failed ({r.status_code}): {r.text[:300]}")
        body = r.json()
        self._token = body["access_token"]
        self._token_expires = time.time() + int(body.get("expires_in", 3600))
        return self._token

    # ── calls ───────────────────────────────────────────────
    def _post(self, path: str, payload: dict, retries: int = 3) -> dict:
        for attempt in range(retries):
            wait = MIN_SECONDS_BETWEEN - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            r = self.session.post(
                f"{API_BASE}{path}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._get_token()}",
                    "Content-Type": "application/json",
                    "x-marketplace": self.marketplace,
                },
                timeout=self.timeout,
            )
            if r.status_code == 200:
                return r.json()
            text = r.text[:500]
            if "AssociateNotEligible" in text:
                raise CreatorsApiError(
                    "Amazon says this account isn't eligible for the API yet "
                    "(AssociateNotEligible). You need 10 qualifying sales in the "
                    "last 30 days. The site will keep running without live prices."
                )
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise CreatorsApiError(f"{path} failed ({r.status_code}): {text}")
        raise CreatorsApiError(f"{path} failed after {retries} attempts")

    def get_items(self, asins: Iterable[str], resources: list[str] = DEAL_RESOURCES) -> tuple[list[dict], list[dict]]:
        """Return (items, errors) for any number of ASINs, batching 10 at a time."""
        asins = [a for a in dict.fromkeys(asins) if a]
        items, errors = [], []
        for i in range(0, len(asins), MAX_IDS_PER_CALL):
            batch = asins[i:i + MAX_IDS_PER_CALL]
            body = self._post("/catalog/v1/getItems", {
                "itemIds": batch,
                "itemIdType": "ASIN",
                "marketplace": self.marketplace,
                "partnerTag": self.partner_tag,
                "condition": "New",
                "resources": resources,
            })
            # The docs show both "itemsResult" and "itemResults"; accept either.
            container = body.get("itemsResult") or body.get("itemResults") or {}
            items.extend(container.get("items") or [])
            errors.extend(body.get("errors") or [])
        return items, errors
