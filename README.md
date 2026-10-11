# Security Job Tracker

Checks big company career sites every morning for security analyst and engineer roles that fit your profile, sends new matches to your phone, and keeps a searchable dashboard page up to date.

## What it covers

| How | Companies |
|---|---|
| Read automatically | Amazon, NVIDIA, Adobe, Salesforce, Dell, Intel, Qualcomm, Mastercard, PayPal, Walmart, Cloudflare, Datadog, Okta |
| One-click pre-filtered search on the dashboard | Microsoft, Google, Meta, IBM, Apple, Accenture, Oracle, Capgemini |

Microsoft, Google, Meta and IBM do not offer a public feed that a script can read reliably, so the dashboard gives you ready-made search links for them instead of guessing. Edit the `links` list in `config.yaml` to add more.

If any automatic source breaks (a company changes its site), the dashboard's "Source status" section says so and keeps showing that company's earlier results. Nothing fails silently.

## Set it up (about 10 minutes)

1. **Create a GitHub repository** and upload this folder to it.
2. **Phone alerts (free, no account):** install the **ntfy** app, subscribe to a long random topic name (for example `saran-jobs-8f3k29x`). In the repo go to Settings, Secrets and variables, Actions, and add a secret `NTFY_TOPIC` with that same name.
3. **Optional email:** add secrets `SMTP_USER` (your Gmail address), `SMTP_PASS` (a Gmail app password) and optionally `EMAIL_TO`.
4. **Optional Telegram:** add secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
5. **Dashboard page:** Settings, Pages, deploy from branch `main`, folder `/docs`. Then add a repository *variable* (not secret) `DASHBOARD_URL` with the page address so notifications link to it.
6. **First run:** Actions tab, "Daily job check", Run workflow. After that it runs by itself every day at 09:00 India time.

The first run sends one summary of the best current matches. After that you only hear about jobs that are new.

## Run it on your own computer

```
pip install -r requirements.txt
python -m tracker.main --dry-run      # fetch and print results, save nothing, notify nobody
python -m tracker.main                # real run
NTFY_TOPIC=your-topic python -m tracker.main --test-notify
```

## Tuning

Everything is in `config.yaml`: wanted job titles and their weights, words that exclude a title (senior, lead, manager...), skills that add points, cities, the maximum years of experience a job may ask for (default 3), how old a posting may be (default 30 days), and the minimum score. If you get too many or too few matches, change `min_score` first.

## Tests

```
pip install pytest
python -m pytest
```
