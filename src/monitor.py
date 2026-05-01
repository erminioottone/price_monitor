#!/usr/bin/env python3
"""Price Monitor - fetches prices and sends email alerts on changes.

Orca.com is protected by Cloudflare Enterprise and blocks all automated
server-side requests. We use ScraperAPI (free tier: 1000 req/month) which
renders pages with a real headless browser to bypass Cloudflare.

Setup: sign up at https://www.scraperapi.com (free, no credit card needed)
       and add SCRAPERAPI_KEY as a GitHub Actions secret.
"""

import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_DIR      = Path(__file__).parent.parent
PRODUCTS_FILE = BASE_DIR / "products.json"
PRICES_FILE   = BASE_DIR / "prices.json"


def clean_price(value):
    s = str(value).strip()
    s = re.sub(r'[€$£\s\xa0\u202f\u00a0]', '', s)
    s = re.sub(r'(\d)[.](\d{3})(?:[,]|$)', r'\1\2', s)
    s = s.replace(',', '.')
    m = re.search(r'\d+(?:\.\d{1,2})?', s)
    return float(m.group()) if m else None


def extract_prices_from_html(html, min_price=10, max_price=5000):
    """Extract all plausible prices from HTML, sorted ascending."""
    prices = []
    seen   = set()
    soup   = BeautifulSoup(html, "html.parser")

    # 1. JSON-LD structured data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                offers = item.get("offers") or {}
                if isinstance(offers, list):
                    for o in offers:
                        p = clean_price(str(o.get("price", "")))
                        if p and min_price < p < max_price and p not in seen:
                            prices.append(p); seen.add(p)
                else:
                    for key in ("price", "lowPrice"):
                        val = offers.get(key) or item.get(key)
                        if val:
                            p = clean_price(str(val))
                            if p and min_price < p < max_price and p not in seen:
                                prices.append(p); seen.add(p)
        except Exception:
            pass

    # 2. CSS selectors
    for sel in [
        "[itemprop='price']", "[data-price]",
        ".price", ".price-item", ".product-price", ".current-price",
        ".pdp-price", ".c-product-price", ".product-card__price",
        ".price__current", ".price__sale", ".amount", ".money",
    ]:
        for el in soup.select(sel):
            if el.select(sel):
                continue
            p = clean_price(el.get_text())
            if p and min_price < p < max_price and p not in seen:
                prices.append(p); seen.add(p)

    # 3. Inline JSON price fields
    for m in re.finditer(
        r'"(?:price|salesPrice|listPrice|currentPrice)"\s*:\s*["\s]*(\d+(?:[.,]\d{1,2})?)',
        html
    ):
        p = clean_price(m.group(1))
        if p and min_price < p < max_price and p not in seen:
            prices.append(p); seen.add(p)

    # 4. EUR text patterns as last resort
    if not prices:
        for m in re.finditer(r'(\d{2,4}(?:[.,]\d{2}))\s*(?:€|EUR)', html):
            p = clean_price(m.group(1))
            if p and min_price < p < max_price and p not in seen:
                prices.append(p); seen.add(p)

    return sorted(prices)


# ── ScraperAPI (bypasses Cloudflare with real headless browser) ──────────────

def fetch_with_scraperapi(url, render_js=True):
    """
    Fetch via ScraperAPI which uses real Chrome to bypass Cloudflare.
    Free tier: 1000 requests/month (plenty for 4x/day × 2 products = ~240/month).
    Sign up: https://www.scraperapi.com
    """
    api_key = os.environ.get("SCRAPERAPI_KEY")
    if not api_key:
        print("  [ScraperAPI] SCRAPERAPI_KEY not set — skipping")
        return None

    params = {
        "api_key": api_key,
        "url": url,
        "render": "true" if render_js else "false",
        "country_code": "es",
    }
    try:
        print(f"  [ScraperAPI] fetching {url}")
        r = requests.get("https://api.scraperapi.com/", params=params, timeout=60)
        print(f"  [ScraperAPI] status={r.status_code} size={len(r.text)}")
        if r.status_code == 200 and len(r.text) > 1000:
            return r.text
        else:
            print(f"  [ScraperAPI] response too small or error: {r.text[:200]}")
    except Exception as e:
        print(f"  [ScraperAPI] error: {e}", file=sys.stderr)
    return None


# ── Shopify JSON endpoint (always works, no auth needed) ─────────────────────

def fetch_shopify_price(url, price_index=0):
    handle   = url.rstrip('/').split('/products/')[-1].split('?')[0]
    domain   = url.split('/products/')[0]
    json_url = f"{domain}/products/{handle}.json"
    print(f"  [Shopify JSON] {json_url}")
    r = requests.get(json_url, timeout=20,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    variants = r.json().get("product", {}).get("variants", [])
    prices   = sorted(set(float(v["price"]) for v in variants if v.get("price")))
    print(f"  [Shopify] prices found: {prices}")
    idx = int(price_index) if price_index is not None else 0
    return prices[idx] if idx < len(prices) else prices[0]


# ── Orca via ScraperAPI ───────────────────────────────────────────────────────

# Direct product page URLs (more reliable than category page)
ORCA_URLS = {
    0: "https://www.orca.com/es-es/neopreno-freedive-zen-hombre",
    1: "https://www.orca.com/es-es/neopreno-freedive-mantra-hombre",
}

def fetch_orca_price(price_index=0):
    idx         = int(price_index) if price_index is not None else 0
    product_url = ORCA_URLS.get(idx, ORCA_URLS[0])

    html = fetch_with_scraperapi(product_url, render_js=True)
    if html:
        prices = extract_prices_from_html(html)
        if prices:
            print(f"  [Orca] prices from ScraperAPI: {prices}")
            return prices[0]
        else:
            print(f"  [Orca] ScraperAPI returned HTML but no prices found")
            # Debug: show first 500 chars to help diagnose
            print(f"  [Orca] HTML preview: {html[:300]}")

    # Fallback: try the category page
    category_url = "https://www.orca.com/es-es/hombre/neoprenos/apnea"
    html = fetch_with_scraperapi(category_url, render_js=True)
    if html:
        prices = extract_prices_from_html(html)
        if prices:
            print(f"  [Orca category] prices: {prices}")
            return prices[idx] if idx < len(prices) else prices[0]

    return None


# ── Generic fallback ──────────────────────────────────────────────────────────

def fetch_generic(url, price_index=0):
    # Try direct with realistic browser headers
    for ua in [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 Version/17.4 Safari/605.1.15",
    ]:
        try:
            r = requests.get(url, timeout=25, headers={
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "es-ES,es;q=0.9,fr;q=0.8,en;q=0.7",
            })
            if r.status_code == 200 and len(r.text) > 1000:
                prices = extract_prices_from_html(r.text)
                if prices:
                    idx = int(price_index) if price_index is not None else 0
                    return prices[idx] if idx < len(prices) else prices[0]
        except Exception:
            pass

    # Fallback to ScraperAPI
    html = fetch_with_scraperapi(url)
    if html:
        prices = extract_prices_from_html(html)
        if prices:
            idx = int(price_index) if price_index is not None else 0
            return prices[idx] if idx < len(prices) else prices[0]

    return None


# ── Router ────────────────────────────────────────────────────────────────────

def get_price(url, css_selector=None, price_index=0):
    print(f"  URL: {url}")

    # Shopify (always try .json endpoint first)
    if "/products/" in url:
        try:
            handle = url.rstrip('/').split('/products/')[-1].split('?')[0]
            domain = url.split('/products/')[0]
            test   = requests.get(f"{domain}/products/{handle}.json", timeout=10,
                                  headers={"User-Agent": "Mozilla/5.0"})
            if test.status_code == 200 and '"product"' in test.text:
                return fetch_shopify_price(url, price_index)
        except Exception:
            pass

    # Orca → always use ScraperAPI
    if "orca.com" in url:
        return fetch_orca_price(price_index)

    # Generic
    return fetch_generic(url, price_index)


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(subject, body_html):
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    to_addr   = os.environ["NOTIFY_EMAIL"]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = to_addr
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    with smtplib.SMTP(smtp_host, smtp_port) as srv:
        srv.starttls()
        srv.login(smtp_user, smtp_pass)
        srv.sendmail(smtp_user, to_addr, msg.as_string())
    print(f"  Email sent to {to_addr}")


def build_email_body(changes):
    rows = ""
    for c in changes:
        diff  = c["new"] - c["old"]
        pct   = (diff / c["old"] * 100) if c["old"] else 0
        label = "DROP" if diff < 0 else "RISE"
        color = "#437a22" if diff < 0 else "#a12c7b"
        arrow = "v" if diff < 0 else "^"
        rows += (
            "<tr><td style='padding:10px 16px;border-bottom:1px solid #e5e7eb'>"
            "<a href='" + c["url"] + "' style='color:#01696f;font-weight:600;text-decoration:none'>"
            + c["name"] + "</a></td>"
            "<td style='padding:10px 16px;border-bottom:1px solid #e5e7eb;text-align:center'>"
            + f"{c['old']:.2f}" + " EUR</td>"
            "<td style='padding:10px 16px;border-bottom:1px solid #e5e7eb;text-align:center;"
            "font-weight:700;color:" + color + "'>" + f"{c['new']:.2f}" + " EUR</td>"
            "<td style='padding:10px 16px;border-bottom:1px solid #e5e7eb;text-align:center'>"
            + arrow + " " + label + " " + f"{abs(pct):.1f}" + "%</td></tr>"
        )
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
        "<body style='font-family:system-ui,sans-serif;background:#f7f6f2;margin:0;padding:24px'>"
        "<div style='max-width:680px;margin:0 auto;background:#fff;border-radius:12px;"
        "box-shadow:0 4px 16px rgba(0,0,0,.08);overflow:hidden'>"
        "<div style='background:#01696f;padding:24px 32px'>"
        "<h1 style='color:#fff;margin:0;font-size:20px'>Price Monitor Alert</h1>"
        "<p style='color:#cedcd8;margin:4px 0 0;font-size:14px'>" + ts + "</p></div>"
        "<div style='padding:24px 32px'>"
        "<p style='color:#28251d;margin:0 0 20px'>Price changes detected for <strong>"
        + str(len(changes)) + "</strong> product(s):</p>"
        "<table style='width:100%;border-collapse:collapse;font-size:14px'>"
        "<thead><tr style='background:#f3f0ec'>"
        "<th style='padding:10px 16px;text-align:left;color:#7a7974'>Product</th>"
        "<th style='padding:10px 16px;text-align:center;color:#7a7974'>Old Price</th>"
        "<th style='padding:10px 16px;text-align:center;color:#7a7974'>New Price</th>"
        "<th style='padding:10px 16px;text-align:center;color:#7a7974'>Change</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table></div>"
        "<div style='padding:16px 32px;background:#f7f6f2;border-top:1px solid #e5e7eb'>"
        "<p style='color:#7a7974;font-size:12px;margin:0'>Sent by your GitHub Actions Price Monitor</p>"
        "</div></div></body></html>"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not PRODUCTS_FILE.exists():
        print("products.json not found.")
        sys.exit(0)

    products = json.loads(PRODUCTS_FILE.read_text())
    history  = json.loads(PRICES_FILE.read_text()) if PRICES_FILE.exists() else {}
    changes  = []
    now_str  = datetime.now(timezone.utc).isoformat()

    for product in products:
        if product.get("active") is False:
            print(f"Skipping (paused): {product['name']}")
            continue

        pid         = product["id"]
        name        = product["name"]
        url         = product["url"]
        selector    = product.get("css_selector")
        price_index = product.get("price_index", 0)
        threshold   = float(product.get("threshold_pct", 2))

        print(f"\nChecking: {name}")
        price = get_price(url, selector, price_index)

        if price is None:
            print(f"  ERROR: Could not extract price for {name}")
            continue

        print(f"  Current price: {price:.2f} EUR")
        entry = {"price": price, "checked_at": now_str}

        if pid not in history:
            print(f"  First check - recording {price:.2f} EUR as baseline")
            history[pid] = {"name": name, "url": url, "history": [entry]}
        else:
            old_price = history[pid]["history"][-1]["price"]
            history[pid]["history"].append(entry)
            history[pid]["history"] = history[pid]["history"][-90:]
            diff_pct = abs(price - old_price) / old_price * 100 if old_price else 0
            if diff_pct > max(threshold, 0.01):
                print(f"  ALERT: {old_price:.2f} -> {price:.2f} EUR ({diff_pct:.1f}%)")
                changes.append({"name": name, "url": url, "old": old_price, "new": price})
            else:
                print(f"  No change (was {old_price:.2f}, threshold: {threshold}%)")

        time.sleep(5)

    PRICES_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False))
    print(f"\nSaved price history.")

    if changes:
        try:
            send_email(
                f"[Price Monitor] {len(changes)} price change(s) detected",
                build_email_body(changes)
            )
        except KeyError as e:
            print(f"Email not sent - missing env var: {e}", file=sys.stderr)
    else:
        print("No price changes detected.")


if __name__ == "__main__":
    main()
