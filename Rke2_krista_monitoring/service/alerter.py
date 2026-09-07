"""
Alert engine — evaluates custom metric threshold rules from the DB,
then routes notifications to the correct webhook channel based on category.

Notification channels are configured from UI — no hardcoded webhooks.
Each channel has a name, webhook URL, and category filter.
"""

import logging
import os
import re
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List
from urllib.parse import parse_qs, unquote, urlparse

import requests

log = logging.getLogger(__name__)

COOLDOWN = int(os.environ.get("ALERT_COOLDOWN", "300"))

_OPERATORS = {
    ">": lambda v, t: v > t,
    ">=": lambda v, t: v >= t,
    "<": lambda v, t: v < t,
    "<=": lambda v, t: v <= t,
    "==": lambda v, t: v == t,
    "!=": lambda v, t: v != t,
}


class Alerter:
    def __init__(self, store=None):
        self.store = store
        self._fired: Dict[str, float] = {}

    def evaluate(self, cluster_id: str, cluster_name: str, report: Dict[str, Any]) -> List[Dict]:
        if not self.store:
            return []

        rules = self.store.get_alert_rules()
        enabled_rules = [r for r in rules if r.get("enabled", True)]
        # Map rule_name -> channels for resolved-alert routing (covers disabled rules too)
        rule_channels_by_name = {r["name"]: r.get("channels", "") for r in rules}
        if not enabled_rules:
            return []

        alerts = []
        now = time.time()
        timestamp = report.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        fired_keys = set()

        # Pre-fetch active alerts so the rule loop can detect "DB says resolved
        # but metric is still tripping" — that signals we should bypass the
        # in-memory cooldown and re-fire (otherwise the alert is stuck silent).
        active_pre = self.store.get_active_alerts(cluster_id=cluster_id) if self.store else []
        active_keys_pre = {f"{a.get('check_name', '')}:{a.get('item_name', '')}" for a in active_pre}

        for check in report.get("checks", []):
            for item in check.get("items", []):
                metrics = item.get("metrics", {})
                for rule in enabled_rules:
                    metric_name = rule.get("metric_name", "")
                    if metric_name not in metrics:
                        continue

                    value = metrics[metric_name]
                    if not isinstance(value, (int, float)):
                        continue

                    pattern = rule.get("item_pattern", "")
                    if pattern:
                        try:
                            if not re.search(pattern, item.get("name", "")):
                                continue
                        except re.error:
                            pass

                    op_fn = _OPERATORS.get(rule.get("operator", ">"), _OPERATORS[">"])
                    critical_t = rule.get("critical")
                    warning_t = rule.get("warning")

                    severity = None
                    threshold = None
                    if critical_t is not None and op_fn(value, critical_t):
                        severity = "critical"
                        threshold = critical_t
                    elif warning_t is not None and op_fn(value, warning_t):
                        severity = "warning"
                        threshold = warning_t

                    if not severity:
                        continue

                    notify = rule.get("notify", "both")
                    if notify == "none":
                        continue
                    if notify == "critical" and severity == "warning":
                        continue

                    # Always mark as fired (for auto-resolve tracking)
                    rule_item_key = f"{rule['name']}:{item.get('name', '')}"
                    fired_keys.add(rule_item_key)

                    key = f"{rule['id']}:{item.get('name', '')}"
                    # Cooldown applies only while the alert is still tracked as
                    # active in the DB. If the DB has no active row for this
                    # rule+item (e.g. it was resolved or cleared), re-fire so a
                    # fresh notification goes out — otherwise the alert can be
                    # silently stuck above threshold forever.
                    in_cooldown = key in self._fired and (now - self._fired[key]) < COOLDOWN
                    has_active_row = rule_item_key in active_keys_pre
                    if in_cooldown and has_active_row:
                        self._fired[key] = now  # Keep the timer fresh
                        continue

                    msg_template = rule.get("message") or "{metric} is {value} on {item}"
                    message = msg_template.format(
                        value=round(value, 2) if isinstance(value, float) else value,
                        item=item.get("name", ""), metric=metric_name, threshold=threshold,
                    )

                    alert = {
                        "type": "rule", "rule_name": rule["name"], "rule_id": rule["id"],
                        "cluster_id": cluster_id, "cluster_name": cluster_name,
                        "timestamp": timestamp, "severity": severity,
                        "check_name": check["name"], "category": check.get("category", ""),
                        "item_names": item.get("name", ""),
                        "metric_name": metric_name, "metric_value": value, "threshold": threshold,
                        "message": f"[{severity.upper()}] {rule['name']}: {message}",
                        "summary": message,
                    }
                    alert["_rule_channels"] = rule.get("channels", "")

                    # Send webhook (cooldown already checked above)
                    if key not in self._fired or (now - self._fired[key]) >= COOLDOWN:
                        alerts.append(alert)
                        self._fire_to_channels(alert)
                    self._fired[key] = now

        # Sync DB alerts
        if self.store:
            active = self.store.get_active_alerts(cluster_id=cluster_id)
            active_keys = {f"{a.get('check_name', '')}:{a.get('item_name', '')}": a for a in active}
            for alert in alerts:
                akey = f"{alert.get('rule_name', '')}:{alert.get('item_names', '')}"
                if akey not in active_keys:
                    self.store.save_alert(cluster_id, alert["severity"], alert["rule_name"],
                                          alert.get("item_names", ""), alert["message"])
            for akey, a in active_keys.items():
                if akey not in fired_keys:
                    self.store.resolve_alerts(cluster_id, a.get("check_name", ""), a.get("item_name", ""))
                    # Send RESOLVED notification — reuse the original rule's channel routing
                    rule_name = a.get("check_name", "")
                    resolved_alert = {
                        "severity": "resolved",
                        "rule_name": rule_name,
                        "cluster_name": cluster_name,
                        "category": "",
                        "metric_name": "",
                        "metric_value": "",
                        "threshold": "",
                        "item_names": a.get("item_name", ""),
                        "message": f"[RESOLVED] {rule_name}: {a.get('item_name', '')} is back to normal",
                        "summary": f"{a.get('item_name', '')} is back to normal",
                        "_rule_channels": rule_channels_by_name.get(rule_name, ""),
                    }
                    self._fire_to_channels(resolved_alert)

        return alerts

    # ── Channel-based routing ────────────────────────────────────────

    def _fire_to_channels(self, alert: Dict):
        """Route alert to matching notification channels.
        Priority: rule-specific channels > category-based channels > all channels."""
        if not self.store:
            return

        channels = self.store.get_notification_channels()
        if not channels:
            return

        alert_category = alert.get("category", "")
        rule_channels = alert.get("_rule_channels", "").strip()

        # If rule specifies channels, only send to those (by channel ID or name)
        if rule_channels:
            selected_ids = set()
            selected_names = set()
            for part in rule_channels.split(","):
                part = part.strip()
                if part.isdigit():
                    selected_ids.add(int(part))
                elif part:
                    selected_names.add(part.lower())

            for ch in channels:
                if not ch["enabled"]:
                    continue
                if ch["id"] in selected_ids or ch["name"].lower() in selected_names:
                    self._send_webhook(ch["webhook_url"], ch["name"], alert)
            return

        # Otherwise fall back to category-based routing.
        # If alert has no category (e.g. resolved alert from a deleted rule),
        # skip category filtering and broadcast to all enabled channels.
        for ch in channels:
            if not ch["enabled"]:
                continue
            cat_filter = ch.get("categories", "").strip()
            if cat_filter and alert_category:
                allowed = [c.strip().lower() for c in cat_filter.split(",") if c.strip()]
                if allowed and alert_category.lower() not in allowed:
                    continue
            self._send_webhook(ch["webhook_url"], ch["name"], alert)

    def _send_webhook(self, url: str, channel_name: str, alert: Dict):
        """Send alert to a webhook URL. Auto-detects format."""
        if not url:
            return
        wtype = self._detect_type(url)
        # Email is special — uses SMTP, not HTTP POST
        if wtype == "email":
            self._send_email(url, channel_name, alert)
            return
        payload = getattr(self, f"_build_{wtype}", self._build_generic)(alert, channel_name)
        try:
            resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            if resp.status_code < 300:
                log.info("Alert sent to '%s' via %s: %s", channel_name, wtype, alert.get("rule_name", ""))
            else:
                log.warning("Webhook '%s' failed (%s, HTTP %d): %s", channel_name, wtype, resp.status_code, resp.text[:200])
        except Exception as exc:
            log.warning("Webhook '%s' error (%s): %s", channel_name, wtype, exc)

    def _detect_type(self, url):
        u = url.lower()
        if u.startswith("smtp://") or u.startswith("smtps://"): return "email"
        if "office.com" in u or "microsoft" in u: return "teams"
        if "hooks.slack.com" in u: return "slack"
        if "pagerduty.com" in u: return "pagerduty"
        if "discord" in u: return "discord"
        return "generic"

    # ── Email transport ──────────────────────────────────────────────
    def _send_email(self, url: str, channel_name: str, alert: Dict):
        """
        SMTP transport. URL format:
          smtp://user:pass@smtp.gmail.com:587?from=alerts@x.com&to=ops@y.com,sec@y.com[&tls=1][&starttls=1]
          smtps://user:pass@smtp.example.com:465?from=...&to=...
        """
        try:
            p = urlparse(url)
            host = p.hostname or ""
            port = p.port or (465 if p.scheme == "smtps" else 587)
            user = unquote(p.username) if p.username else ""
            password = unquote(p.password) if p.password else ""
            qs = parse_qs(p.query or "")
            sender = (qs.get("from") or [user])[0]
            to_str = (qs.get("to") or [""])[0]
            recipients = [r.strip() for r in to_str.split(",") if r.strip()]
            if not (host and sender and recipients):
                log.warning("Email channel '%s' missing host/from/to in URL", channel_name)
                return
            use_starttls = (qs.get("starttls", ["1" if p.scheme == "smtp" else "0"])[0] in ("1", "true", "yes"))

            sev = alert.get("severity", "warning")
            subj_prefix = {"resolved": "[RESOLVED]", "critical": "[CRITICAL]", "warning": "[WARNING]", "info": "[INFO]"}.get(sev, "[ALERT]")
            subject = f"{subj_prefix} {alert.get('cluster_name', '')} — {alert.get('rule_name', '')}"
            html_body = self._build_email_html(alert, channel_name)
            text_body = alert.get("message", "")

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = ", ".join(recipients)
            msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            if p.scheme == "smtps":
                with smtplib.SMTP_SSL(host, port, timeout=10) as s:
                    if user and password: s.login(user, password)
                    s.sendmail(sender, recipients, msg.as_string())
            else:
                with smtplib.SMTP(host, port, timeout=10) as s:
                    s.ehlo()
                    if use_starttls:
                        s.starttls()
                        s.ehlo()
                    if user and password: s.login(user, password)
                    s.sendmail(sender, recipients, msg.as_string())
            log.info("Email sent to '%s' (%d recipients) for %s", channel_name, len(recipients), alert.get("rule_name", ""))
        except Exception as exc:
            log.warning("Email channel '%s' error: %s", channel_name, exc)

    def _build_email_html(self, a, channel_name=""):
        sev = a.get("severity", "warning")
        if sev == "resolved":
            color, banner = "#10b981", "RESOLVED"
        elif sev == "critical":
            color, banner = "#ef4444", "CRITICAL ALERT"
        elif sev == "info":
            color, banner = "#3b82f6", "INFO"
        else:
            color, banner = "#f59e0b", "WARNING"
        rows = [
            ("Rule", a.get("rule_name", "")),
            ("Severity", sev.upper()),
            ("Cluster", a.get("cluster_name", "")),
            ("Item", a.get("item_names", "")),
        ]
        if sev not in ("resolved", "info"):
            rows += [
                ("Metric", f"{a.get('metric_name', '')} = {a.get('metric_value', '')}"),
                ("Threshold", str(a.get("threshold", ""))),
            ]
        rows.append(("Summary", a.get("summary", "")))
        rows_html = "".join(f"<tr><td style='padding:6px 12px;color:#6b7280;width:120px;'>{k}</td><td style='padding:6px 12px;color:#111827;font-family:monospace;'>{v}</td></tr>" for k, v in rows)
        return f"""<!doctype html><html><body style="margin:0;padding:24px;background:#f3f4f6;font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
<table cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,0.08);">
<tr><td style="padding:18px 24px;background:{color};color:#fff;font-size:18px;font-weight:600;">{banner} — {a.get('cluster_name', '')}</td></tr>
<tr><td style="padding:20px 24px;color:#1f2937;font-size:14px;">{a.get('message', '')}</td></tr>
<tr><td style="padding:0 24px 18px;"><table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:13px;">{rows_html}</table></td></tr>
<tr><td style="padding:14px 24px;background:#f9fafb;color:#6b7280;font-size:12px;border-top:1px solid #e5e7eb;">Krista Monitoring — channel: {channel_name}</td></tr>
</table></body></html>"""

    def _build_teams(self, a, channel_name=""):
        sev = a.get("severity", "warning")
        if sev == "resolved":
            color = "10B981"
            icon = "&#x2705;"
            title = f"{icon} RESOLVED — {a.get('cluster_name', '')}"
        elif sev == "critical":
            color = "FF0000"
            icon = "&#x1F6A8;"
            title = f"{icon} CRITICAL ALERT — {a.get('cluster_name', '')}"
        else:
            color = "FFA500"
            icon = "&#x26A0;"
            title = f"{icon} WARNING — {a.get('cluster_name', '')}"
        facts = [
            {"name": "Rule", "value": a.get("rule_name", "")},
            {"name": "Severity", "value": sev.upper()},
            {"name": "Item", "value": a.get("item_names", "")},
            {"name": "Summary", "value": a.get("summary", "")},
        ]
        if sev != "resolved":
            facts.insert(2, {"name": "Metric", "value": f"{a.get('metric_name', '')} = {a.get('metric_value', '')}"})
            facts.insert(3, {"name": "Threshold", "value": str(a.get("threshold", ""))})
        return {
            "@type": "MessageCard", "@context": "http://schema.org/extensions",
            "themeColor": color, "summary": a["message"][:150],
            "sections": [{"activityTitle": title, "activitySubtitle": channel_name,
                          "facts": facts, "markdown": True}],
        }

    def _build_slack(self, a, channel_name=""):
        sev = a.get("severity", "warning")
        color = "#10b981" if sev == "resolved" else "#ef4444" if sev == "critical" else "#f59e0b"
        icon = ":white_check_mark:" if sev == "resolved" else ":rotating_light:" if sev == "critical" else ":warning:"
        return {
            "text": f"{icon} {a['message']}",
            "attachments": [{"color": color, "title": f"{'RESOLVED' if sev=='resolved' else 'ALERT'} — {a.get('rule_name', '')}",
                             "fields": [
                                 {"title": "Severity", "value": sev.upper(), "short": True},
                                 {"title": "Item", "value": a.get("item_names", ""), "short": True},
                                 {"title": "Summary", "value": a.get("summary", ""), "short": False},
                             ]}],
        }

    def _build_pagerduty(self, a, channel_name=""):
        return {
            "routing_key": a.get("webhook_url", "").split("/")[-1],
            "event_action": "trigger",
            "payload": {"summary": a["message"][:1024], "severity": a["severity"],
                        "source": "rke2-monitor", "component": a.get("rule_name", ""),
                        "custom_details": a},
        }

    def _build_discord(self, a, channel_name=""):
        color = 0xFF0000 if a["severity"] == "critical" else 0xFFA500
        return {"embeds": [{"title": a["message"][:256], "color": color,
                            "fields": [{"name": "Category", "value": a.get("category", ""), "inline": True},
                                       {"name": "Metric", "value": f"{a.get('metric_name', '')} = {a.get('metric_value', '')}", "inline": True}]}]}

    def _build_generic(self, a, channel_name=""):
        return {"text": a["message"], "alert": a}
