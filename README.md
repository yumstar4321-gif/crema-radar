# Crema Radar: setup guide

A coffee gear deals site for Amazon Australia that rebuilds itself every morning.

- **Phase 1 (now):** buying guides + a watchlist of coffee gear + your hand-picked deals. Links go to Amazon AU with your affiliate tag. No prices shown (Amazon only allows prices from its API).
- **Phase 2 (after 10 sales in 30 days):** add two API keys and the site starts checking Amazon's live prices every morning, flags real discounts, and feeds a daily deal email. No code changes needed.

Hosting is free (GitHub Pages). You'll need about an hour for the first setup.

---

## What's in the folder

| Path | What it is | Do you edit it? |
|---|---|---|
| `config.yaml` | Site name, web address, your Amazon tracking ID, deal rules, newsletter form | **Yes, once** |
| `data/products.yaml` | The watchlist: products listed on the site and checked daily | **Yes, often** |
| `data/picks.yaml` | Your hand-picked deals (phase 1) | **Yes, most days** |
| `content/guides/*.md` | Buying guides (plain text with `##` headings) | **Yes, add more** |
| `templates/`, `static/` | Page layouts and styling | Only to change the look |
| `scripts/build.py` | Builds the website into `site/` | No |
| `scripts/fetch_deals.py`, `scripts/creators_api.py` | The daily Amazon price check | No |
| `.github/workflows/daily.yml` | Runs everything every morning and publishes | No |
| `fixtures/sample_response.json` | **Fake** data for testing only | No |

---

## Step 1: Put the site online (about 20 minutes)

1. Create a free account at **github.com**.
2. Click **New repository**. Name it `crema-radar` (or your site name), set it to **Public**, and create it.
3. Click **uploading an existing file** and drag in everything from this folder.
   - **Mac users:** the `.github` folder is hidden in Finder. Press **Cmd + Shift + .** to show it, and make sure it gets uploaded. Without it, nothing runs automatically.
4. In the repository, go to **Settings → Pages**. Under **Source**, choose **GitHub Actions**.
5. Edit `config.yaml` on GitHub (click it, then the pencil icon):
   - `url:` → `https://YOUR-GITHUB-USERNAME.github.io/crema-radar`
   - Commit the change. This triggers the first build.
6. Go to the **Actions** tab and watch "Daily deal check and publish" run (2–3 minutes). Your site is now live at the address above.

**Optional: your own domain.** A domain like `cremaradar.com.au` costs roughly $15–30 a year (`.com.au` needs an ABN). Add it under **Settings → Pages → Custom domain**, then update `url:` in `config.yaml`. A real domain helps with trust and Amazon approval.

---

## Step 2: Join Amazon Associates Australia (about 20 minutes)

Do this **after** the site is live; Amazon reviews your site.

1. Go to **affiliate-program.amazon.com.au** and sign up.
2. Enter your site address. For "topics", choose Home & Kitchen.
3. You'll get a **tracking ID ending in `-22`**. Put it in `config.yaml` → `tracking_id:` and commit.
4. The affiliate disclosure Amazon requires is already in every page footer and on the About page.

**Important:** new accounts are approved provisionally. Amazon typically closes accounts that don't make **3 qualifying sales within 180 days**. Check the exact terms in Associates Central.

---

## Step 3: Set up the daily email (about 15 minutes)

The site publishes an RSS feed at `/feed.xml`. Newsletter tools can turn that into an automatic email.

1. Pick a newsletter tool that supports **"RSS-to-email"** (Mailchimp's RSS campaigns are one option; several others do it too). Check pricing for your list size.
2. Create an RSS campaign pointing at `https://YOUR-SITE/feed.xml`, sending **daily** at about 7am Sydney time, and only when there's something new.
3. From the tool's **embedded sign-up form** code, copy the `action="…"` URL into `config.yaml` → `newsletter: form_action:`. Set `provider_name:` too. The sign-up box then appears across the site.

**Amazon rule:** you can't put Amazon affiliate links in emails. The feed already links only to *your* site, and your site links to Amazon. Don't paste Amazon links into emails by hand.

---

## Your daily routine in phase 1 (5–10 minutes)

1. Browse **amazon.com.au → Today's Deals**, plus the Kitchen and coffee machine sections.
2. For anything on your watchlist that's genuinely cheap, add an entry to `data/picks.yaml`:
   ```yaml
   - date: 2026-11-04
     product: breville-barista-express
     headline: "Barista Express in the Black Friday sale"
     note: "Biggest drop we've seen on Amazon AU this year."
     ends: 2026-11-30
   ```
   **Never type the Amazon price.** Describe the deal in words instead.
3. Commit. The site rebuilds in about 2 minutes, and the pick goes into the next morning's email.

You can also paste a deal into a chat with Claude and ask it to write the `picks.yaml` entry for you.

### Adding products and ASINs

Each product in `data/products.yaml` has an `asin:` field. While it's blank, the button links to an Amazon AU search. To link the exact product:
1. Open the product on amazon.com.au. Confirm it's **Australian stock** (AU plug, sold by Amazon AU or the brand).
2. Copy the ASIN from **Product details** (10 characters, usually starting with `B0`).
3. Paste it into `asin: "B0XXXXXXXX"`.

Fill in ASINs before phase 2: the daily price check only watches products that have one.

---

## Phase 2: switch on the automatic price check

**When:** your Associates account has **10 or more qualifying sales in the last 30 days** (Associates Central → Reports). Below that, Amazon blocks API access, and the site simply carries on without prices.

1. In **Associates Central**, find **Creators API** (under Tools) and register an application.
2. Create credentials. You'll get a **Credential ID** and **Credential Secret**. Australia uses the **Far East** region (credential version 3.3); the code already handles this.
3. In your GitHub repository, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `AMAZON_CLIENT_ID` = your Credential ID
   - `AMAZON_CLIENT_SECRET` = your Credential Secret
4. **Actions → Daily deal check and publish → Run workflow.** Check the log for "Checked N products, N deals found."

From then on, every morning the workflow:
- asks Amazon for the current price on every product with an ASIN (10 per request, at most 1 request a second),
- marks a product as a deal when Amazon's own savings is at least `min_discount_percent` (12% by default). Discounts measured against a **list price (RRP)** need 10 points more, because RRPs are often inflated,
- skips out-of-stock items and items whose price Amazon hides (MAP),
- shows prices with the time they were checked, and hides them if they're more than 23 hours old,
- adds a "Today's deals" entry to the RSS feed, which your newsletter tool sends.

If you drop below 10 sales, Amazon pauses API access and the site automatically falls back to phase 1 mode. Nothing breaks.

Tune the deal rules in `config.yaml` under `deals:`.

---

## Amazon rules this site already follows

| Rule | How it's handled |
|---|---|
| Affiliate disclosure on every page | Footer + About page |
| Prices only from the API, with a timestamp, under 24h old | Prices only appear in phase 2, with a time stamp; stale data is hidden and never saved to GitHub |
| Don't store Amazon images | Images are only shown from the live API response, never downloaded |
| No affiliate links in emails | The RSS feed and emails link only to your site |
| Mark affiliate links | All Amazon links use `rel="sponsored nofollow"` |
| No scraping Amazon | Nothing in this project scrapes. Phase 1 is manual, phase 2 uses the official API |

---

## Getting your first sales

Most of your first sales will come from **search**: people googling "Breville Barista Express sale" or "best coffee grinder Australia". To get there:
- **Write a guide a week.** Aim for specific searches, e.g. "Bambino Plus vs Barista Express", "best coffee grinder under $300 Australia", "coffee machine Black Friday Australia".
- **Submit the site to Google Search Console** and add `https://YOUR-SITE/sitemap.xml`.
- **Black Friday is weeks away.** Get picks up early, since that's when buyers search hardest.
- Share genuinely useful deals in communities you're part of, following each community's rules on self-promotion and affiliate links.

---

## Previewing on your own computer (optional)

```bash
pip install -r requirements.txt
python scripts/build.py --local                 # real data
python -m http.server 8000 --directory site     # open http://localhost:8000
```

To see how pages look **with prices**, using fake sample data:
```bash
python scripts/fetch_deals.py --sample
python scripts/build.py --local --allow-sample  # shows a red "FAKE PRICES" banner
```
Never publish a sample build. The live workflow refuses sample data automatically.
