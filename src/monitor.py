#!/usr/bin/env python3
"""Price Monitor - checks product prices and sends email alerts on change."""

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

BASE_DIR = Path(__file__).parent.parent
PRODUCTS_FILE = BASE_DIR / "products.json"
PRICES_FILE   = BASE_DIR / "prices.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,fr-FR;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
}

# Cache soup objects so pages shared by multiple products are only fetched once
_SOUP_CACHE = {}


def clean_price(text):
    text = str(text).strip()
    text = re.sub(r'[€$£¥₹\xa0\u202f\u00a0]', ' ', text)
    # Remove thousands separators
    text = re.sub(r'(\d)[.](\d{3})(?:[,\s]|$)', r'\1\2 ', text)
    text = re.sub(r'(\d)[,](\d{3})(?:[.\s]|$)', r'\1\2 ', text)
    text = text.replace(',', '.')
    m = re.search(r'\d+(?:\.\d{1,2})?', text)
    return float(m.group()) if m else None


def fetch_soup(url):
    if url not in _SOUP_CACHE:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        _SOUP_CACHE[url] = BeautifulSoup(resp.text, "html.parser")
    return _SOUP_CACHE[url]


def extract_prices(soup, url=""):
    """Return sorted list of all detected prices on the page."""
    prices = []
    seen   = set()

    # 1. Schema.org / JSON-LD (most reliable)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                for key in ("price", "lowPrice", "highPrice"):
                    val = item.get("offers", {}).get(key) or item.get(key)
                    if val:
                        p = clean_price(str(val))
                        if p and 1 < p < 100000 and p not in seen:
                            prices.append(p); seen.add(p)
        except Exception:
            pass

    # 2. Amazon selectors
    if "amazon." in url:
        for sel in ["#priceblock_ourprice", "#priceblock_dealprice",
                    ".a-price .a-offscreen", "#price_inside_buybox",
                    "#newBuyBoxPrice", "#corePrice_feature_div .a-offscreen"]:
            el = soup.select_one(sel)
            if el:
                p = clean_price(el.get_text())
                if p and p not in seen:
                    prices.append(p); seen.add(p)

    # 3. Common CSS selectors
    for sel in [
        "[itemprop='price']", "[data-price]",
        ".price", ".price-item", ".product-price", ".current-price",
        ".price__current", ".price__sale", ".price__regular",
        ".amount", ".money", ".woocommerce-Price-amount",
        ".product-card__price", ".c-product-price",
        ".pdp-price", ".final-price", ".sales-price",
    ]:
        for el in soup.select(sel):
            # Skip elements that contain child price elements (avoid double-counting)
            if el.select(sel):
                continue
            p = clean_price(el.get_text())
            if p and 1 < p < 100000 and p not in seen:
                prices.append(p); seen.add(p)

    # 4. Regex fallback over raw text
    if not prices:
        for m in re.finditer(
            r'(\d{1,4}(?:[.,]\d{3})*[.,]\d{2})\s*€|€\s*(\d{1,4}(?:[.,]\d{3})*[.,]\d{2})',
            soup.get_text()
        ):
            raw = m.group(1) or m.group(2)
            p = clean_price(raw)
            if p and 1 < p < 100000 and p not in seen:
                prices.append(p); seen.add(p)

    return sorted(prices)


def get_price(url, css_selector=None, price_index=0):
    try:
        soup = fetch_soup(url)

        if css_selector:
            el = soup.select_one(css_selector)
            if el:
                return clean_price(el.get_text())

        prices = extract_prices(soup, url)
        if not prices:
            return None

        idx = int(price_index) if price_index is not None else 0
        return prices[idx] if idx < len(prices) else prices[0]

    except Exception as e:
        print(f"  WARNING: Error fetching {url}: {e}", file=sys.stderr)
        return None


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
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_addr, msg.as_string())
    print(f"  Email sent to {to_addr}")


def build_email_body(changes):
    rows = ""
    for c in changes:
        diff = c["new"] - c["old"]
        pct  = (diff / c["old"] * 100) if c["old"] else 0
        direction = "DROP" if diff < 0 else "RISE"
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
            + arrow + " " + direction + " " + f"{abs(pct):.1f}" + "%</td>"
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
        "</div>"
        "<div style='padding:24px 32px'>"
        "<p style='color:#28251d;margin:0 0 20px'>Price changes detected for <strong>"
        + str(n) + "</strong> product(s):</p>"
        "<table style='width:100%;border-collapse:collapse;font-size:14px'>"
        "<thead><tr style='background:#f3f0ec'>"
        "<th style='padding:10px 16px;text-align:left;color:#7a7974'>Product</th>"
        "<th style='padding:10px 16px;text-align:center;color:#7a7974'>Old Price</th>"
        "<th style='padding:10px 16px;text-align:center;color:#7a7974'>New Price</th>"
        "<th style='padding:10px 16px;text-align:center;color:#7a7974'>Change</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
        "</div>"
        "<div style='padding:16px 32px;background:#f7f6f2;border-top:1px solid #e5e7eb'>"
        "<p style='color:#7a7974;font-size:12px;margin:0'>Sent by your GitHub Actions Price Monitor</p>"
        "</div></div></body></html>"
    )


def main():
    if not PRODUCTS_FILE.exists():
        print("products.json not found - nothing to monitor.")
        sys.exit(0)

    products  = json.loads(PRODUCTS_FILE.read_text())
    history   = json.loads(PRICES_FILE.read_text()) if PRICES_FILE.exists() else {}
    changes   = []
    now_str   = datetime.now(timezone.utc).isoformat()

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

        print(f"Checking: {name}")
        price = get_price(url, selector, price_index)

        if price is None:
            print(f"  ERROR: Could not extract price for {name}")
            continue

        print(f"  Current price: {price:.2f} EUR")
        entry = {"price": price, "checked_at": now_str}

        if pid not in history:
            print(f"  First check - recording {price:.2f} EUR")
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

        time.sleep(3)

    PRICES_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False))
    print("\nSaved price history.")

    if changes:
        try:
            send_email(
                f"[Price Monitor] {len(changes)} price change(s) detected",
                build_email_body(changes)
            )
        except KeyError as e:
            print(f"Email not sent - missing env var: {e}", file=sys.stderr)
            for c in changes:
                print(f"  * {c['name']}: {c['old']:.2f} -> {c['new']:.2f} EUR")
    else:
        print("No price changes detected.")


if __name__ == "__main__":
    main()
