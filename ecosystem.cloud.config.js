/**
 * ecosystem.cloud.config.js — Platinum Tier
 * ============================================
 * PM2 process definitions for the Oracle Always Free VM (Cloud domain).
 *
 * What runs on CLOUD:
 *   ✓ orchestrator      — scheduler (cloud schedule: no Playwright tasks)
 *   ✓ gmail_watcher     — reads inbox, creates task cards (draft-only)
 *   ✓ social_drafter    — generates Twitter/Meta/LinkedIn DRAFT text via Claude
 *   ✓ claim_agent       — picks up Needs_Action tasks atomically
 *   ✓ ralph_loop        — autonomous planning loop
 *   ✓ watchdog          — restarts crashed processes
 *
 * What does NOT run on CLOUD:
 *   ✗ twitter_poster    — Playwright (LOCAL only)
 *   ✗ meta_poster       — Playwright (LOCAL only)
 *   ✗ linkedin_poster   — Playwright (LOCAL only)
 *   ✗ whatsapp_watcher  — requires phone QR (LOCAL only)
 *
 * Usage:
 *   pm2 start ecosystem.cloud.config.js
 *   pm2 save
 *   pm2 startup   ← run the printed command as root
 */

const REPO   = "/home/ubuntu/ai-employee";
const PYTHON = `${REPO}/.venv/bin/python`;
const VAULT  = `${REPO}/vault`;

module.exports = {
  apps: [

    // -------------------------------------------------------------------------
    // Watchdog — supervises orchestrator, restarts on crash
    // -------------------------------------------------------------------------
    {
      name:           "watchdog",
      script:         "watchdog.py",
      interpreter:    PYTHON,
      cwd:            REPO,
      watch:          false,
      restart_delay:  3000,
      max_restarts:   20,
      min_uptime:     "10s",
      env: {
        PYTHONUNBUFFERED: "1",
        DEPLOYMENT_MODE:  "cloud",
      },
      error_file:   `${VAULT}/Logs/pm2_watchdog_err.log`,
      out_file:     `${VAULT}/Logs/pm2_watchdog_out.log`,
    },

    // -------------------------------------------------------------------------
    // Orchestrator — central scheduler
    // -------------------------------------------------------------------------
    {
      name:           "orchestrator",
      script:         "orchestrator.py",
      interpreter:    PYTHON,
      cwd:            REPO,
      watch:          false,
      restart_delay:  5000,
      max_restarts:   10,
      min_uptime:     "15s",
      env: {
        PYTHONUNBUFFERED: "1",
        DEPLOYMENT_MODE:  "cloud",
      },
      error_file:   `${VAULT}/Logs/pm2_orchestrator_err.log`,
      out_file:     `${VAULT}/Logs/pm2_orchestrator_out.log`,
    },

    // -------------------------------------------------------------------------
    // Gmail Watcher — reads inbox, creates task cards (no sending)
    // -------------------------------------------------------------------------
    {
      name:           "gmail_watcher",
      script:         "watchers/gmail_watcher.py",
      interpreter:    PYTHON,
      cwd:            REPO,
      watch:          false,
      restart_delay:  10000,
      max_restarts:   5,
      min_uptime:     "30s",
      env: {
        PYTHONUNBUFFERED: "1",
        DEPLOYMENT_MODE:  "cloud",
      },
      error_file:   `${VAULT}/Logs/pm2_gmail_err.log`,
      out_file:     `${VAULT}/Logs/pm2_gmail_out.log`,
    },

    // -------------------------------------------------------------------------
    // Social Drafter — generates drafts from vault/Needs_Action/social/
    // -------------------------------------------------------------------------
    {
      name:           "social_drafter",
      script:         "watchers/social_drafter.py",
      interpreter:    PYTHON,
      cwd:            REPO,
      args:           "--poll 120",
      watch:          false,
      restart_delay:  15000,
      max_restarts:   5,
      min_uptime:     "30s",
      env: {
        PYTHONUNBUFFERED: "1",
        DEPLOYMENT_MODE:  "cloud",
      },
      error_file:   `${VAULT}/Logs/pm2_social_drafter_err.log`,
      out_file:     `${VAULT}/Logs/pm2_social_drafter_out.log`,
    },

    // -------------------------------------------------------------------------
    // Claim Agent — atomic Needs_Action → In_Progress mover
    // -------------------------------------------------------------------------
    {
      name:           "claim_agent",
      script:         "scripts/claim_agent.py",
      interpreter:    PYTHON,
      cwd:            REPO,
      args:           "--agent orchestrator --poll 3",
      watch:          false,
      restart_delay:  5000,
      max_restarts:   20,
      min_uptime:     "5s",
      env: {
        PYTHONUNBUFFERED: "1",
        DEPLOYMENT_MODE:  "cloud",
      },
      error_file:   `${VAULT}/Logs/pm2_claim_agent_err.log`,
      out_file:     `${VAULT}/Logs/pm2_claim_agent_out.log`,
    },

    // -------------------------------------------------------------------------
    // Ralph Loop — autonomous multi-step Claude planning
    // -------------------------------------------------------------------------
    {
      name:           "ralph-loop",
      script:         "ralph_loop.py",
      interpreter:    PYTHON,
      cwd:            REPO,
      watch:          false,
      restart_delay:  10000,
      max_restarts:   5,
      min_uptime:     "30s",
      env: {
        PYTHONUNBUFFERED: "1",
        DEPLOYMENT_MODE:  "cloud",
      },
      error_file:   `${VAULT}/Logs/pm2_ralph_err.log`,
      out_file:     `${VAULT}/Logs/pm2_ralph_out.log`,
    },

  ]
};
