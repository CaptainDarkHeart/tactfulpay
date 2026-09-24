"""Slack webhook alerts for DISPUTE/HOSTILE flags and other notifications.

Sends structured messages to a Slack channel when the agent needs
human attention — disputes, hostile responses, phase 4 exhaustion.
"""

from __future__ import annotations

import requests

from src.config import settings


def send_alert(
    title: str,
    message: str,
    invoice_number: str | None = None,
    debtor_company: str | None = None,
    severity: str = "warning",
) -> bool:
    """Send an alert to the configured Slack webhook.

    Args:
        title: Alert title (e.g., "DISPUTE Detected").
        message: Description of what happened.
        invoice_number: Related invoice number.
        debtor_company: The debtor's company name.
        severity: "warning" (yellow), "critical" (red), or "info" (blue).

    Returns:
        True if the webhook returned 200.
    """
    if not settings.slack_webhook_url:
        return False

    color_map = {"warning": "#FFA500", "critical": "#FF0000", "info": "#0088FF"}
    color = color_map.get(severity, "#FFA500")

    fields = []
    if invoice_number:
        fields.append({"title": "Invoice", "value": f"#{invoice_number}", "short": True})
    if debtor_company:
        fields.append({"title": "Debtor", "value": debtor_company, "short": True})

    payload = {
        "attachments": [
            {
                "color": color,
                "title": title,
                "text": message,
                "fields": fields,
                "footer": "TactfulPay",
            }
        ]
    }

    try:
        resp = requests.post(settings.slack_webhook_url, json=payload, timeout=10)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def notify_dispute(invoice_number: str, debtor_company: str, reply_excerpt: str) -> bool:
    """Alert: Invoice disputed — agent paused, human must review."""
    return send_alert(
        title="DISPUTE Detected — Agent Paused",
        message=(
            f"The debtor has disputed this invoice. The agent has stopped all "
            f"automated outreach. Please review the reply and clear the flag "
            f"in the dashboard before the agent re-engages.\n\n"
            f"Reply excerpt: _{reply_excerpt[:200]}_"
        ),
        invoice_number=invoice_number,
        debtor_company=debtor_company,
        severity="critical",
    )


def notify_hostile(invoice_number: str, debtor_company: str, reply_excerpt: str) -> bool:
    """Alert: Hostile response — agent paused, do NOT respond."""
    return send_alert(
        title="HOSTILE Response — Agent Paused",
        message=(
            f"The debtor's response has been classified as hostile. The agent "
            f"has stopped all outreach and will NOT send any further messages. "
            f"Human review required.\n\n"
            f"Reply excerpt: _{reply_excerpt[:200]}_"
        ),
        invoice_number=invoice_number,
        debtor_company=debtor_company,
        severity="critical",
    )


def notify_write_off_claimed(invoice_number: str, debtor_company: str, reply_excerpt: str) -> bool:
    """Alert: Debtor claims the invoice was written off — agent paused, SME must verify."""
    return send_alert(
        title="Write-Off Claimed — Verify with SME",
        message=(
            f"The debtor is claiming that Invoice #{invoice_number} was written off "
            f"or cancelled. The agent has paused all outreach. "
            f"The SME must confirm or deny this via the dashboard.\n\n"
            f"Reply excerpt: _{reply_excerpt[:200]}_"
        ),
        invoice_number=invoice_number,
        debtor_company=debtor_company,
        severity="warning",
    )


def notify_human_review(invoice_number: str, debtor_company: str, reason: str) -> bool:
    """Alert: Invoice flagged for human review (e.g., Phase 4 exhausted)."""
    return send_alert(
        title="Human Review Required",
        message=reason,
        invoice_number=invoice_number,
        debtor_company=debtor_company,
        severity="warning",
    )
