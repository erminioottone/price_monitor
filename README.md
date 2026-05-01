# Price Monitor

Monitors prices automatically via GitHub Actions — checks 4x/day, sends email alerts on changes.

## Currently monitored

| Product | URL | Last known price |
|---------|-----|-----------------|
| Orca Freedive Zen Hombre | https://www.orca.com/es-es/hombre/neoprenos/apnea | 449 EUR |
| Orca Freedive Mantra Hombre | https://www.orca.com/es-es/hombre/neoprenos/apnea | 349 EUR |
| Xero Shoes Scrambler Trail Low WP Men | https://xeroshoes.eu/products/scrambler-trail-low-wp-men | 150 EUR |

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
Runs at 04:00, 10:00, 16:00, 22:00 UTC (~120 min/month = 6% of free tier).

## Adding more products
Open dashboard.html in your browser to manage products visually.
