/**
 * ecosystem.local.config.js — Platinum Tier
 * ============================================
 * PM2 process definitions for the LOCAL Windows machine (full-access domain).
 *
 * What runs on LOCAL:
 *   ✓ orchestrator       — full schedule (Playwright tasks enabled)
 *   ✓ whatsapp_watcher   — Playwright browser, phone QR session
 *   ✓ twitter_poster     — Playwright browser, posts approved drafts
 *   ✓ meta_poster        — Playwright browser, posts approved drafts
 *   ✓ linkedin_poster    — Playwright browser, posts approved drafts
 *   ✓ claim_agent        — Needs_Action tasks (local domain tasks)
 *   ✓ ralph-loop         — autonomous planning (full permissions)
 *
 * What does NOT run on LOCAL (handled by Cloud):
 *   The cloud handles: gmail_watcher, social_drafter, ceo_briefing (read),
 *   vault_sync cron, claim_agent for cloud domains
 *
 * Note on Playwright (headless: false):
 *   twitter_poster, meta_poster, linkedin_poster, whatsapp_watcher all use
 *   headless=False. PM2 on Windows requires a real desktop session.
 *   Run PM2 from a logged-in terminal, not as a service.
 *
 * Usage (Windows — run as normal user, NOT administrator):
 *   pm2 start ecosystem.local.config.js
 *   pm2 save
 *   pm2 startup      ← copy + run the printed command
 */

const REPO   = "D:\\Heck ---0\\AI Empolyee";
const PYTHON = `${REPO}\\.venv\\Scripts\\python.exe`;
const VAULT  = `${REPO}\\vault`;

module.exports = {
  apps: [

    // -------------------------------------------------------------------------
    // Orchestrator — full local schedule
    // -------------------------------------------------------------------------
    {
      name:           "orchestrator-local",
      script:         "orchestrator.py",
      interpreter:    PYTHON,
      cwd:            REPO,
      watch:          false,
      restart_delay:  5000,
      max_restarts:   10,
      min_uptime:     "15s",
      env: {
        PYTHONUNBUFFERED: "1",
        DEPLOYMENT_MODE:  "local",
      },
      error_file:   `${VAULT}\\Logs\\pm2_orchestrator_err.log`,
      out_file:     `${VAULT}\\Logs\\pm2_orchestrator_out.log`,
    },

    // -------------------------------------------------------------------------
    // Claim Agent — local domain tasks (approval domain, WhatsApp domain)
    // -------------------------------------------------------------------------
    {
      name:           "claim-agent-local",
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
        DEPLOYMENT_MODE:  "local",
      },
    },

    // -------------------------------------------------------------------------
    // WhatsApp Watcher — Playwright (requires phone + real browser)
    // IMPORTANT: start this manually first to scan QR code.
    //   python watchers/whatsapp_watcher.py
    // Once session is saved, PM2 can restart it automatically.
    // -------------------------------------------------------------------------
    {
      name:           "whatsapp_watcher",
      script:         "watchers/whatsapp_watcher.py",
      interpreter:    PYTHON,
      cwd:            REPO,
      watch:          false,
      restart_delay:  15000,
      max_restarts:   3,
      min_uptime:     "60s",
      env: {
        PYTHONUNBUFFERED: "1",
        DEPLOYMENT_MODE:  "local",
      },
      error_file:   `${VAULT}\\Logs\\pm2_whatsapp_err.log`,
      out_file:     `${VAULT}\\Logs\\pm2_whatsapp_out.log`,
    },

    // -------------------------------------------------------------------------
    // Twitter/X Poster — Playwright, posts approved drafts from Twitter_Drafts/
    // Human sets status: ready → this script posts via browser automation
    // -------------------------------------------------------------------------
    {
      name:           "twitter_poster",
      script:         "watchers/twitter_poster.py",
      interpreter:    PYTHON,
      cwd:            REPO,
      watch:          false,
      restart_delay:  30000,
      max_restarts:   3,
      min_uptime:     "60s",
      env: {
        PYTHONUNBUFFERED: "1",
        DEPLOYMENT_MODE:  "local",
      },
      error_file:   `${VAULT}\\Logs\\pm2_twitter_err.log`,
      out_file:     `${VAULT}\\Logs\\pm2_twitter_out.log`,
    },

    // -------------------------------------------------------------------------
    // Meta Poster — Playwright (Facebook + Instagram)
    // Posts approved drafts from Meta_Drafts/
    // -------------------------------------------------------------------------
    {
      name:           "meta_poster",
      script:         "watchers/meta_poster.py",
      interpreter:    PYTHON,
      cwd:            REPO,
      watch:          false,
      restart_delay:  30000,
      max_restarts:   3,
      min_uptime:     "60s",
      env: {
        PYTHONUNBUFFERED: "1",
        DEPLOYMENT_MODE:  "local",
      },
      error_file:   `${VAULT}\\Logs\\pm2_meta_err.log`,
      out_file:     `${VAULT}\\Logs\\pm2_meta_out.log`,
    },

    // -------------------------------------------------------------------------
    // LinkedIn Poster — Playwright
    // Posts approved drafts from LinkedIn_Drafts/
    // -------------------------------------------------------------------------
    {
      name:           "linkedin_poster",
      script:         "watchers/linkedin_poster.py",
      interpreter:    PYTHON,
      cwd:            REPO,
      watch:          false,
      restart_delay:  30000,
      max_restarts:   3,
      min_uptime:     "60s",
      env: {
        PYTHONUNBUFFERED: "1",
        DEPLOYMENT_MODE:  "local",
      },
      error_file:   `${VAULT}\\Logs\\pm2_linkedin_err.log`,
      out_file:     `${VAULT}\\Logs\\pm2_linkedin_out.log`,
    },

    // -------------------------------------------------------------------------
    // Ralph Loop (local) — full permissions for autonomous tasks
    // -------------------------------------------------------------------------
    {
      name:           "ralph-loop-local",
      script:         "ralph_loop.py",
      interpreter:    PYTHON,
      cwd:            REPO,
      watch:          false,
      restart_delay:  10000,
      max_restarts:   5,
      min_uptime:     "30s",
      env: {
        PYTHONUNBUFFERED: "1",
        DEPLOYMENT_MODE:  "local",
      },
    },

  ]
};
