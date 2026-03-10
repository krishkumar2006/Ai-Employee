"""
setup_vault_structure.py — Platinum Tier
=========================================
Creates the domain-based vault folder hierarchy and places .gitkeep
placeholders so empty folders are tracked by Git.

Run once on any machine (local or VM) after cloning the repo:
    python scripts/setup_vault_structure.py

Safe to re-run — never deletes existing files.
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root (one level up from /scripts/)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VAULT = PROJECT_ROOT / "vault"

# ---------------------------------------------------------------------------
# Domains — add more as needed
# ---------------------------------------------------------------------------
DOMAINS = ["email", "odoo", "social", "calendar", "general"]

# ---------------------------------------------------------------------------
# Agents — must match --agent names passed to claim_agent.py
# ---------------------------------------------------------------------------
AGENTS = ["orchestrator", "ralph", "watcher_email", "watcher_social", "watcher_calendar"]

# ---------------------------------------------------------------------------
# Folder tree
#   key   → parent path (relative to vault/)
#   value → list of subfolders to create
# ---------------------------------------------------------------------------
STRUCTURE: dict[str, list[str]] = {
    # Workflow pipeline — domain-namespaced
    "Needs_Action":     DOMAINS,
    "In_Progress":      AGENTS,
    "Plans":            DOMAINS,
    "Pending_Approval": DOMAINS,
    "Approved":         DOMAINS,   # cloud drafts approved by human / auto
    "Rejected":         DOMAINS,   # cloud drafts rejected by human
    "Done":             DOMAINS,
    # Flat folders
    "Updates":          [],
    "Inbox":            [],
    # Existing (kept for Gold Tier compatibility)
    "Logs":             [],
    "Queue":            [],
    "Odoo_Drafts":      [],
    "Odoo_Logs":        [],
    "LinkedIn_Drafts":  [],
    "LinkedIn_Posted":  [],
    "Meta_Drafts":      [],
    "Meta_Posted":      [],
    "Twitter_Drafts":   [],
    "Twitter_Posted":   [],
    "Sent_Emails":      [],
}


def create_gitkeep(folder: Path) -> None:
    """Place a .gitkeep in folder so Git tracks the empty directory."""
    gk = folder / ".gitkeep"
    if not gk.exists():
        gk.touch()


def setup() -> None:
    created = 0
    skipped = 0

    for parent_name, subfolders in STRUCTURE.items():
        parent = VAULT / parent_name
        parent.mkdir(parents=True, exist_ok=True)

        if not subfolders:
            # Flat folder — just ensure .gitkeep exists
            create_gitkeep(parent)
            skipped += 1
        else:
            for sub in subfolders:
                path = parent / sub
                if path.exists():
                    skipped += 1
                else:
                    path.mkdir(parents=True, exist_ok=True)
                    created += 1
                    print(f"  [+] {path.relative_to(PROJECT_ROOT)}")
                create_gitkeep(path)

    print(f"\nDone. Created: {created}  Already existed: {skipped}")
    print(f"Vault root: {VAULT}")


if __name__ == "__main__":
    print("Setting up vault domain structure...\n")
    setup()
