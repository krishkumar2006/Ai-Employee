// PM2 Ecosystem File — AI Employee Orchestrator
// Usage: pm2 start ecosystem.config.js

module.exports = {
  apps: [
    {
      name: "ai-employee",
      script: "orchestrator.py",
      interpreter: "python",
      cwd: __dirname,
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      out_file: "./logs/pm2-out.log",
      error_file: "./logs/pm2-error.log",
      merge_logs: true,
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
