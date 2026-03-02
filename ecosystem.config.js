// PM2 Ecosystem File — AI Employee (Gold Tier)
// =============================================
// Manages long-running WATCHER and ORCHESTRATOR processes.
//
// NOTE: MCP servers (email, odoo, meta, twitter, browser) are NOT here.
// Claude Code launches MCP servers automatically per session from .mcp.json.
// PM2 only manages processes that run continuously in the background.
//
// Usage:
//   pm2 start ecosystem.config.js          # Start all
//   pm2 start ecosystem.config.js --only orchestrator   # One app
//   pm2 list                               # Status
//   pm2 logs                               # All logs
//   pm2 logs gmail-watcher                 # One process
//   pm2 save                               # Persist across reboots
//   pm2 startup                            # Generate startup script

const path = require("path");
const ROOT = __dirname;

// Load env vars from .env for use in this config file
require("dotenv").config({ path: path.join(ROOT, ".env") });

module.exports = {
  apps: [

    // -----------------------------------------------------------------------
    // Orchestrator — central controller, polls all sources
    // -----------------------------------------------------------------------
    {
      name: "orchestrator",
      script: "orchestrator.py",
      interpreter: "python",
      cwd: ROOT,
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      out_file: "./logs/pm2-orchestrator-out.log",
      error_file: "./logs/pm2-orchestrator-err.log",
      merge_logs: true,
      env: {
        PYTHONUNBUFFERED: "1",
        GMAIL_ADDRESS: process.env.GMAIL_ADDRESS || "",
        GMAIL_APP_PASSWORD: process.env.GMAIL_APP_PASSWORD || "",
        CLAUDE_TIMEOUT: process.env.CLAUDE_TIMEOUT || "120",
        SENSITIVE_ACTIONS: process.env.SENSITIVE_ACTIONS || "",
        AUTO_APPROVE_BELOW: process.env.AUTO_APPROVE_BELOW || "low",
      },
    },

    // -----------------------------------------------------------------------
    // Gmail Watcher — polls inbox, surfaces action items to vault
    // -----------------------------------------------------------------------
    {
      name: "gmail-watcher",
      script: "watchers/gmail_watcher.py",
      interpreter: "python",
      cwd: ROOT,
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 10000,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      out_file: "./logs/pm2-gmail-out.log",
      error_file: "./logs/pm2-gmail-err.log",
      merge_logs: true,
      env: {
        PYTHONUNBUFFERED: "1",
        GMAIL_ADDRESS: process.env.GMAIL_ADDRESS || "",
        GMAIL_APP_PASSWORD: process.env.GMAIL_APP_PASSWORD || "",
        GMAIL_POLL_INTERVAL: process.env.GMAIL_POLL_INTERVAL || "60",
      },
    },

    // -----------------------------------------------------------------------
    // Filesystem Watcher — monitors vault for new files
    // -----------------------------------------------------------------------
    {
      name: "filesystem-watcher",
      script: "watchers/filesystem_watcher.py",
      interpreter: "python",
      cwd: ROOT,
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      out_file: "./logs/pm2-filesystem-out.log",
      error_file: "./logs/pm2-filesystem-err.log",
      merge_logs: true,
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },

    // -----------------------------------------------------------------------
    // Meta Poster — posts Facebook + Instagram drafts from vault
    // -----------------------------------------------------------------------
    {
      name: "meta-poster",
      script: "watchers/meta_poster.py",
      interpreter: "python",
      args: ["watch"],
      cwd: ROOT,
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 15000,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      out_file: "./logs/pm2-meta-poster-out.log",
      error_file: "./logs/pm2-meta-poster-err.log",
      merge_logs: true,
      env: {
        PYTHONUNBUFFERED: "1",
        META_PAGE_ACCESS_TOKEN: process.env.META_PAGE_ACCESS_TOKEN || "",
        META_PAGE_ID: process.env.META_PAGE_ID || "",
        META_IG_USER_ID: process.env.META_IG_USER_ID || "",
        META_GRAPH_VERSION: process.env.META_GRAPH_VERSION || "v22.0",
      },
    },

    // -----------------------------------------------------------------------
    // Twitter Poster — posts X/Twitter drafts from vault
    // -----------------------------------------------------------------------
    {
      name: "twitter-poster",
      script: "watchers/twitter_poster.py",
      interpreter: "python",
      args: ["watch"],
      cwd: ROOT,
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 15000,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      out_file: "./logs/pm2-twitter-poster-out.log",
      error_file: "./logs/pm2-twitter-poster-err.log",
      merge_logs: true,
      env: {
        PYTHONUNBUFFERED: "1",
        X_API_KEY: process.env.X_API_KEY || "",
        X_API_SECRET: process.env.X_API_SECRET || "",
        X_ACCESS_TOKEN: process.env.X_ACCESS_TOKEN || "",
        X_ACCESS_TOKEN_SECRET: process.env.X_ACCESS_TOKEN_SECRET || "",
        X_BEARER_TOKEN: process.env.X_BEARER_TOKEN || "",
      },
    },

  ],
};
