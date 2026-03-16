import smtplib
import os
import logging
from email.mime.text import MIMEText
from database import get_stats

logger = logging.getLogger(__name__)


def send_sale_notification(date_str, amount, location, platform):
    gmail_user = os.getenv("GMAIL_USER")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_password:
        logger.warning("GMAIL_USER or GMAIL_APP_PASSWORD not set — skipping email notification")
        return

    stats = get_stats()

    lines = [
        "Hey! Here are the current stats for your Sandbag Holder sales!",
        "",
        "── New Sale ──────────────────────────",
        f"  Date:      {date_str}",
        f"  Amount:    ${amount:.2f}",
        f"  Location:  {location}",
        f"  Platform:  {platform}",
        "",
        "── Overall ───────────────────────────",
        f"  Total Revenue:  ${stats['total_revenue']:.2f}",
        f"  Total Sales:    {stats['total_sales']}",
        f"  Average Sale:   ${stats['avg_sale']:.2f}",
        "",
        "── This Month ────────────────────────",
        f"  Revenue:        ${stats['this_month_revenue']:.2f}",
        f"  Sales:          {stats['this_month_sales']}",
    ]

    if stats["by_platform"]:
        top = stats["by_platform"][0]
        lines += ["", f"  Top Platform:   {top['platform']} (${top['revenue']:.2f}, {top['count']} sales)"]
    if stats["by_location"]:
        top = stats["by_location"][0]
        lines += [f"  Top Location:   {top['location']} (${top['revenue']:.2f}, {top['count']} sales)"]

    body = "\n".join(lines)
    subject = f"New {platform} Sale — ${amount:.2f} in {location}"

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = gmail_user

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_user, gmail_password)
            smtp.send_message(msg)
        logger.info("Sale notification email sent")
    except Exception as e:
        logger.error(f"Email notification failed: {e}")
