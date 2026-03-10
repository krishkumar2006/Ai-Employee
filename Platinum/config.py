"""
config.py — Platinum Tier
===========================
Mode-aware central configuration for the AI Employee system.

DEPLOYMENT_MODE controls what this instance is allowed to do:
  cloud → email triage, plan generation, social DRAFTS only
           (no sends, no payments, no Playwright browser posting)
  local → full access: approvals, WhatsApp, payments, final sends,
           Playwright posting for Twitter/Meta/LinkedIn

Env file loading order (later overrides earlier):
  1. .env              (base / fallback)
  2. .env.<mode>       (.env.cloud or .env.local — mode-specific)
  3. actual os.environ (CI/CD, docker, systemd — always wins)

Usage
-----
    from config import cfg

    # Guard a sensitive action
    cfg.assert_allowed("email_send")          # raises ModeError if on cloud

    # Read env vars
    api_key = cfg.require("ANTHROPIC_API_KEY")
    timeout = int(cfg.get("CLAUDE_TIMEOUT", "120"))

    # Branch on mode
    if cfg.is_cloud():
        generate_draft()
    else:
        post_via_playwright()
"""

import os
import sys
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Simple .env parser (no external deps)
# ---------------------------------------------------------------------------

def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict. Ignores comments and blank lines."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        # Strip inline comments and surrounding quotes
        val = val.split(" #")[0].strip().strip('"').strip("'")
        env[key.strip()] = val
    return env


# ---------------------------------------------------------------------------
# Determine mode (check os.environ first, then .env)
# ---------------------------------------------------------------------------

_base_env  = _parse_env_file(PROJECT_ROOT / ".env")
_mode_raw  = os.environ.get("DEPLOYMENT_MODE") or _base_env.get("DEPLOYMENT_MODE", "local")
_mode      = _mode_raw.lower().strip()
_mode_file = PROJECT_ROOT / f".env.{_mode}"
_mode_env  = _parse_env_file(_mode_file)

# Merge: base < .env.mode < os.environ (os.environ always wins)
_merged: dict[str, str] = {**_base_env, **_mode_env}
for k, v in _merged.items():
    os.environ.setdefault(k, v)

# DRY_RUN: read after merge so .env.local / .env.cloud can set it
_DRY_RUN: bool = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------

# Actions allowed on CLOUD (read / draft / plan — never send or post)
CLOUD_ALLOWED: frozenset[str] = frozenset({
    "email_read",            # gmail_watcher.py — reads inbox, creates task cards
    "email_triage",          # classify priority, extract metadata
    "email_draft",           # generate draft reply text (saved to vault, NOT sent)
    "plan_generate",         # ralph_loop / orchestrator — generate Plans/*.md
    "social_draft",          # generate Twitter/Meta/LinkedIn DRAFT text (status: draft)
    "social_summary",        # twitter_summary.py, meta_summary.py — read analytics
    "odoo_read",             # ceo_briefing — read invoices/partners (NO writes)
    "ceo_briefing",          # generate weekly briefing markdown
    "task_claim",            # claim_agent.py — Needs_Action → In_Progress
    "vault_sync",            # vault_sync.sh / vault_sync_windows.py
    "ralph_loop",            # autonomous multi-step planning
    "health_check",          # health_check.sh / AuditLogger
})

# Actions BLOCKED on cloud
CLOUD_BLOCKED: frozenset[str] = frozenset({
    "email_send",            # no SMTP from cloud (email_mcp.js send_email)
    "odoo_confirm",          # no invoice/payment confirmation from cloud
    "odoo_write",            # no Odoo create/update from cloud
    "whatsapp_read",         # WhatsApp requires phone session — LOCAL only
    "whatsapp_send",         # WhatsApp send — LOCAL only
    "social_post_twitter",   # Playwright twitter_poster.py — LOCAL only
    "social_post_meta",      # Playwright meta_poster.py — LOCAL only
    "social_post_linkedin",  # Playwright linkedin_poster.py — LOCAL only
    "approval_execute",      # run approved actions — LOCAL only
    "payment_process",       # financial payments — LOCAL only
    # Additional hard blocks — sensitive domains never touched from cloud
    "banking_read",          # bank account data — LOCAL only
    "banking_write",         # bank transfers — LOCAL only
    "credential_access",     # reading stored passwords/keys — LOCAL only
    "vault_secrets_read",    # vault/Secrets/ — LOCAL only
})

# Local: everything is allowed
LOCAL_ALLOWED: frozenset[str] = CLOUD_ALLOWED | CLOUD_BLOCKED
LOCAL_BLOCKED: frozenset[str] = frozenset()

# Mapping for external introspection
ACTION_DESCRIPTIONS: dict[str, str] = {
    "email_read":           "Read Gmail inbox (no sends)",
    "email_triage":         "Classify and prioritize emails",
    "email_draft":          "Generate draft email replies",
    "email_send":           "Send emails via SMTP/MCP",
    "plan_generate":        "Generate Plans/*.md via Claude",
    "social_draft":         "Generate social media draft text",
    "social_summary":       "Read social media analytics",
    "social_post_twitter":  "Post to Twitter/X via Playwright",
    "social_post_meta":     "Post to Facebook/Instagram via Playwright",
    "social_post_linkedin": "Post to LinkedIn via Playwright",
    "odoo_read":            "Read Odoo data (invoices, partners)",
    "odoo_confirm":         "Confirm Odoo invoices/payments",
    "odoo_write":           "Create/update Odoo records",
    "whatsapp_read":        "Monitor WhatsApp messages",
    "whatsapp_send":        "Send WhatsApp replies",
    "approval_execute":     "Execute human-approved actions",
    "payment_process":      "Process financial payments",
    "banking_read":         "Read bank account / transaction data",
    "banking_write":        "Initiate bank transfers or payments",
    "credential_access":    "Access stored credentials / passwords",
    "vault_secrets_read":   "Read vault/Secrets/ directory",
    "task_claim":           "Claim tasks from Needs_Action",
    "vault_sync":           "Sync vault to/from GitHub",
    "ralph_loop":           "Run autonomous Claude loop",
    "health_check":         "System health monitoring",
    "ceo_briefing":         "Generate CEO briefing report",
}


# ---------------------------------------------------------------------------
# ModeError
# ---------------------------------------------------------------------------

class ModeError(PermissionError):
    """
    Raised when an action is attempted that is blocked in the current
    DEPLOYMENT_MODE.  Callers should catch this and skip gracefully.
    """
    pass


# ---------------------------------------------------------------------------
# Config class
# ---------------------------------------------------------------------------

DeploymentMode = Literal["cloud", "local"]


class Config:
    """
    Mode-aware config singleton.  Import as `from config import cfg`.
    """

    def __init__(self) -> None:
        self.mode: DeploymentMode = "cloud" if _mode == "cloud" else "local"
        self._allowed = CLOUD_ALLOWED if self.is_cloud() else LOCAL_ALLOWED
        self._blocked = CLOUD_BLOCKED if self.is_cloud() else LOCAL_BLOCKED
        self.project_root: Path = PROJECT_ROOT
        self.vault: Path = PROJECT_ROOT / "vault"
        self.dry_run: bool = _DRY_RUN

    # ------------------------------------------------------------------
    # Mode helpers
    # ------------------------------------------------------------------

    def is_cloud(self) -> bool:
        """True when running on the Oracle Cloud VM."""
        return self.mode == "cloud"

    def is_local(self) -> bool:
        """True when running on the local Windows machine."""
        return self.mode == "local"

    def is_dry_run(self) -> bool:
        """
        True when DRY_RUN=true is set in env.
        All destructive operations should check this before executing.
        """
        return self.dry_run

    def dry_run_guard(self, description: str, component: str = "") -> bool:
        """
        Call before any destructive action.

        If DRY_RUN is active, prints a '[DRY RUN]' message and returns True
        (caller should skip the real action).  Returns False when DRY_RUN
        is off (caller should proceed normally).

        Usage:
            if cfg.dry_run_guard(f"send email to {address}", "gmail_watcher"):
                return "DRY RUN: email not sent"
            # ... perform real send ...
        """
        if not self.dry_run:
            return False
        prefix = f"[{component}] " if component else ""
        print(f"{prefix}[DRY RUN] Skipped: {description}")
        return True

    # ------------------------------------------------------------------
    # Action guards
    # ------------------------------------------------------------------

    def allowed(self, action: str) -> bool:
        """Return True if action is permitted in the current mode."""
        return action in self._allowed and action not in self._blocked

    def assert_allowed(self, action: str, component: str = "") -> None:
        """
        Raise ModeError if action is blocked in the current mode.
        Call this at the top of any function that performs a sensitive action.

        Example:
            cfg.assert_allowed("email_send", "email_mcp")
        """
        if not self.allowed(action):
            prefix = f"[{component}] " if component else ""
            desc   = ACTION_DESCRIPTIONS.get(action, action)
            raise ModeError(
                f"{prefix}'{action}' ({desc}) is BLOCKED in {self.mode.upper()} mode. "
                f"This action must run on the LOCAL machine."
            )

    # ------------------------------------------------------------------
    # Env var access
    # ------------------------------------------------------------------

    def get(self, key: str, default: str = "") -> str:
        """Read an env var, returning default if not set."""
        return os.environ.get(key, default)

    def require(self, key: str) -> str:
        """
        Read an env var or raise EnvironmentError if missing/empty.
        Use for keys that are mandatory in the current mode.
        """
        val = os.environ.get(key, "").strip()
        if not val:
            raise EnvironmentError(
                f"Required env var '{key}' is not set. "
                f"Check .env.{self.mode} (mode={self.mode})"
            )
        return val

    def has(self, key: str) -> bool:
        """True if env var is set and non-empty."""
        return bool(os.environ.get(key, "").strip())

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def summary(self) -> str:
        lines = [
            f"DEPLOYMENT_MODE : {self.mode.upper()}",
            f"DRY_RUN         : {'YES — no destructive actions will execute' if self.dry_run else 'no'}",
            f"Env file loaded : .env.{self.mode}" if _mode_file.exists() else
            f"Env file        : .env.{self.mode} (NOT FOUND — using .env only)",
            f"ANTHROPIC_API_KEY : {'SET' if self.has('ANTHROPIC_API_KEY') else 'MISSING'}",
            f"GMAIL_ADDRESS     : {self.get('GMAIL_ADDRESS', '(not set)')}",
            f"ODOO_URL          : {self.get('ODOO_URL', '(not set)')}",
        ]
        if self.is_cloud():
            lines += [
                "",
                "CLOUD MODE — blocked actions:",
                *[f"  - {a}: {ACTION_DESCRIPTIONS.get(a, a)}" for a in sorted(self._blocked)],
            ]
        return "\n".join(lines)

    def allowed_actions(self) -> list[str]:
        return sorted(self._allowed - self._blocked)

    def blocked_actions(self) -> list[str]:
        return sorted(self._blocked)


# ---------------------------------------------------------------------------
# Singleton — import this everywhere
# ---------------------------------------------------------------------------
cfg = Config()


# ---------------------------------------------------------------------------
# CLI: python config.py  →  print mode summary
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 55)
    print("  AI Employee — Config")
    print("=" * 55)
    print(cfg.summary())
    print()
    print("Allowed actions:")
    for a in cfg.allowed_actions():
        print(f"  [+] {a:<25}  {ACTION_DESCRIPTIONS.get(a, '')}")
    if cfg.blocked_actions():
        print()
        print("Blocked actions (current mode):")
        for a in cfg.blocked_actions():
            print(f"  [-] {a:<25}  {ACTION_DESCRIPTIONS.get(a, '')}")
