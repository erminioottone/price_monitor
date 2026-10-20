# Price Monitor

Monitors prices via GitHub Actions — runs every Friday and sends an email report only when a price drops.

## Currently monitored

| Product | URL | Last known price |
|---------|-----|-----------------|
| Molchanovs CORE Silicone Bifins | https://www.molchanovs.com/collections/freediving-fins/products/core-silicone-bifins | 125.00 USD |

## One-time setup (10 minutes)

### 1. Create a private GitHub repo named `price-monitor`

### 2. Upload ALL files from this ZIP keeping the folder structure

### 3. Add 5 repository secrets
Settings > Secrets and variables > Actions > New repository secret:

| Secret | Value |
|--------|-------|
| SMTP_HOST | smtp.gmail.com |
| SMTP_PORT | 587 |
| SMTP_USER | your-gmail@gmail.com |
| SMTP_PASS | Gmail App Password (16-char) |
| NOTIFY_EMAIL | email to receive alerts |

### 4. Gmail App Password
Enable 2FA then go to https://myaccount.google.com/apppasswords

### 5. Test it
GitHub repo > Actions > "Price Monitor" > "Run workflow"

## Schedule
Runs every Friday at 09:00 UTC (11:00 Paris time). Sends an email only if the price dropped since the previous check. The first run after adding a product records its baseline price without sending an email.

## Adding more products
Open dashboard.html in your browser to manage products visually.
