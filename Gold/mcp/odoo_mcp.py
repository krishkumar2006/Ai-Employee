"""
Odoo MCP Server — Gold Tier
============================
A Model Context Protocol server that gives Claude the ability to
interact with a local Odoo 19 instance via JSON-RPC.

Built with Human-in-the-Loop (HITL) safety:
  - WRITE actions (create invoice, post payment) produce DRAFTS first
  - Drafts are saved to vault/Odoo_Drafts/ for human review
  - Only explicit approval triggers the actual Odoo write
  - READ actions (list invoices, get report) execute immediately

Tools exposed:
  1. odoo_draft_invoice     — Prepare an invoice draft (NO Odoo write)
  2. odoo_confirm_invoice   — Create the invoice in Odoo after approval
  3. odoo_read_invoices     — List/search invoices (read-only, immediate)
  4. odoo_read_payments     — List payments (read-only, immediate)
  5. odoo_draft_payment     — Prepare a payment draft (NO Odoo write)
  6. odoo_confirm_payment   — Register payment in Odoo after approval
  7. odoo_get_partners      — List customers/vendors (read-only)
  8. odoo_get_products       — List products (read-only)
  9. odoo_report_summary    — Generate a financial summary report
  10. odoo_list_drafts       — Show all pending Odoo drafts
  11. odoo_discard_draft     — Discard a draft without executing

Env vars required:
  ODOO_URL       — Odoo base URL (default: http://localhost:8069)
  ODOO_DB        — Odoo database name (default: ai-employee)
  ODOO_USER      — Odoo admin email/login
  ODOO_PASSWORD  — Odoo admin password

Part of the Personal AI Employee system.
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP

# Gold Tier: structured audit logging + resilience + offline queue
# Insert project root so watchers/mcp can import sibling modules
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))
from audit_logger import (
    AuditLogger,
    EV_API_CALL,
    EV_API_FAIL,
    EV_CIRCUIT_OPEN,
    EV_ODOO_ACTION,
    EV_ODOO_FAIL,
)
from retry_handler import CircuitBreaker, CircuitOpenError, retry
from offline_queue import get_queue

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VAULT_PATH = PROJECT_ROOT / "vault"
ODOO_DRAFTS_DIR = VAULT_PATH / "Odoo_Drafts"
ODOO_LOG_DIR = VAULT_PATH / "Odoo_Logs"

ODOO_URL = os.environ.get("ODOO_URL", "http://localhost:8069")
ODOO_DB = os.environ.get("ODOO_DB", "ai-employee")
ODOO_USER = os.environ.get("ODOO_USER", "")
ODOO_PASSWORD = os.environ.get("ODOO_PASSWORD", "")

# Pakistan Standard Time (UTC+05:00)
PKT = timezone(timedelta(hours=5))

# ---------------------------------------------------------------------------
# Logging  (stderr plain logger for MCP stdio transport; structured AuditLogger)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    stream=sys.stderr,
)
logger    = logging.getLogger("odoo_mcp")
audit_log = AuditLogger("odoo_mcp")
_odoo_q   = get_queue("odoo")   # Offline queue for when Odoo is unreachable

# ---------------------------------------------------------------------------
# Ensure directories exist
# ---------------------------------------------------------------------------
ODOO_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
ODOO_LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# JSON-RPC Client
# ---------------------------------------------------------------------------
_request_id = 0


def _next_id() -> int:
    global _request_id
    _request_id += 1
    return _request_id


@retry(service="odoo", component="odoo_mcp")
def jsonrpc(url: str, service: str, method: str, args: list) -> Any:
    """Send a JSON-RPC 2.0 call to Odoo and return the result (with retry)."""
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "service": service,
            "method": method,
            "args": args,
        },
        "id": _next_id(),
    }
    audit_log.info(EV_API_CALL, service="odoo", rpc_service=service, method=method)
    resp = requests.post(f"{url}/jsonrpc", json=payload, timeout=30)
    resp.raise_for_status()
    body = resp.json()

    if body.get("error"):
        err = body["error"]
        msg = err.get("data", {}).get("message", "") or err.get("message", str(err))
        raise RuntimeError(f"Odoo RPC error: {msg}")

    return body.get("result")


# ---------------------------------------------------------------------------
# Odoo Authentication
# ---------------------------------------------------------------------------
_uid_cache: int | None = None


def odoo_authenticate() -> int:
    """Authenticate with Odoo and return the user ID (uid)."""
    global _uid_cache
    if _uid_cache is not None:
        return _uid_cache

    if not ODOO_USER or not ODOO_PASSWORD:
        raise RuntimeError(
            "ODOO_USER and ODOO_PASSWORD environment variables are required. "
            "Set them in your MCP config (.claude/mcp.json)."
        )

    uid = jsonrpc(ODOO_URL, "common", "authenticate",
                  [ODOO_DB, ODOO_USER, ODOO_PASSWORD, {}])

    if not uid:
        raise RuntimeError(
            f"Odoo authentication failed for user '{ODOO_USER}' on "
            f"database '{ODOO_DB}' at {ODOO_URL}. Check credentials."
        )

    _uid_cache = uid
    logger.info("Authenticated with Odoo as uid=%d", uid)
    return uid


def odoo_execute(model: str, method: str, args: list,
                 kwargs: dict | None = None) -> Any:
    """Execute an Odoo model method via JSON-RPC (circuit-breaker protected)."""
    cb = CircuitBreaker.get("odoo")
    with cb:
        uid = odoo_authenticate()
        call_args = [ODOO_DB, uid, ODOO_PASSWORD, model, method, args]
        if kwargs:
            call_args.append(kwargs)
        return jsonrpc(ODOO_URL, "object", "execute_kw", call_args)


def _odoo_unavailable_message(operation: str = "") -> str:
    """Return a user-friendly message when Odoo circuit is OPEN."""
    pending = _odoo_q.pending_count()
    queued_note = (
        f" Operation queued (queue depth: {pending})."
        if operation else ""
    )
    return (
        "⚠ Odoo is currently unreachable (circuit OPEN)."
        f"{queued_note}\n"
        "The system will retry automatically when Odoo recovers.\n"
        "Check vault/Queue/ for queued operations.\n"
        f"Check vault/Logs/HEALTH.json for circuit status."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_pkt() -> datetime:
    return datetime.now(tz=PKT)


def now_iso() -> str:
    return now_pkt().isoformat()


def short_id() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# In-memory draft store  (HITL: draft first, confirm only after approval)
# ---------------------------------------------------------------------------
drafts: dict[str, dict] = {}


def save_draft_to_vault(draft_id: str, draft: dict) -> Path:
    """Persist a draft as a .md card in vault/Odoo_Drafts/."""
    ts = now_pkt().strftime("%Y-%m-%dT%H-%M-%S")
    kind = draft["kind"]
    filename = f"ODOO_{kind}_{draft_id}_{ts}.md"
    path = ODOO_DRAFTS_DIR / filename

    lines = draft.get("preview_lines", [])
    frontmatter = (
        f"---\n"
        f"type: odoo_{kind}_draft\n"
        f"draft_id: {draft_id}\n"
        f"status: pending_approval\n"
        f"created_at: {now_iso()}\n"
        f"source: odoo_mcp\n"
        f"---\n\n"
    )
    path.write_text(frontmatter + "\n".join(lines), encoding="utf-8")
    return path


def log_odoo_action(action: str, details: dict) -> None:
    """Append an audit entry to vault/Odoo_Logs/."""
    ts = now_pkt().strftime("%Y-%m-%dT%H-%M-%S")
    filename = f"ODOO_LOG_{ts}_{short_id()}.json"
    path = ODOO_LOG_DIR / filename
    entry = {
        "action": action,
        "timestamp": now_iso(),
        "details": details,
    }
    path.write_text(json.dumps(entry, indent=2, default=str), encoding="utf-8")


def format_currency(amount: float) -> str:
    return f"{amount:,.2f}"


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "ai-employee-odoo",
    instructions=(
        "Odoo ERP integration for the AI Employee. "
        "WRITE operations (invoices, payments) always create drafts first. "
        "Ask the human to approve before calling any confirm_ tool. "
        "READ operations execute immediately."
    ),
)


# ===== TOOL 1: Draft Invoice ================================================
@mcp.tool()
def odoo_draft_invoice(
    partner_name: str,
    lines: list[dict],
    invoice_date: str = "",
    due_date: str = "",
    notes: str = "",
) -> str:
    """Prepare a customer invoice draft for human review. Does NOT write to Odoo.

    Args:
        partner_name: Customer name (must exist in Odoo contacts).
        lines: Invoice line items. Each dict needs:
                 - description (str): Line description / product name
                 - quantity (float): Qty, default 1
                 - price_unit (float): Unit price
        invoice_date: Invoice date YYYY-MM-DD (default: today).
        due_date: Due date YYYY-MM-DD (default: empty, uses payment terms).
        notes: Internal notes for the invoice.
    """
    # Validate inputs
    if not partner_name:
        return "ERROR: partner_name is required."
    if not lines or len(lines) == 0:
        return "ERROR: At least one invoice line is required."

    for i, line in enumerate(lines):
        if "price_unit" not in line:
            return f"ERROR: Line {i+1} is missing 'price_unit'."

    # Look up partner in Odoo (read-only — safe)
    try:
        partners = odoo_execute(
            "res.partner", "search_read",
            [[["name", "ilike", partner_name]]],
            {"fields": ["id", "name", "email"], "limit": 5},
        )
    except CircuitOpenError:
        audit_log.warn(EV_CIRCUIT_OPEN, service="odoo", action="draft_invoice",
                       partner=partner_name)
        return _odoo_unavailable_message()
    except Exception as e:
        audit_log.error(EV_ODOO_FAIL, action="partner_lookup", error=str(e))
        return f"ERROR connecting to Odoo: {e}"

    if not partners:
        return (
            f"ERROR: No customer found matching '{partner_name}' in Odoo.\n"
            f"Create the contact in Odoo first, then retry."
        )

    partner = partners[0]

    # Calculate totals
    total = 0.0
    line_previews = []
    for i, line in enumerate(lines):
        qty = float(line.get("quantity", 1))
        price = float(line["price_unit"])
        desc = line.get("description", f"Item {i+1}")
        subtotal = qty * price
        total += subtotal
        line_previews.append(
            f"  {i+1}. {desc}  |  Qty: {qty}  |  "
            f"Unit: {format_currency(price)}  |  "
            f"Subtotal: {format_currency(subtotal)}"
        )

    inv_date = invoice_date or now_pkt().strftime("%Y-%m-%d")

    draft_id = short_id()
    draft = {
        "kind": "invoice",
        "partner_id": partner["id"],
        "partner_name": partner["name"],
        "partner_email": partner.get("email", ""),
        "move_type": "out_invoice",
        "invoice_date": inv_date,
        "due_date": due_date,
        "lines": lines,
        "notes": notes,
        "total": total,
        "preview_lines": [],
    }

    preview = [
        f"ODOO INVOICE DRAFT — ID: {draft_id}",
        "",
        "============ INVOICE PREVIEW ============",
        f"Type:      Customer Invoice",
        f"Customer:  {partner['name']} (ID: {partner['id']})",
        f"Email:     {partner.get('email', 'N/A')}",
        f"Date:      {inv_date}",
        f"Due:       {due_date or '(per payment terms)'}",
        "",
        "Lines:",
        *line_previews,
        "",
        f"TOTAL:     {format_currency(total)}",
        "",
        f"Notes:     {notes or '(none)'}",
        "=========================================",
        "",
        "STATUS: Waiting for human approval.",
        "",
        "To create this invoice in Odoo, ask the user:",
        f'  "Should I create this invoice? (Draft ID: {draft_id})"',
        "",
        f"Then call odoo_confirm_invoice with draft_id=\"{draft_id}\".",
        f"To cancel, call odoo_discard_draft with draft_id=\"{draft_id}\".",
    ]

    draft["preview_lines"] = preview
    drafts[draft_id] = draft
    save_draft_to_vault(draft_id, draft)

    return "\n".join(preview)


# ===== TOOL 2: Confirm Invoice ==============================================
@mcp.tool()
def odoo_confirm_invoice(draft_id: str) -> str:
    """Create an invoice in Odoo from an approved draft.
    NEVER call this without explicit user confirmation.

    Args:
        draft_id: The draft ID returned by odoo_draft_invoice.
    """
    draft = drafts.get(draft_id)
    if not draft or draft["kind"] != "invoice":
        active = [k for k, v in drafts.items() if v["kind"] == "invoice"]
        return (
            f"ERROR: Invoice draft '{draft_id}' not found.\n"
            f"Active invoice drafts: {', '.join(active) or 'none'}"
        )

    # Build Odoo invoice line commands: (0, 0, {vals})
    invoice_lines = []
    for line in draft["lines"]:
        vals = {
            "name": line.get("description", "Item"),
            "quantity": float(line.get("quantity", 1)),
            "price_unit": float(line["price_unit"]),
        }
        # Try to find product by description
        try:
            products = odoo_execute(
                "product.product", "search_read",
                [[["name", "ilike", line.get("description", "")]]],
                {"fields": ["id"], "limit": 1},
            )
            if products:
                vals["product_id"] = products[0]["id"]
        except Exception:
            pass  # Product lookup is best-effort
        invoice_lines.append((0, 0, vals))

    invoice_vals = {
        "move_type": "out_invoice",
        "partner_id": draft["partner_id"],
        "invoice_date": draft["invoice_date"],
        "invoice_line_ids": invoice_lines,
    }
    if draft["due_date"]:
        invoice_vals["invoice_date_due"] = draft["due_date"]
    if draft["notes"]:
        invoice_vals["narration"] = draft["notes"]

    try:
        invoice_id = odoo_execute("account.move", "create", [invoice_vals])
    except CircuitOpenError:
        # Odoo down — queue the confirmed invoice creation for later
        _odoo_q.enqueue("create_invoice", payload={
            "draft_id": draft_id,
            "invoice_vals": invoice_vals,
            "partner_name": draft["partner_name"],
        })
        audit_log.warn(EV_CIRCUIT_OPEN, service="odoo", action="confirm_invoice",
                       draft_id=draft_id, queued=True)
        return _odoo_unavailable_message(operation="create_invoice")
    except Exception as e:
        audit_log.error(EV_ODOO_FAIL, action="create_invoice", draft_id=draft_id,
                        error=str(e))
        return (
            f"ERROR creating invoice in Odoo: {e}\n\n"
            f"Draft is still saved (ID: {draft_id}). Fix the issue and retry."
        )

    # Read back the created invoice for confirmation
    try:
        created = odoo_execute(
            "account.move", "read",
            [invoice_id],
            {"fields": ["name", "state", "amount_total", "partner_id"]},
        )
        inv = created[0] if isinstance(created, list) else created
        inv_name = inv.get("name", f"ID {invoice_id}")
        inv_total = inv.get("amount_total", draft["total"])
        inv_state = inv.get("state", "draft")
    except Exception:
        inv_name = f"ID {invoice_id}"
        inv_total = draft["total"]
        inv_state = "draft"

    # Cleanup
    del drafts[draft_id]

    # Audit log (both legacy file log and structured AuditLogger)
    log_odoo_action("invoice_created", {
        "invoice_id": invoice_id,
        "invoice_name": inv_name,
        "partner": draft["partner_name"],
        "total": inv_total,
        "draft_id": draft_id,
    })
    audit_log.info(
        EV_ODOO_ACTION,
        action="invoice_created",
        invoice_id=invoice_id,
        invoice_name=inv_name,
        partner=draft["partner_name"],
        total=inv_total,
        draft_id=draft_id,
    )

    return (
        f"INVOICE CREATED SUCCESSFULLY IN ODOO\n"
        f"\n"
        f"Invoice:   {inv_name}\n"
        f"Odoo ID:   {invoice_id}\n"
        f"Customer:  {draft['partner_name']}\n"
        f"Total:     {format_currency(inv_total)}\n"
        f"State:     {inv_state} (draft — ready to be validated in Odoo)\n"
        f"Date:      {draft['invoice_date']}\n"
        f"\n"
        f"The invoice is in DRAFT state in Odoo. A human can review and\n"
        f"click 'Confirm' in the Odoo UI to finalize it.\n"
        f"\n"
        f"View it at: {ODOO_URL}/odoo/accounting/customer-invoices/{invoice_id}\n"
        f"\n"
        f"Audit log saved to vault/Odoo_Logs/."
    )


# ===== TOOL 3: Read Invoices ================================================
@mcp.tool()
def odoo_read_invoices(
    state: str = "",
    partner_name: str = "",
    limit: int = 20,
    move_type: str = "out_invoice",
) -> str:
    """Search and list invoices from Odoo. Read-only, executes immediately.

    Args:
        state: Filter by state: draft, posted, cancel (empty = all).
        partner_name: Filter by customer name (partial match).
        limit: Max results (default 20).
        move_type: Invoice type — out_invoice (customer), in_invoice (vendor).
    """
    domain: list = [["move_type", "=", move_type]]

    if state:
        domain.append(["state", "=", state])
    if partner_name:
        domain.append(["partner_id.name", "ilike", partner_name])

    try:
        invoices = odoo_execute(
            "account.move", "search_read",
            [domain],
            {
                "fields": [
                    "name", "partner_id", "invoice_date", "invoice_date_due",
                    "amount_total", "amount_residual", "state", "payment_state",
                ],
                "limit": limit,
                "order": "invoice_date desc",
            },
        )
    except CircuitOpenError:
        audit_log.warn(EV_CIRCUIT_OPEN, service="odoo", action="read_invoices")
        return _odoo_unavailable_message()
    except Exception as e:
        audit_log.error(EV_ODOO_FAIL, action="read_invoices", error=str(e))
        return f"ERROR reading invoices from Odoo: {e}"

    if not invoices:
        filters = []
        if state:
            filters.append(f"state={state}")
        if partner_name:
            filters.append(f"partner={partner_name}")
        return f"No invoices found. Filters: {', '.join(filters) or 'none'}"

    type_label = "Customer" if move_type == "out_invoice" else "Vendor"
    lines = [
        f"ODOO {type_label.upper()} INVOICES ({len(invoices)} found)",
        "=" * 60,
        "",
    ]

    for inv in invoices:
        partner = inv["partner_id"]
        partner_display = partner[1] if isinstance(partner, (list, tuple)) else str(partner)
        paid_status = inv.get("payment_state", "not_paid")
        lines.extend([
            f"  {inv['name']}",
            f"    Customer:  {partner_display}",
            f"    Date:      {inv.get('invoice_date', 'N/A')}",
            f"    Due:       {inv.get('invoice_date_due', 'N/A')}",
            f"    Total:     {format_currency(inv['amount_total'])}",
            f"    Remaining: {format_currency(inv['amount_residual'])}",
            f"    State:     {inv['state']}  |  Payment: {paid_status}",
            "",
        ])

    total_sum = sum(inv["amount_total"] for inv in invoices)
    outstanding = sum(inv["amount_residual"] for inv in invoices)
    lines.append(f"Totals:  {format_currency(total_sum)} invoiced  |  "
                 f"{format_currency(outstanding)} outstanding")

    return "\n".join(lines)


# ===== TOOL 4: Read Payments ================================================
@mcp.tool()
def odoo_read_payments(
    partner_name: str = "",
    payment_type: str = "inbound",
    limit: int = 20,
) -> str:
    """List payments from Odoo. Read-only, executes immediately.

    Args:
        partner_name: Filter by partner name (partial match).
        payment_type: inbound (customer payment) or outbound (vendor payment).
        limit: Max results (default 20).
    """
    domain: list = [["payment_type", "=", payment_type]]
    if partner_name:
        domain.append(["partner_id.name", "ilike", partner_name])

    try:
        payments = odoo_execute(
            "account.payment", "search_read",
            [domain],
            {
                "fields": [
                    "name", "partner_id", "amount", "date",
                    "state", "payment_type", "ref",
                ],
                "limit": limit,
                "order": "date desc",
            },
        )
    except CircuitOpenError:
        audit_log.warn(EV_CIRCUIT_OPEN, service="odoo", action="read_payments")
        return _odoo_unavailable_message()
    except Exception as e:
        audit_log.error(EV_ODOO_FAIL, action="read_payments", error=str(e))
        return f"ERROR reading payments from Odoo: {e}"

    if not payments:
        return f"No {payment_type} payments found."

    type_label = "Customer" if payment_type == "inbound" else "Vendor"
    lines = [
        f"ODOO {type_label.upper()} PAYMENTS ({len(payments)} found)",
        "=" * 60,
        "",
    ]

    for pay in payments:
        partner = pay["partner_id"]
        partner_display = partner[1] if isinstance(partner, (list, tuple)) else str(partner)
        lines.extend([
            f"  {pay['name']}",
            f"    Partner:  {partner_display}",
            f"    Amount:   {format_currency(pay['amount'])}",
            f"    Date:     {pay.get('date', 'N/A')}",
            f"    State:    {pay['state']}",
            f"    Ref:      {pay.get('ref', 'N/A')}",
            "",
        ])

    total = sum(p["amount"] for p in payments)
    lines.append(f"Total: {format_currency(total)}")

    return "\n".join(lines)


# ===== TOOL 5: Draft Payment ================================================
@mcp.tool()
def odoo_draft_payment(
    partner_name: str,
    amount: float,
    invoice_name: str = "",
    date: str = "",
    memo: str = "",
) -> str:
    """Prepare a customer payment draft for human review. Does NOT write to Odoo.

    Args:
        partner_name: Customer or vendor name (must exist in Odoo).
        amount: Payment amount.
        invoice_name: Optional Odoo invoice number to reconcile against (e.g. INV/2026/0001).
        date: Payment date YYYY-MM-DD (default: today).
        memo: Payment reference / memo.
    """
    if not partner_name:
        return "ERROR: partner_name is required."
    if amount <= 0:
        return "ERROR: amount must be positive."

    # Look up partner
    try:
        partners = odoo_execute(
            "res.partner", "search_read",
            [[["name", "ilike", partner_name]]],
            {"fields": ["id", "name"], "limit": 5},
        )
    except CircuitOpenError:
        audit_log.warn(EV_CIRCUIT_OPEN, service="odoo", action="draft_payment",
                       partner=partner_name)
        return _odoo_unavailable_message()
    except Exception as e:
        audit_log.error(EV_ODOO_FAIL, action="partner_lookup_payment", error=str(e))
        return f"ERROR connecting to Odoo: {e}"

    if not partners:
        return f"ERROR: No partner found matching '{partner_name}' in Odoo."

    partner = partners[0]
    pay_date = date or now_pkt().strftime("%Y-%m-%d")

    # If invoice specified, look it up
    invoice_id = None
    if invoice_name:
        try:
            invs = odoo_execute(
                "account.move", "search_read",
                [[["name", "=", invoice_name], ["move_type", "=", "out_invoice"]]],
                {"fields": ["id", "name", "amount_residual", "state"], "limit": 1},
            )
            if invs:
                invoice_id = invs[0]["id"]
        except Exception:
            pass

    draft_id = short_id()
    draft = {
        "kind": "payment",
        "partner_id": partner["id"],
        "partner_name": partner["name"],
        "amount": amount,
        "date": pay_date,
        "memo": memo,
        "invoice_name": invoice_name,
        "invoice_id": invoice_id,
        "preview_lines": [],
    }

    preview = [
        f"ODOO PAYMENT DRAFT — ID: {draft_id}",
        "",
        "============ PAYMENT PREVIEW ============",
        f"Type:      Customer Payment (inbound)",
        f"Customer:  {partner['name']} (ID: {partner['id']})",
        f"Amount:    {format_currency(amount)}",
        f"Date:      {pay_date}",
        f"Ref:       {memo or '(none)'}",
        f"Invoice:   {invoice_name or '(not linked)'}",
        "=========================================",
        "",
        "STATUS: Waiting for human approval.",
        "",
        "To register this payment in Odoo, ask the user:",
        f'  "Should I register this payment? (Draft ID: {draft_id})"',
        "",
        f"Then call odoo_confirm_payment with draft_id=\"{draft_id}\".",
        f"To cancel, call odoo_discard_draft with draft_id=\"{draft_id}\".",
    ]

    draft["preview_lines"] = preview
    drafts[draft_id] = draft
    save_draft_to_vault(draft_id, draft)

    return "\n".join(preview)


# ===== TOOL 6: Confirm Payment ==============================================
@mcp.tool()
def odoo_confirm_payment(draft_id: str) -> str:
    """Register a payment in Odoo from an approved draft.
    NEVER call this without explicit user confirmation.

    Args:
        draft_id: The draft ID returned by odoo_draft_payment.
    """
    draft = drafts.get(draft_id)
    if not draft or draft["kind"] != "payment":
        active = [k for k, v in drafts.items() if v["kind"] == "payment"]
        return (
            f"ERROR: Payment draft '{draft_id}' not found.\n"
            f"Active payment drafts: {', '.join(active) or 'none'}"
        )

    # Find a suitable journal (Bank journal)
    try:
        journals = odoo_execute(
            "account.journal", "search_read",
            [[["type", "=", "bank"]]],
            {"fields": ["id", "name"], "limit": 1},
        )
        journal_id = journals[0]["id"] if journals else False
    except Exception:
        journal_id = False

    payment_vals: dict[str, Any] = {
        "payment_type": "inbound",
        "partner_type": "customer",
        "partner_id": draft["partner_id"],
        "amount": draft["amount"],
        "date": draft["date"],
    }
    if journal_id:
        payment_vals["journal_id"] = journal_id
    if draft["memo"]:
        payment_vals["ref"] = draft["memo"]

    try:
        payment_id = odoo_execute("account.payment", "create", [payment_vals])
    except CircuitOpenError:
        _odoo_q.enqueue("create_payment", payload={
            "draft_id": draft_id,
            "payment_vals": payment_vals,
            "partner_name": draft["partner_name"],
        })
        audit_log.warn(EV_CIRCUIT_OPEN, service="odoo", action="confirm_payment",
                       draft_id=draft_id, queued=True)
        return _odoo_unavailable_message(operation="create_payment")
    except Exception as e:
        audit_log.error(EV_ODOO_FAIL, action="create_payment", draft_id=draft_id,
                        error=str(e))
        return (
            f"ERROR creating payment in Odoo: {e}\n\n"
            f"Draft is still saved (ID: {draft_id}). Fix the issue and retry."
        )

    # Read back
    try:
        created = odoo_execute(
            "account.payment", "read",
            [payment_id],
            {"fields": ["name", "state", "amount"]},
        )
        pay = created[0] if isinstance(created, list) else created
        pay_name = pay.get("name", f"ID {payment_id}")
        pay_state = pay.get("state", "draft")
    except Exception:
        pay_name = f"ID {payment_id}"
        pay_state = "draft"

    del drafts[draft_id]

    log_odoo_action("payment_created", {
        "payment_id": payment_id,
        "payment_name": pay_name,
        "partner": draft["partner_name"],
        "amount": draft["amount"],
        "draft_id": draft_id,
    })
    audit_log.info(
        EV_ODOO_ACTION,
        action="payment_created",
        payment_id=payment_id,
        payment_name=pay_name,
        partner=draft["partner_name"],
        amount=draft["amount"],
        draft_id=draft_id,
    )

    return (
        f"PAYMENT REGISTERED SUCCESSFULLY IN ODOO\n"
        f"\n"
        f"Payment:   {pay_name}\n"
        f"Odoo ID:   {payment_id}\n"
        f"Customer:  {draft['partner_name']}\n"
        f"Amount:    {format_currency(draft['amount'])}\n"
        f"State:     {pay_state} (draft — confirm in Odoo UI to post)\n"
        f"Date:      {draft['date']}\n"
        f"\n"
        f"The payment is in DRAFT state. A human can validate it in the\n"
        f"Odoo UI to post it to the ledger.\n"
        f"\n"
        f"Audit log saved to vault/Odoo_Logs/."
    )


# ===== TOOL 7: Get Partners =================================================
@mcp.tool()
def odoo_get_partners(
    search: str = "",
    customer_only: bool = True,
    limit: int = 20,
) -> str:
    """List contacts/partners from Odoo. Read-only, executes immediately.

    Args:
        search: Filter by name (partial match). Empty = list all.
        customer_only: If True, only return customers (not suppliers).
        limit: Max results (default 20).
    """
    domain: list = [["is_company", "=", True]]
    if search:
        domain.append(["name", "ilike", search])
    if customer_only:
        domain.append(["customer_rank", ">", 0])

    try:
        partners = odoo_execute(
            "res.partner", "search_read",
            [domain],
            {
                "fields": ["id", "name", "email", "phone", "city",
                           "country_id", "customer_rank", "supplier_rank"],
                "limit": limit,
                "order": "name asc",
            },
        )
    except CircuitOpenError:
        audit_log.warn(EV_CIRCUIT_OPEN, service="odoo", action="get_partners")
        return _odoo_unavailable_message()
    except Exception as e:
        audit_log.error(EV_ODOO_FAIL, action="get_partners", error=str(e))
        return f"ERROR reading partners from Odoo: {e}"

    if not partners:
        suffix = f' matching "{search}"' if search else ""
        return f"No partners found{suffix}."

    lines = [
        f"ODOO PARTNERS ({len(partners)} found)",
        "=" * 50,
        "",
    ]

    for p in partners:
        country = p.get("country_id")
        country_name = country[1] if isinstance(country, (list, tuple)) else "N/A"
        lines.extend([
            f"  [{p['id']}] {p['name']}",
            f"       Email: {p.get('email') or 'N/A'}  |  "
            f"Phone: {p.get('phone') or 'N/A'}",
            f"       City: {p.get('city') or 'N/A'}  |  Country: {country_name}",
            "",
        ])

    return "\n".join(lines)


# ===== TOOL 8: Get Products =================================================
@mcp.tool()
def odoo_get_products(
    search: str = "",
    limit: int = 20,
) -> str:
    """List products from Odoo. Read-only, executes immediately.

    Args:
        search: Filter by product name (partial match). Empty = list all.
        limit: Max results (default 20).
    """
    domain: list = [["sale_ok", "=", True]]
    if search:
        domain.append(["name", "ilike", search])

    try:
        products = odoo_execute(
            "product.product", "search_read",
            [domain],
            {
                "fields": ["id", "name", "list_price", "default_code",
                           "type", "qty_available"],
                "limit": limit,
                "order": "name asc",
            },
        )
    except CircuitOpenError:
        audit_log.warn(EV_CIRCUIT_OPEN, service="odoo", action="get_products")
        return _odoo_unavailable_message()
    except Exception as e:
        audit_log.error(EV_ODOO_FAIL, action="get_products", error=str(e))
        return f"ERROR reading products from Odoo: {e}"

    if not products:
        suffix = f' matching "{search}"' if search else ""
        return f"No products found{suffix}."

    lines = [
        f"ODOO PRODUCTS ({len(products)} found)",
        "=" * 50,
        "",
    ]

    for p in products:
        lines.extend([
            f"  [{p['id']}] {p['name']}",
            f"       SKU: {p.get('default_code') or 'N/A'}  |  "
            f"Price: {format_currency(p['list_price'])}  |  "
            f"Type: {p.get('type', 'N/A')}  |  "
            f"Stock: {p.get('qty_available', 'N/A')}",
            "",
        ])

    return "\n".join(lines)


# ===== TOOL 9: Financial Report Summary =====================================
@mcp.tool()
def odoo_report_summary(period: str = "this_month") -> str:
    """Generate a financial summary from Odoo data. Read-only.

    Args:
        period: Time period — this_month, last_month, this_year, all.
    """
    today = now_pkt().date()

    if period == "this_month":
        start = today.replace(day=1).isoformat()
        end = today.isoformat()
        label = f"This Month ({today.strftime('%B %Y')})"
    elif period == "last_month":
        first_this = today.replace(day=1)
        last_day_prev = first_this - timedelta(days=1)
        start = last_day_prev.replace(day=1).isoformat()
        end = last_day_prev.isoformat()
        label = f"Last Month ({last_day_prev.strftime('%B %Y')})"
    elif period == "this_year":
        start = today.replace(month=1, day=1).isoformat()
        end = today.isoformat()
        label = f"This Year ({today.year})"
    else:
        start = "2000-01-01"
        end = today.isoformat()
        label = "All Time"

    try:
        # Customer invoices
        out_invoices = odoo_execute(
            "account.move", "search_read",
            [[
                ["move_type", "=", "out_invoice"],
                ["invoice_date", ">=", start],
                ["invoice_date", "<=", end],
            ]],
            {"fields": ["amount_total", "amount_residual", "state",
                         "payment_state"]},
        )

        # Vendor bills
        in_invoices = odoo_execute(
            "account.move", "search_read",
            [[
                ["move_type", "=", "in_invoice"],
                ["invoice_date", ">=", start],
                ["invoice_date", "<=", end],
            ]],
            {"fields": ["amount_total", "amount_residual", "state"]},
        )

        # Payments received
        payments_in = odoo_execute(
            "account.payment", "search_read",
            [[
                ["payment_type", "=", "inbound"],
                ["date", ">=", start],
                ["date", "<=", end],
                ["state", "=", "posted"],
            ]],
            {"fields": ["amount"]},
        )

        # Payments sent
        payments_out = odoo_execute(
            "account.payment", "search_read",
            [[
                ["payment_type", "=", "outbound"],
                ["date", ">=", start],
                ["date", "<=", end],
                ["state", "=", "posted"],
            ]],
            {"fields": ["amount"]},
        )

    except CircuitOpenError:
        audit_log.warn(EV_CIRCUIT_OPEN, service="odoo", action="report_summary",
                       period=period)
        return _odoo_unavailable_message()
    except Exception as e:
        audit_log.error(EV_ODOO_FAIL, action="report_summary", period=period,
                        error=str(e))
        return f"ERROR generating report from Odoo: {e}"

    # Crunch numbers
    rev_total = sum(i["amount_total"] for i in out_invoices)
    rev_outstanding = sum(i["amount_residual"] for i in out_invoices)
    rev_draft = sum(i["amount_total"] for i in out_invoices
                     if i["state"] == "draft")
    rev_posted = sum(i["amount_total"] for i in out_invoices
                      if i["state"] == "posted")
    rev_paid = sum(i["amount_total"] for i in out_invoices
                    if i.get("payment_state") == "paid")

    exp_total = sum(i["amount_total"] for i in in_invoices)
    exp_outstanding = sum(i["amount_residual"] for i in in_invoices)

    cash_in = sum(p["amount"] for p in payments_in)
    cash_out = sum(p["amount"] for p in payments_out)

    report = [
        f"ODOO FINANCIAL SUMMARY — {label}",
        "=" * 60,
        f"Period: {start} to {end}",
        "",
        "REVENUE (Customer Invoices)",
        f"  Total invoiced:    {format_currency(rev_total)}  "
        f"({len(out_invoices)} invoices)",
        f"  - Draft:           {format_currency(rev_draft)}",
        f"  - Posted:          {format_currency(rev_posted)}",
        f"  - Fully paid:      {format_currency(rev_paid)}",
        f"  Outstanding (A/R): {format_currency(rev_outstanding)}",
        "",
        "EXPENSES (Vendor Bills)",
        f"  Total billed:      {format_currency(exp_total)}  "
        f"({len(in_invoices)} bills)",
        f"  Outstanding (A/P): {format_currency(exp_outstanding)}",
        "",
        "CASH FLOW",
        f"  Payments received: {format_currency(cash_in)}  "
        f"({len(payments_in)} payments)",
        f"  Payments sent:     {format_currency(cash_out)}  "
        f"({len(payments_out)} payments)",
        f"  Net cash flow:     {format_currency(cash_in - cash_out)}",
        "",
        "NET POSITION",
        f"  Revenue - Expenses:  {format_currency(rev_total - exp_total)}",
        "=" * 60,
    ]

    # Save to vault
    log_odoo_action("report_generated", {
        "period": period,
        "revenue": rev_total,
        "expenses": exp_total,
        "net_cash": cash_in - cash_out,
    })
    audit_log.info(
        EV_ODOO_ACTION,
        action="report_generated",
        period=period,
        revenue=round(rev_total, 2),
        expenses=round(exp_total, 2),
        net_cash=round(cash_in - cash_out, 2),
    )

    return "\n".join(report)


# ===== TOOL 10: List Drafts =================================================
@mcp.tool()
def odoo_list_drafts() -> str:
    """List all pending Odoo drafts waiting for human approval."""
    if not drafts:
        return "No pending Odoo drafts."

    lines = ["PENDING ODOO DRAFTS", "=" * 40, ""]

    for draft_id, draft in drafts.items():
        kind = draft["kind"]
        partner = draft.get("partner_name", "N/A")
        if kind == "invoice":
            amount = format_currency(draft.get("total", 0))
        else:
            amount = format_currency(draft.get("amount", 0))
        lines.extend([
            f"  Draft ID: {draft_id}",
            f"    Type:    {kind}",
            f"    Partner: {partner}",
            f"    Amount:  {amount}",
            "",
        ])

    lines.append(f"Total: {len(drafts)} draft(s) awaiting approval.")
    return "\n".join(lines)


# ===== TOOL 11: Discard Draft ===============================================
@mcp.tool()
def odoo_discard_draft(draft_id: str) -> str:
    """Discard an Odoo draft without executing it.

    Args:
        draft_id: The draft ID to discard.
    """
    if draft_id not in drafts:
        return (
            f"Draft '{draft_id}' not found. "
            f"It may have been already confirmed or discarded."
        )

    kind = drafts[draft_id]["kind"]
    del drafts[draft_id]

    log_odoo_action("draft_discarded", {
        "draft_id": draft_id,
        "kind": kind,
    })
    audit_log.info(EV_ODOO_ACTION, action="draft_discarded",
                   draft_id=draft_id, kind=kind)

    return (
        f"Draft '{draft_id}' ({kind}) has been discarded. "
        f"No changes were made in Odoo."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Odoo MCP server (Gold Tier)...")
    logger.info("Odoo URL: %s  |  DB: %s  |  User: %s", ODOO_URL, ODOO_DB, ODOO_USER)
    mcp.run(transport="stdio")
