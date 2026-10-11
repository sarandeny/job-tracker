"""Send push, email or Telegram alerts. Each channel only runs if its settings exist."""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import requests

from .sources import Job

log = logging.getLogger("tracker")


def build_digest(
    jobs: list[Job], max_items: int, dashboard_url: str = "", initial: bool = False
) -> tuple[str, str]:
    """Return (title, body). Title is ASCII so it is safe in an HTTP header."""
    if not jobs:
        body = "No matching jobs right now. You will get an alert when new ones appear."
        if dashboard_url:
            body += f"\n\nDashboard: {dashboard_url}"
        return "Job tracker is live: no matches yet", body
    top = sorted(jobs, key=lambda j: j.score, reverse=True)[:max_items]
    if initial:
        title = f"Job tracker is live: {len(jobs)} matches found"
    else:
        title = f"{len(jobs)} new security job match" + ("" if len(jobs) == 1 else "es")
    lines = [f"- {j.company}: {j.title} ({j.location or 'location n/a'})\n  {j.url}" for j in top]
    if len(jobs) > len(top):
        lines.append(f"...and {len(jobs) - len(top)} more.")
    if dashboard_url:
        lines.append(f"\nAll matches: {dashboard_url}")
    return title, "\n".join(lines)


def send_ntfy(title: str, body: str, click: str = "", env=os.environ) -> bool:
    topic = env.get("NTFY_TOPIC")
    if not topic:
        return False
    server = env.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {"Title": title, "Tags": "briefcase"}
    if click:
        headers["Click"] = click
    r = requests.post(f"{server}/{topic}", data=body.encode("utf-8"), headers=headers, timeout=20)
    r.raise_for_status()
    return True


def send_email(title: str, body: str, env=os.environ) -> bool:
    user, password = env.get("SMTP_USER"), env.get("SMTP_PASS")
    if not (user and password):
        return False
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = user
    msg["To"] = env.get("EMAIL_TO") or user
    msg.set_content(body)
    host = env.get("SMTP_HOST", "smtp.gmail.com")
    port = int(env.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=30) as s:
        s.login(user, password)
        s.send_message(msg)
    return True


def send_telegram(title: str, body: str, env=os.environ) -> bool:
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": f"{title}\n\n{body}", "disable_web_page_preview": True},
        timeout=20,
    )
    r.raise_for_status()
    return True


def send_all(title: str, body: str, click: str = "", env=os.environ) -> list[str]:
    """Try every configured channel. Returns the names that succeeded."""
    sent = []
    for name, fn in (
        ("ntfy", lambda: send_ntfy(title, body, click, env)),
        ("email", lambda: send_email(title, body, env)),
        ("telegram", lambda: send_telegram(title, body, env)),
    ):
        try:
            if fn():
                sent.append(name)
        except Exception as e:  # one broken channel must not block the others
            log.warning("notify via %s failed: %s", name, e)
    return sent
