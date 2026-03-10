"""
CEO Briefing — Gold Tier
=========================
Weekly audit engine. Runs every Sunday at 23:00 PKT and generates
a Monday Morning CEO Briefing saved to vault/Plans/.

Data sources it reads automatically:
  1. vault/Business_Goals.md          — Revenue targets, KPIs, audit rules
  2. Odoo JSON-RPC (live)             — Invoices, payments, vendor bills
  3. vault/Plans/META_SUMMARY_*.md    — Latest Facebook/Instagram analytics
  4. vault/Plans/TWITTER_SUMMARY_*.md — Latest Twitter/X analytics
  5. vault/Needs_Action/*.md          — Pending task cards
  6. vault/Plans/DAILY_BRIEFING_*.md  — Recent daily briefings (context)

Output:
  vault/Plans/CEO_BRIEFING_<YYYY-MM-DD>.md

Usage:
  python ceo_briefing.py              # Run full audit now
  python ceo_briefing.py --force      # Re-run even if briefing exists today
  python ceo_briefing.py --no-odoo    # Skip Odoo (offline mode)
  python ceo_briefing.py --no-claude  # Data-only (no AI narrative)

Part of the Personal AI Employee system.
"""

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VAULT_PATH   = PROJECT_ROOT / "vault"
PLANS_PATH   = VAULT_PATH / "Plans"
NEEDS_ACTION = VAULT_PATH / "Needs_Action"

BUSINESS_GOALS_FILE = VAULT_PATH / "Business_Goals.md"
LOG_DIR = PROJECT_ROOT / "logs"

# Pakistan Standard Time (UTC+05:00)
PKT = timezone(timedelta(hours=5))

# ---------------------------------------------------------------------------
# Load env
# ---------------------------------------------------------------------------
load_dotenv(PROJECT_ROOT / ".env")

ODOO_URL      = os.environ.get("ODOO_URL", "http://localhost:8069")
ODOO_DB       = os.environ.get("ODOO_DB", "ai-employee")
ODOO_USER     = os.environ.get("ODOO_USER", "")
ODOO_PASSWORD = os.environ.get("ODOO_PASSWORD", "")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "ceo_briefing.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("ceo_briefing")


# ===========================================================================
# Odoo JSON-RPC helpers  (mirrors odoo_mcp.py — kept self-contained)
# ===========================================================================
_request_id = 0
_uid_cache: Optional[int] = None


def _next_id() -> int:
    global _request_id
    _request_id += 1
    return _request_id


def _jsonrpc(service: str, method: str, args: list) -> Any:
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {"service": service, "method": method, "args": args},
        "id": _next_id(),
    }
    resp = requests.post(f"{ODOO_URL}/jsonrpc", json=payload, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        err = body["error"]
        msg = (err.get("data", {}) or {}).get("message", "") or err.get("message", str(err))
        raise RuntimeError(f"Odoo RPC error: {msg}")
    return body.get("result")


def _authenticate() -> int:
    global _uid_cache
    if _uid_cache:
        return _uid_cache
    uid = _jsonrpc("common", "authenticate",
                   [ODOO_DB, ODOO_USER, ODOO_PASSWORD, {}])
    if not uid:
        raise RuntimeError(
            f"Odoo auth failed for '{ODOO_USER}' on '{ODOO_DB}' at {ODOO_URL}"
        )
    _uid_cache = uid
    return uid


def _execute(model: str, method: str, args: list, kwargs: dict = None) -> Any:
    uid = _authenticate()
    call_args = [ODOO_DB, uid, ODOO_PASSWORD, model, method, args]
    if kwargs:
        call_args.append(kwargs)
    return _jsonrpc("object", "execute_kw", call_args)


def _fc(amount: float) -> str:
    """Format currency with commas."""
    return f"{amount:,.0f}"


# ===========================================================================
# Odoo Data Collection
# ===========================================================================

def _week_range() -> tuple[str, str]:
    """Return (start, end) for the last 7 days as YYYY-MM-DD strings."""
    today = datetime.now(tz=PKT).date()
    start = today - timedelta(days=7)
    return start.isoformat(), today.isoformat()


def _month_range() -> tuple[str, str]:
    """Return (start, end) for the current calendar month."""
    today = datetime.now(tz=PKT).date()
    start = today.replace(day=1)
    return start.isoformat(), today.isoformat()


def collect_odoo_data() -> dict[str, Any]:
    """Pull all relevant financial data from Odoo. Returns structured dict."""
    logger.info("Connecting to Odoo at %s (db: %s)...", ODOO_URL, ODOO_DB)

    week_start, week_end = _week_range()
    month_start, month_end = _month_range()
    today = datetime.now(tz=PKT).date().isoformat()

    data: dict[str, Any] = {
        "available": False,
        "week_start": week_start,
        "week_end": week_end,
        "month_start": month_start,
        "month_end": month_end,
        "error": None,
    }

    try:
        _authenticate()
        data["available"] = True
        logger.info("Odoo connected.")

        # ── Customer Invoices (this week) ──────────────────────────────────
        week_invoices = _execute(
            "account.move", "search_read",
            [[
                ["move_type", "=", "out_invoice"],
                ["invoice_date", ">=", week_start],
                ["invoice_date", "<=", week_end],
            ]],
            {
                "fields": ["name", "partner_id", "invoice_date", "invoice_date_due",
                           "amount_total", "amount_residual", "state", "payment_state"],
                "order": "invoice_date desc",
            },
        ) or []

        # ── Customer Invoices (this month) ─────────────────────────────────
        month_invoices = _execute(
            "account.move", "search_read",
            [[
                ["move_type", "=", "out_invoice"],
                ["invoice_date", ">=", month_start],
                ["invoice_date", "<=", month_end],
            ]],
            {
                "fields": ["name", "amount_total", "amount_residual",
                           "state", "payment_state"],
            },
        ) or []

        # ── Overdue Invoices ───────────────────────────────────────────────
        overdue = _execute(
            "account.move", "search_read",
            [[
                ["move_type", "=", "out_invoice"],
                ["state", "=", "posted"],
                ["payment_state", "not in", ["paid", "in_payment"]],
                ["invoice_date_due", "<", today],
            ]],
            {
                "fields": ["name", "partner_id", "invoice_date_due",
                           "amount_residual", "invoice_date"],
                "order": "invoice_date_due asc",
            },
        ) or []

        # ── Vendor Bills (this week — subscription audit) ──────────────────
        vendor_bills = _execute(
            "account.move", "search_read",
            [[
                ["move_type", "=", "in_invoice"],
                ["invoice_date", ">=", week_start],
            ]],
            {
                "fields": ["name", "partner_id", "invoice_date", "amount_total",
                           "amount_residual", "state", "narration",
                           "invoice_line_ids"],
                "order": "invoice_date desc",
                "limit": 50,
            },
        ) or []

        # Enrich vendor bills with line descriptions
        for bill in vendor_bills:
            line_ids = bill.get("invoice_line_ids", [])
            if line_ids:
                try:
                    lines = _execute(
                        "account.move.line", "read",
                        [line_ids],
                        {"fields": ["name", "price_unit", "quantity"]},
                    ) or []
                    bill["lines"] = lines
                except Exception:
                    bill["lines"] = []

        # ── Payments Received (this week) ──────────────────────────────────
        payments_in_week = _execute(
            "account.payment", "search_read",
            [[
                ["payment_type", "=", "inbound"],
                ["date", ">=", week_start],
                ["date", "<=", week_end],
                ["state", "in", ["posted", "in_process"]],
            ]],
            {"fields": ["name", "partner_id", "amount", "date"]},
        ) or []

        # ── Payments Received (this month) ─────────────────────────────────
        payments_in_month = _execute(
            "account.payment", "search_read",
            [[
                ["payment_type", "=", "inbound"],
                ["date", ">=", month_start],
                ["date", "<=", month_end],
                ["state", "in", ["posted", "in_process"]],
            ]],
            {"fields": ["amount"]},
        ) or []

        # ── Payments Sent (this month) ─────────────────────────────────────
        payments_out_month = _execute(
            "account.payment", "search_read",
            [[
                ["payment_type", "=", "outbound"],
                ["date", ">=", month_start],
                ["date", "<=", month_end],
                ["state", "in", ["posted", "in_process"]],
            ]],
            {"fields": ["amount"]},
        ) or []

        # ── Draft invoices older than 3 days ───────────────────────────────
        three_days_ago = (datetime.now(tz=PKT).date() - timedelta(days=3)).isoformat()
        stale_drafts = _execute(
            "account.move", "search_read",
            [[
                ["move_type", "=", "out_invoice"],
                ["state", "=", "draft"],
                ["create_date", "<=", three_days_ago],
            ]],
            {"fields": ["name", "partner_id", "create_date", "amount_total"],
             "limit": 20},
        ) or []

        # ── Pack it all up ─────────────────────────────────────────────────
        data.update({
            # Weekly
            "week_invoices":       week_invoices,
            "week_revenue":        sum(i["amount_total"] for i in week_invoices),
            "week_collected":      sum(p["amount"] for p in payments_in_week),
            "payments_in_week":    payments_in_week,

            # Monthly
            "month_invoices":      month_invoices,
            "month_revenue":       sum(i["amount_total"] for i in month_invoices),
            "month_collected":     sum(p["amount"] for p in payments_in_month),
            "month_paid_out":      sum(p["amount"] for p in payments_out_month),

            # Overdue & risk
            "overdue_invoices":    overdue,
            "overdue_total":       sum(i["amount_residual"] for i in overdue),
            "overdue_30d":         [
                i for i in overdue
                if (datetime.now(tz=PKT).date() -
                    date.fromisoformat(i["invoice_date_due"])).days >= 30
            ],

            # Vendor bills / subscription audit
            "vendor_bills":        vendor_bills,
            "stale_drafts":        stale_drafts,
        })

        logger.info(
            "Odoo data collected: %d week invoices, %d overdue, %d vendor bills",
            len(week_invoices), len(overdue), len(vendor_bills),
        )

    except Exception as e:
        data["error"] = str(e)
        data["available"] = False
        # Ensure all keys exist with safe defaults so downstream code doesn't crash
        data.setdefault("week_invoices", [])
        data.setdefault("week_revenue", 0)
        data.setdefault("week_collected", 0)
        data.setdefault("payments_in_week", [])
        data.setdefault("month_invoices", [])
        data.setdefault("month_revenue", 0)
        data.setdefault("month_collected", 0)
        data.setdefault("month_paid_out", 0)
        data.setdefault("overdue_invoices", [])
        data.setdefault("overdue_total", 0)
        data.setdefault("overdue_30d", [])
        data.setdefault("vendor_bills", [])
        data.setdefault("stale_drafts", [])
        logger.warning("Odoo collection failed: %s", e)

    return data


# ===========================================================================
# Vault Data Collection
# ===========================================================================

def read_business_goals() -> str:
    """Read vault/Business_Goals.md. Returns raw markdown text."""
    if not BUSINESS_GOALS_FILE.exists():
        return "(Business_Goals.md not found — using defaults)"
    return BUSINESS_GOALS_FILE.read_text(encoding="utf-8")


def read_latest_social_summary(platform: str) -> str:
    """Read the most recent META_SUMMARY_*.md or TWITTER_SUMMARY_*.md."""
    pattern = f"{platform.upper()}_SUMMARY_*.md"
    files = sorted(PLANS_PATH.glob(pattern), reverse=True)
    if not files:
        return f"(No {platform} summary found in vault/Plans/ — run the summary script first)"
    latest = files[0]
    logger.info("Reading social summary: %s", latest.name)
    return latest.read_text(encoding="utf-8")


def read_pending_actions() -> tuple[list[dict], dict[str, int]]:
    """Read Needs_Action/*.md and return (card_list, type_counts)."""
    cards: list[dict] = []
    type_counts: dict[str, int] = {}

    if not NEEDS_ACTION.exists():
        return cards, type_counts

    for card_file in sorted(NEEDS_ACTION.glob("*.md")):
        try:
            text = card_file.read_text(encoding="utf-8")
            card: dict[str, str] = {"filename": card_file.name, "text": text}

            # Extract frontmatter fields
            for line in text.splitlines():
                for field in ("type", "priority", "status", "subject", "from"):
                    if line.strip().startswith(f"{field}:"):
                        card[field] = line.split(":", 1)[1].strip()

            cards.append(card)
            t = card.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        except Exception:
            pass

    return cards, type_counts


def read_recent_daily_briefings(count: int = 3) -> str:
    """Return the last N daily briefings concatenated (for Claude context)."""
    files = sorted(PLANS_PATH.glob("DAILY_BRIEFING_*.md"), reverse=True)[:count]
    if not files:
        return "(No recent daily briefings found)"
    parts = []
    for f in files:
        try:
            parts.append(f"### {f.name}\n" + f.read_text(encoding="utf-8")[:1500])
        except Exception:
            pass
    return "\n\n---\n\n".join(parts)


# ===========================================================================
# Subscription Audit
# ===========================================================================

SUBSCRIPTION_KEYWORDS = [
    "subscription", "monthly fee", "annual plan", "saas", "software license",
    "cloud storage", "hosting", "domain", "renewal", "auto-renew",
    "membership", "retainer", "license", "plan",
]


def run_subscription_audit(
    vendor_bills: list[dict],
    goals_text: str,
) -> dict[str, Any]:
    """Scan vendor bills for subscription-like charges and flag them."""

    # Parse approved subscriptions from Business_Goals.md
    approved: list[str] = []
    in_approved_block = False
    for line in goals_text.splitlines():
        if "approved_subscriptions:" in line:
            in_approved_block = True
            continue
        if in_approved_block:
            if line.strip().startswith("- name:"):
                name = line.split(":", 1)[1].strip().strip('"')
                approved.append(name.lower())
            elif line.strip().startswith("```") or (line.strip() and not line.startswith(" ")):
                in_approved_block = False

    # Parse budget threshold from goals
    max_single = 15000  # PKR default
    max_total  = 50000  # PKR default
    for line in goals_text.splitlines():
        if "Maximum single subscription" in line:
            m = re.search(r"(\d[\d,]+)", line)
            if m:
                max_single = int(m.group(1).replace(",", ""))
        if "Maximum total monthly subscriptions" in line:
            m = re.search(r"(\d[\d,]+)", line)
            if m:
                max_total = int(m.group(1).replace(",", ""))

    flagged: list[dict] = []
    subscription_total = 0.0

    for bill in vendor_bills:
        # Build searchable text from bill + lines
        partner_name = ""
        if isinstance(bill.get("partner_id"), (list, tuple)):
            partner_name = bill["partner_id"][1]

        line_descs = " ".join(
            ln.get("name", "") for ln in bill.get("lines", [])
        )
        full_text = (
            f"{partner_name} {bill.get('narration', '')} {line_descs}"
        ).lower()

        is_sub = any(kw in full_text for kw in SUBSCRIPTION_KEYWORDS)
        if not is_sub:
            continue

        amount = float(bill.get("amount_total", 0))
        subscription_total += amount

        # Check against approved list
        is_approved = any(a in full_text or a in partner_name.lower()
                          for a in approved)
        over_limit = amount > max_single

        flag_reasons = []
        if not is_approved:
            flag_reasons.append("not in approved list")
        if over_limit:
            flag_reasons.append(f"exceeds single-item limit ({_fc(max_single)} PKR)")

        flagged.append({
            "bill_name": bill.get("name", "?"),
            "partner": partner_name,
            "amount": amount,
            "date": bill.get("invoice_date", "?"),
            "state": bill.get("state", "?"),
            "reasons": flag_reasons,
            "approved": is_approved,
        })

    over_budget = subscription_total > max_total

    return {
        "flagged": flagged,
        "total": subscription_total,
        "max_total": max_total,
        "max_single": max_single,
        "over_budget": over_budget,
        "approved_names": approved,
    }


# ===========================================================================
# KPI Evaluation
# ===========================================================================

def evaluate_kpis(odoo: dict, goals_text: str) -> list[dict]:
    """Check each KPI against Business_Goals thresholds. Returns list of results."""
    now = datetime.now(tz=PKT).date()

    # Parse monthly revenue target from goals (rough extraction)
    monthly_target = 500000  # PKR default
    for line in goals_text.splitlines():
        if "| Monthly" in line and "PKR" in line:
            m = re.search(r"\|\s*([\d,]+)\s*\|", line.split("|", 2)[-1] if "|" in line else "")
            if not m:
                m = re.search(r"([\d,]+)\s*\|", line)
            if m:
                try:
                    monthly_target = int(m.group(1).replace(",", ""))
                except ValueError:
                    pass

    # Prorated weekly target (÷ 4.33 weeks per month)
    weekly_target = monthly_target / 4.33
    days_elapsed  = now.day
    month_days    = 31  # approximate
    prorated_month_target = monthly_target * (days_elapsed / month_days)

    results = []

    if odoo.get("available"):
        results += [
            {
                "kpi": "Weekly Revenue Collected",
                "value": _fc(odoo["week_collected"]) + " PKR",
                "target": _fc(weekly_target * 0.8) + " PKR",
                "status": "PASS" if odoo["week_collected"] >= weekly_target * 0.8 else "FAIL",
                "note": f"Week {odoo['week_start']} → {odoo['week_end']}",
            },
            {
                "kpi": "New Invoices This Week",
                "value": str(len(odoo["week_invoices"])),
                "target": "≥ 3",
                "status": "PASS" if len(odoo["week_invoices"]) >= 3 else "WARN",
                "note": f"PKR {_fc(odoo['week_revenue'])} invoiced",
            },
            {
                "kpi": "Month Revenue vs. Prorated Target",
                "value": f"PKR {_fc(odoo['month_revenue'])}",
                "target": f"PKR {_fc(prorated_month_target)}",
                "status": "PASS" if odoo["month_revenue"] >= prorated_month_target else "FAIL",
                "note": f"Day {days_elapsed} of month",
            },
            {
                "kpi": "Overdue Invoices (>30 days)",
                "value": str(len(odoo["overdue_30d"])),
                "target": "0",
                "status": "PASS" if not odoo["overdue_30d"] else "ALERT",
                "note": f"PKR {_fc(sum(i['amount_residual'] for i in odoo['overdue_30d']))} at risk",
            },
            {
                "kpi": "Net Cash Flow (This Month)",
                "value": f"PKR {_fc(odoo['month_collected'] - odoo['month_paid_out'])}",
                "target": "> 0",
                "status": "PASS" if odoo["month_collected"] >= odoo["month_paid_out"] else "FAIL",
                "note": f"In: {_fc(odoo['month_collected'])} | Out: {_fc(odoo['month_paid_out'])}",
            },
            {
                "kpi": "Stale Draft Invoices (>3 days)",
                "value": str(len(odoo["stale_drafts"])),
                "target": "0",
                "status": "PASS" if not odoo["stale_drafts"] else "WARN",
                "note": "Draft invoices should be confirmed or deleted",
            },
        ]
    else:
        results.append({
            "kpi": "Odoo Connection",
            "value": "OFFLINE",
            "target": "Online",
            "status": "ALERT",
            "note": odoo.get("error", "Cannot connect to Odoo"),
        })

    return results


# ===========================================================================
# Prompt Builder (the SKILL)
# ===========================================================================

SKILL_PROMPT_TEMPLATE = """
You are the AI Employee acting as a Chief of Staff. Your job is to generate
a Monday Morning CEO Briefing — a concise, data-driven executive report.

Today: {today}  |  Week reviewed: {week_start} → {week_end}
Time: {time_pkt} PKT  |  Generated by: AI Employee Gold Tier

━━━ BUSINESS GOALS (from vault/Business_Goals.md) ━━━
{goals_text}

━━━ FINANCIAL DATA FROM ODOO ━━━
Odoo Status: {odoo_status}
{odoo_block}

━━━ KPI SCORECARD ━━━
{kpi_block}

━━━ SUBSCRIPTION AUDIT RESULTS ━━━
{subscription_block}

━━━ PENDING ACTIONS (vault/Needs_Action/) ━━━
Total pending cards: {pending_count}
By type: {pending_by_type}
High-priority items: {high_priority_items}

━━━ SOCIAL MEDIA PERFORMANCE ━━━
{social_block}

━━━ RECENT CONTEXT (last 3 daily briefings, truncated) ━━━
{recent_briefings}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR TASK: Generate the Monday Morning CEO Briefing as a well-structured
markdown document. Use the data above — do NOT invent numbers.

Required sections (use these exact headings):
## 1. Executive Summary
   One paragraph. Key wins, key risks, and the single most important thing
   the CEO must focus on this week.

## 2. Financial Performance
   - Revenue this week vs. weekly target (prorated from monthly goal)
   - Revenue this month vs. prorated monthly target
   - Cash collected this week
   - Overdue invoices: count + total PKR at risk
   - Net cash flow this month
   - Any stale draft invoices that need attention

## 3. KPI Scorecard
   Table: KPI | Value | Target | Status (PASS / WARN / FAIL / ALERT)
   Use ✅ for PASS, ⚠️ for WARN, ❌ for FAIL, 🚨 for ALERT.
   After the table: one sentence per failing/alerting KPI explaining the risk.

## 4. Subscription Audit
   - Total subscription spend found this week
   - Budget status (within / over limit)
   - List any flagged subscriptions with reason
   - Specific action for each flag (cancel / approve / investigate)
   - If all clear: say so explicitly

## 5. Social Media Report
   - Bullet summary of each platform's performance vs. weekly targets
   - Top performing post (if data available)
   - Gap vs. posting targets (e.g. "Posted 2/5 planned Facebook posts")

## 6. Pending Actions
   - Count by type
   - Any high-priority items requiring CEO attention today (list specifically)
   - Recommended order of resolution

## 7. Weekly Priorities
   Numbered list of 5 specific actions the CEO should take THIS WEEK,
   ordered by business impact. Each must be actionable in one sentence.
   Reference specific data from above (e.g. "Follow up on invoice INV/2026/0003
   — 45 days overdue, PKR 75,000 at risk").

## 8. Alerts & Red Flags
   Only include items that require IMMEDIATE attention (today or tomorrow).
   If none: write "No critical alerts this week."

Rules:
- Be direct. No motivational fluff. CEO wants data and actions.
- If Odoo is offline, say so clearly in Section 2 and skip financial KPIs.
- Format numbers with commas (e.g., 125,000 not 125000).
- Use PKR for Pakistani Rupees. Add USD equivalent where helpful (rate: ~278 PKR/USD).
- All status badges must be consistent with the KPI data provided.
- End with a one-line "Generated by AI Employee | {today}" footer.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def build_odoo_block(odoo: dict) -> str:
    """Format Odoo data as readable text for the prompt."""
    if not odoo.get("available"):
        return f"⚠️  Odoo OFFLINE — {odoo.get('error', 'connection failed')}\nAll financial KPIs will show as unavailable."

    lines = []

    # Week invoices
    lines.append(f"WEEK INVOICES ({len(odoo['week_invoices'])} total, PKR {_fc(odoo['week_revenue'])}):")
    for inv in odoo["week_invoices"][:10]:
        partner = inv["partner_id"][1] if isinstance(inv["partner_id"], (list, tuple)) else "?"
        lines.append(
            f"  {inv['name']} | {partner} | PKR {_fc(inv['amount_total'])} | "
            f"State: {inv['state']} | Paid: {inv['payment_state']}"
        )
    if not odoo["week_invoices"]:
        lines.append("  (none this week)")

    lines.append("")
    lines.append(f"MONTH REVENUE: PKR {_fc(odoo['month_revenue'])} invoiced | "
                 f"PKR {_fc(odoo['month_collected'])} collected | "
                 f"PKR {_fc(odoo['month_paid_out'])} paid out")

    lines.append("")
    lines.append(f"OVERDUE INVOICES ({len(odoo['overdue_invoices'])} total, PKR {_fc(odoo['overdue_total'])} outstanding):")
    for inv in odoo["overdue_invoices"][:8]:
        partner = inv["partner_id"][1] if isinstance(inv["partner_id"], (list, tuple)) else "?"
        due = inv.get("invoice_date_due", "?")
        days_overdue = (datetime.now(tz=PKT).date() - date.fromisoformat(due)).days if due != "?" else "?"
        lines.append(
            f"  {inv['name']} | {partner} | PKR {_fc(inv['amount_residual'])} | "
            f"Due: {due} ({days_overdue} days overdue)"
        )
    if not odoo["overdue_invoices"]:
        lines.append("  ✅ No overdue invoices")

    lines.append("")
    lines.append(f"PAYMENTS RECEIVED THIS WEEK: {len(odoo['payments_in_week'])} payments | PKR {_fc(odoo['week_collected'])}")
    for p in odoo["payments_in_week"][:5]:
        partner = p["partner_id"][1] if isinstance(p["partner_id"], (list, tuple)) else "?"
        lines.append(f"  {p['name']} | {partner} | PKR {_fc(p['amount'])} | {p.get('date', '?')}")
    if not odoo["payments_in_week"]:
        lines.append("  (none this week)")

    if odoo.get("stale_drafts"):
        lines.append("")
        lines.append(f"STALE DRAFT INVOICES (>3 days old, {len(odoo['stale_drafts'])} found):")
        for d in odoo["stale_drafts"][:5]:
            partner = d["partner_id"][1] if isinstance(d["partner_id"], (list, tuple)) else "?"
            lines.append(f"  {d['name']} | {partner} | PKR {_fc(d['amount_total'])} | Created: {str(d.get('create_date', '?'))[:10]}")

    return "\n".join(lines)


def build_kpi_block(kpis: list[dict]) -> str:
    lines = []
    for k in kpis:
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "ALERT": "🚨"}.get(k["status"], "❓")
        lines.append(
            f"  {icon} {k['kpi']}: {k['value']} (target: {k['target']}) — {k.get('note', '')}"
        )
    return "\n".join(lines) or "(no KPIs computed)"


def build_subscription_block(audit: dict) -> str:
    lines = [
        f"Total subscription-like charges found: PKR {_fc(audit['total'])}",
        f"Monthly budget: PKR {_fc(audit['max_total'])}",
        f"Status: {'🚨 OVER BUDGET' if audit['over_budget'] else '✅ Within budget'}",
        "",
    ]
    if audit["flagged"]:
        lines.append(f"FLAGGED SUBSCRIPTIONS ({len(audit['flagged'])}):")
        for item in audit["flagged"]:
            approved_tag = "✅ Approved" if item["approved"] else "⚠️ UNAPPROVED"
            lines.append(
                f"  {approved_tag} | {item['partner']} | PKR {_fc(item['amount'])} | "
                f"{item['bill_name']} | {item['date']}"
            )
            if item["reasons"]:
                lines.append(f"    ↳ Flags: {', '.join(item['reasons'])}")
    else:
        lines.append("✅ No subscription-like vendor bills found this week.")
    return "\n".join(lines)


def build_social_block() -> str:
    """Read and summarize social media summaries."""
    parts = []

    meta_text = read_latest_social_summary("meta")
    if "No META summary" not in meta_text:
        # Extract just the key numbers (first 800 chars of the summary)
        parts.append("--- Facebook + Instagram (latest META_SUMMARY) ---")
        parts.append(meta_text[:800])
    else:
        parts.append("Facebook/Instagram: No summary available. Run: python watchers/meta_summary.py")

    twitter_text = read_latest_social_summary("twitter")
    if "No TWITTER summary" not in twitter_text:
        parts.append("\n--- Twitter/X (latest TWITTER_SUMMARY) ---")
        parts.append(twitter_text[:800])
    else:
        parts.append("\nTwitter/X: No summary available. Run: python watchers/twitter_summary.py")

    return "\n".join(parts)


# ===========================================================================
# Main execution
# ===========================================================================

def run_ceo_briefing(force: bool = False, skip_odoo: bool = False,
                     skip_claude: bool = False) -> Optional[Path]:
    """Full pipeline: collect → audit → prompt → Claude → save."""
    PLANS_PATH.mkdir(parents=True, exist_ok=True)

    now = datetime.now(tz=PKT)
    date_str = now.strftime("%Y-%m-%d")
    output_path = PLANS_PATH / f"CEO_BRIEFING_{date_str}.md"

    if output_path.exists() and not force:
        logger.info("CEO Briefing already exists for %s. Use --force to regenerate.", date_str)
        return output_path

    print()
    print("=" * 65)
    print("  AI Employee — CEO Briefing Generator (Gold Tier)")
    print("=" * 65)
    print(f"  Date     : {date_str}")
    print(f"  Output   : {output_path.name}")
    print(f"  Odoo     : {'SKIP' if skip_odoo else ODOO_URL}")
    print(f"  Claude   : {'SKIP (data only)' if skip_claude else 'enabled'}")
    print("=" * 65)
    print()

    # ── 1. Read Business Goals ──────────────────────────────────────────────
    logger.info("Reading Business_Goals.md...")
    goals_text = read_business_goals()

    # ── 2. Collect Odoo Data ────────────────────────────────────────────────
    if skip_odoo:
        odoo = {"available": False, "error": "Odoo skipped (--no-odoo flag)",
                "week_start": "?", "week_end": "?", "week_invoices": [],
                "week_revenue": 0, "week_collected": 0, "payments_in_week": [],
                "month_invoices": [], "month_revenue": 0, "month_collected": 0,
                "month_paid_out": 0, "overdue_invoices": [], "overdue_total": 0,
                "overdue_30d": [], "vendor_bills": [], "stale_drafts": []}
    else:
        odoo = collect_odoo_data()

    # ── 3. Run Subscription Audit ───────────────────────────────────────────
    logger.info("Running subscription audit...")
    subscription_audit = run_subscription_audit(
        odoo.get("vendor_bills", []), goals_text
    )

    # ── 4. Evaluate KPIs ────────────────────────────────────────────────────
    logger.info("Evaluating KPIs...")
    kpis = evaluate_kpis(odoo, goals_text)

    # ── 5. Collect Vault Data ───────────────────────────────────────────────
    logger.info("Reading vault data...")
    pending_cards, type_counts = read_pending_actions()
    high_priority = [
        c.get("subject", c["filename"])
        for c in pending_cards
        if c.get("priority") in ("high", "urgent")
    ]
    recent_briefings = read_recent_daily_briefings(3)

    # ── 6. Build Prompt ─────────────────────────────────────────────────────
    logger.info("Building CEO Briefing prompt...")

    week_start = odoo.get("week_start", "?")
    week_end   = odoo.get("week_end", "?")

    prompt = SKILL_PROMPT_TEMPLATE.format(
        today=date_str,
        week_start=week_start,
        week_end=week_end,
        time_pkt=now.strftime("%H:%M"),
        goals_text=goals_text[:3000],  # Truncate for prompt safety
        odoo_status="ONLINE" if odoo.get("available") else f"OFFLINE ({odoo.get('error', '?')})",
        odoo_block=build_odoo_block(odoo),
        kpi_block=build_kpi_block(kpis),
        subscription_block=build_subscription_block(subscription_audit),
        pending_count=len(pending_cards),
        pending_by_type=", ".join(f"{v} {k}" for k, v in type_counts.items()) or "none",
        high_priority_items="\n".join(f"  - {h}" for h in high_priority[:10]) or "  (none)",
        social_block=build_social_block(),
        recent_briefings=recent_briefings[:2000],
    )

    # Save raw data as a companion JSON for debugging
    raw_data_path = PLANS_PATH / f"CEO_BRIEFING_RAW_{date_str}.json"
    try:
        raw_export = {
            "date": date_str,
            "kpis": kpis,
            "subscription_audit": {
                k: v for k, v in subscription_audit.items() if k != "flagged"
            },
            "subscription_flagged": subscription_audit.get("flagged", []),
            "pending_count": len(pending_cards),
            "type_counts": type_counts,
            "odoo_available": odoo.get("available", False),
            "week_revenue": odoo.get("week_revenue", 0),
            "week_collected": odoo.get("week_collected", 0),
            "overdue_count": len(odoo.get("overdue_invoices", [])),
            "overdue_total": odoo.get("overdue_total", 0),
        }
        raw_data_path.write_text(
            json.dumps(raw_export, indent=2, default=str), encoding="utf-8"
        )
        logger.info("Raw data saved: %s", raw_data_path.name)
    except Exception:
        logger.warning("Could not save raw data JSON", exc_info=True)

    # ── 7. Call Claude CLI ──────────────────────────────────────────────────
    if skip_claude:
        logger.info("--no-claude flag: saving data-only briefing.")
        content = (
            f"---\ntype: ceo_briefing\ngenerated_at: {now.isoformat()}\n"
            f"ai_enhanced: false\n---\n\n"
            f"# CEO Briefing (Data Only) — {date_str}\n\n"
            f"*(Claude was not invoked — raw data saved to {raw_data_path.name})*\n\n"
            f"## KPI Scorecard\n\n"
            + build_kpi_block(kpis) + "\n\n"
            + "## Subscription Audit\n\n"
            + build_subscription_block(subscription_audit) + "\n"
        )
        output_path.write_text(content, encoding="utf-8")
        logger.info("Data-only briefing saved: %s", output_path.name)
        return output_path

    logger.info("Calling Claude CLI to generate briefing narrative...")

    try:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = subprocess.run(
            ["claude", "--print", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=300,       # 5 minutes — briefing is thorough
            cwd=str(VAULT_PATH),
            env=env,
            shell=True,        # Windows-compatible
        )

        if result.returncode == 0 and result.stdout.strip():
            body = result.stdout.strip()
            frontmatter = (
                f"---\n"
                f"type: ceo_briefing\n"
                f"generated_at: {now.isoformat()}\n"
                f"week_start: {week_start}\n"
                f"week_end: {week_end}\n"
                f"odoo_online: {odoo.get('available', False)}\n"
                f"week_revenue_pkr: {odoo.get('week_revenue', 0):.0f}\n"
                f"week_collected_pkr: {odoo.get('week_collected', 0):.0f}\n"
                f"overdue_count: {len(odoo.get('overdue_invoices', []))}\n"
                f"kpis_pass: {sum(1 for k in kpis if k['status'] == 'PASS')}\n"
                f"kpis_fail: {sum(1 for k in kpis if k['status'] in ('FAIL', 'ALERT'))}\n"
                f"subscription_flags: {len(subscription_audit.get('flagged', []))}\n"
                f"ai_enhanced: true\n"
                f"---\n\n"
            )
            output_path.write_text(frontmatter + body, encoding="utf-8")
            logger.info("CEO Briefing saved: %s (%d chars)", output_path.name, len(body))
            print(f"\n✅ CEO Briefing saved to: {output_path}")
            return output_path

        else:
            stderr_preview = result.stderr[:500] if result.stderr else "(no stderr)"
            logger.error(
                "Claude returned exit %d. stderr: %s", result.returncode, stderr_preview
            )
            # Fall back to data-only
            fallback = (
                f"---\ntype: ceo_briefing\ngenerated_at: {now.isoformat()}\n"
                f"ai_enhanced: false\nerror: claude_exit_{result.returncode}\n---\n\n"
                f"# CEO Briefing (Fallback — Claude Error) — {date_str}\n\n"
                f"Claude CLI exited with code {result.returncode}.\n"
                f"Raw data was saved to: {raw_data_path.name}\n\n"
                f"## KPI Scorecard\n\n" + build_kpi_block(kpis) + "\n\n"
                f"## Subscription Audit\n\n" + build_subscription_block(subscription_audit)
            )
            output_path.write_text(fallback, encoding="utf-8")
            return output_path

    except FileNotFoundError:
        logger.error("Claude CLI not found — install Claude Code and ensure 'claude' is in PATH.")
        return None
    except subprocess.TimeoutExpired:
        logger.error("Claude timed out after 300s generating CEO Briefing.")
        return None
    except Exception:
        logger.error("CEO Briefing generation failed", exc_info=True)
        return None


# ===========================================================================
# CLI
# ===========================================================================
def main() -> None:
    force      = "--force" in sys.argv
    skip_odoo  = "--no-odoo" in sys.argv
    skip_claude = "--no-claude" in sys.argv

    path = run_ceo_briefing(
        force=force,
        skip_odoo=skip_odoo,
        skip_claude=skip_claude,
    )

    if path:
        print(f"\nOpen in VS Code: code \"{path}\"")
    else:
        print("\nCEO Briefing generation failed — check logs/ceo_briefing.log")
        sys.exit(1)


if __name__ == "__main__":
    main()
