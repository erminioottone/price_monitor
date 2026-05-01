#!/usr/bin/env python3
"""Price Monitor - fetches prices via JSON APIs and sends email alerts."""

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

BASE_DIR      = Path(__file__).parent.parent
PRODUCTS_FILE = BASE_DIR / "products.json"
PRICES_FILE   = BASE_DIR / "prices.json"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "es-ES,es;q=0.9,fr;q=0.8,en;q=0.7",
})


def clean_price(value):
    """Convert any price representation to float."""
    s = str(value).strip()
    s = re.sub(r'[€$£\s\xa0\u202f]', '', s)
    s = re.sub(r'(\d)[.](\d{3})(?:[,]|$)', r'\1\2', s)
    s = s.replace(',', '.')
    m = re.search(r'\d+(?:\.\d{1,2})?', s)
    return float(m.group()) if m else None


# ── Shopify JSON endpoint ────────────────────────────────────────────────────

def fetch_shopify_price(url, price_index=0):
    """
    Shopify stores expose /products/<handle>.json publicly.
    Convert the product URL to the JSON endpoint and read variants[0].price.
    """
    # Extract handle: last path segment, strip query/hash
    handle = url.rstrip('/').split('/')[-1].split('?')[0].split('#')[0]
    domain = url.split('/products/')[0]
    json_url = f"{domain}/products/{handle}.json"

    print(f"  [Shopify JSON] {json_url}")
    resp = SESSION.get(json_url, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    variants = data.get("product", {}).get("variants", [])
    if not variants:
        return None

    # Collect all unique prices sorted ascending
    prices = sorted(set(
        float(v["price"]) for v in variants if v.get("price")
    ))
    print(f"  [Shopify] prices found: {prices}")
    idx = int(price_index) if price_index is not None else 0
    return prices[idx] if idx < len(prices) else prices[0]


# ── Orca — try their API / JSON feed ────────────────────────────────────────

def fetch_orca_category(url, price_index=0):
    """
    Orca uses a Salesforce Commerce Cloud backend.
    We try their storefront JSON endpoint for the category.
    Falls back to scraping with a Playwright-style UA if needed.
    """
    # Method 1: OCAPI / storefront JSON
    # Orca ES store category ID for freedive wetsuits
    category_slug = url.rstrip('/').split('/')[-1]
    api_attempts = [
        # Storefront open catalogue endpoint (no auth needed for prices)
        f"https://www.orca.com/on/demandware.store/Sites-Orca_ES-Site/es_ES/Search-Show?cgid={category_slug}&format=ajax&sz=24",
        f"https://www.orca.com/on/demandware.store/Sites-Orca_ES-Site/es_ES/Category-Show?cgid={category_slug}&format=page-element",
    ]

    for api_url in api_attempts:
        try:
            print(f"  [Orca API] trying {api_url}")
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Referer": "https://www.orca.com/",
                "Accept-Language": "es-ES,es;q=0.9",
                "X-Requested-With": "XMLHttpRequest",
            }
            r = SESSION.get(api_url, headers=headers, timeout=20)
            if r.status_code == 200:
                # Look for JSON price data embedded in response
                prices = []
                # Try JSON first
                try:
                    data = r.json()
                    text = json.dumps(data)
                except Exception:
                    text = r.text

                # Extract prices from JSON or HTML
                for m in re.finditer(r'"(?:price|salesPrice|listPrice)"\s*[:\s]+["\s]*(\d+(?:[.,]\d{1,2})?)', text):
                    p = clean_price(m.group(1))
                    if p and 10 < p < 5000:
                        prices.append(p)

                if not prices:
                    # Fallback: look for EUR price patterns in HTML
                    for m in re.finditer(r'(\d{2,4})[.,](\d{2})\s*(?:€|EUR)', text):
                        p = clean_price(f"{m.group(1)}.{m.group(2)}")
                        if p and 10 < p < 5000:
                            prices.append(p)

                prices = sorted(set(prices))
                if prices:
                    print(f"  [Orca] prices found: {prices}")
                    idx = int(price_index) if price_index is not None else 0
                    return prices[idx] if idx < len(prices) else prices[0]
        except Exception as e:
            print(f"  [Orca] attempt failed: {e}", file=sys.stderr)

    # Method 2: Direct product pages with known slugs
    orca_products = [
        ("https://www.orca.com/es-es/hombre/neoprenos/apnea/zen", 0),
        ("https://www.orca.com/es-es/hombre/neoprenos/apnea/mantra", 0),
    ]
    for prod_url, _ in orca_products:
        try:
            print(f"  [Orca direct] {prod_url}")
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "es-ES,es;q=0.9",
            }
            r = SESSION.get(prod_url, headers=headers, timeout=20)
            if r.status_code == 200:
                # Look for JSON-LD structured data
                for m in re.finditer(r'"price"\s*:\s*"?(\d+(?:[.,]\d{1,2})?)"?', r.text):
                    p = clean_price(m.group(1))
                    if p and 10 < p < 5000:
                        print(f"  [Orca direct] found price {p}")
                        return p
        except Exception as e:
            print(f"  [Orca direct] failed: {e}", file=sys.stderr)

    return None


# ── Generic fallback with rotating UAs ──────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

def fetch_generic(url, price_index=0):
    from bs4 import BeautifulSoup
    for ua in USER_AGENTS:
        try:
            headers = {
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                "Accept-Language": "es-ES,es;q=0.9,fr;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
            r = SESSION.get(url, headers=headers, timeout=25)
            if r.status_code == 403:
                continue
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            prices = []
            seen = set()

            # JSON-LD
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        for key in ("price", "lowPrice"):
                            val = (item.get("offers") or {}).get(key) or item.get(key)
                            if val:
                                p = clean_price(str(val))
                                if p and 1 < p < 100000 and p not in seen:
                                    prices.append(p); seen.add(p)
                except Exception:
                    pass

            # CSS selectors
            for sel in ["[itemprop='price']", "[data-price]", ".price", ".price-item",
                        ".product-price", ".current-price", ".price__current",
                        ".amount", ".money", ".woocommerce-Price-amount"]:
                for el in soup.select(sel):
                    if el.select(sel): continue
                    p = clean_price(el.get_text())
                    if p and 1 < p < 100000 and p not in seen:
                        prices.append(p); seen.add(p)

            prices = sorted(prices)
            if prices:
                idx = int(price_index) if price_index is not None else 0
                return prices[idx] if idx < len(prices) else prices[0]
        except Exception as e:
            print(f"  [generic UA={ua[:30]}] error: {e}", file=sys.stderr)
    return None


# ── Router ───────────────────────────────────────────────────────────────────

def get_price(url, css_selector=None, price_index=0):
    print(f"  URL: {url}")

    # Shopify stores
    if "/products/" in url and "shopify" not in url:
        # Check if it's a Shopify store by trying JSON endpoint
        try:
            handle = url.rstrip('/').split('/products/')[-1].split('?')[0]
            domain = url.split('/products/')[0]
            test = SESSION.get(f"{domain}/products/{handle}.json", timeout=10)
            if test.status_code == 200 and "product" in test.text:
                return fetch_shopify_price(url, price_index)
        except Exception:
            pass

    # Orca
    if "orca.com" in url:
        return fetch_orca_category(url, price_index)

    # Generic
    return fetch_generic(url, price_index)


# ── Email ────────────────────────────────────────────────────────────────────

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
            "<tr>"
            "<td style='padding:10px 16px;border-bottom:1px solid #e5e7eb'>"
            "<a href='" + c["url"] + "' style='color:#01696f;font-weight:600;text-decoration:none'>"
            + c["name"] + "</a></td>"
            "<td style='padding:10px 16px;border-bottom:1px solid #e5e7eb;text-align:center'>"
            + f"{c['old']:.2f}" + " EUR</td>"
            "<td style='padding:10px 16px;border-bottom:1px solid #e5e7eb;text-align:center;"
            "font-weight:700;color:" + color + "'>" + f"{c['new']:.2f}" + " EUR</td>"
            "<td style='padding:10px 16px;border-bottom:1px solid #e5e7eb;text-align:center'>"
            + arrow + " " + label + " " + f"{abs(pct):.1f}" + "%</td>"
            "</tr>"
        )
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    n  = len(changes)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
        "<body style='font-family:system-ui,sans-serif;background:#f7f6f2;margin:0;padding:24px'>"
        "<div style='max-width:680px;margin:0 auto;background:#fff;border-radius:12px;"
        "box-shadow:0 4px 16px rgba(0,0,0,.08);overflow:hidden'>"
        "<div style='background:#01696f;padding:24px 32px'>"
        "<h1 style='color:#fff;margin:0;font-size:20px'>Price Monitor Alert</h1>"
        "<p style='color:#cedcd8;margin:4px 0 0;font-size:14px'>" + ts + "</p>"
        "</div><div style='padding:24px 32px'>"
        "<p style='color:#28251d;margin:0 0 20px'>Price changes detected for <strong>"
        + str(n) + "</strong> product(s):</p>"
        "<table style='width:100%;border-collapse:collapse;font-size:14px'>"
        "<thead><tr style='background:#f3f0ec'>"
        "<th style='padding:10px 16px;text-align:left;color:#7a7974'>Product</th>"
        "<th style='padding:10px 16px;text-align:center;color:#7a7974'>Old Price</th>"
        "<th style='padding:10px 16px;text-align:center;color:#7a7974'>New Price</th>"
        "<th style='padding:10px 16px;text-align:center;color:#7a7974'>Change</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
        "</div><div style='padding:16px 32px;background:#f7f6f2;border-top:1px solid #e5e7eb'>"
        "<p style='color:#7a7974;font-size:12px;margin:0'>Sent by your GitHub Actions Price Monitor</p>"
        "</div></div></body></html>"
    )


# ── Main ─────────────────────────────────────────────────────────────────────

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
                print(f"  No significant change (was {old_price:.2f}, threshold: {threshold}%)")

        time.sleep(5)

    PRICES_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False))
    print(f"\nSaved price history to prices.json")

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
