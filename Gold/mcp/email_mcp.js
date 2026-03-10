/**
 * Email MCP Server — Silver Tier
 * ================================
 * A Model Context Protocol server that gives Claude the ability to
 * send emails through Gmail SMTP. Built with Human-in-the-Loop (HITL)
 * safety — Claude must always confirm with the user before sending.
 *
 * Tools exposed:
 *   1. draft_email   — Prepare an email and return a preview (NO send)
 *   2. send_email    — Actually send after human approval
 *   3. list_drafts   — Show all pending drafts in this session
 *   4. discard_draft — Delete a draft without sending
 *
 * Env vars required:
 *   GMAIL_ADDRESS    — your Gmail address (sender)
 *   GMAIL_APP_PASSWORD — 16-char Google App Password (NOT your login password)
 *
 * Part of the Personal AI Employee system.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import nodemailer from "nodemailer";
import { randomUUID } from "node:crypto";
import { appendFileSync, mkdirSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const __dirname = dirname(fileURLToPath(import.meta.url));
const VAULT_PATH = join(__dirname, "..", "vault");
const SENT_LOG_DIR = join(VAULT_PATH, "Sent_Emails");
const GMAIL_ADDRESS = process.env.GMAIL_ADDRESS;
const GMAIL_APP_PASSWORD = process.env.GMAIL_APP_PASSWORD;

// Validate env vars at startup
if (!GMAIL_ADDRESS || !GMAIL_APP_PASSWORD) {
  console.error(
    "ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD environment variables are required.\n" +
      "Set them in your MCP config or shell environment."
  );
  process.exit(1);
}

// Ensure log directory exists
if (!existsSync(SENT_LOG_DIR)) {
  mkdirSync(SENT_LOG_DIR, { recursive: true });
}

// ---------------------------------------------------------------------------
// SMTP Transport (Gmail)
// ---------------------------------------------------------------------------
const transporter = nodemailer.createTransport({
  service: "gmail",
  auth: {
    user: GMAIL_ADDRESS,
    pass: GMAIL_APP_PASSWORD,
  },
});

// Verify connection at startup
transporter.verify().then(() => {
  console.error("[email_mcp] SMTP connection verified. Ready to send emails.");
}).catch((err) => {
  console.error("[email_mcp] SMTP verification failed:", err.message);
  console.error("[email_mcp] Server will start but sends may fail.");
});

// ---------------------------------------------------------------------------
// In-memory draft store (HITL: draft first, send only after approval)
// ---------------------------------------------------------------------------
/** @type {Map<string, {to: string, subject: string, body: string, cc?: string, created_at: string}>} */
const drafts = new Map();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function nowISO() {
  // Pakistan Standard Time offset
  const pkt = new Date(Date.now() + 5 * 60 * 60 * 1000);
  return pkt.toISOString().replace("Z", "+05:00");
}

function logSentEmail(draft, messageId) {
  const timestamp = nowISO().replace(/:/g, "-").slice(0, 19);
  const safeTo = draft.to.replace(/[^a-zA-Z0-9@._-]/g, "_").slice(0, 40);
  const logFile = join(SENT_LOG_DIR, `SENT_${safeTo}_${timestamp}.md`);

  const content = `---
type: sent_email
source: email_mcp
to: ${draft.to}
cc: ${draft.cc || "none"}
subject: ${draft.subject}
sent_at: ${nowISO()}
message_id: ${messageId || "unknown"}
status: sent
---

Email sent successfully.

To: ${draft.to}${draft.cc ? `\nCC: ${draft.cc}` : ""}
Subject: ${draft.subject}

Body:
${draft.body}
`;

  try {
    appendFileSync(logFile, content, "utf-8");
  } catch {
    console.error("[email_mcp] Failed to write sent log:", logFile);
  }
}

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------
const server = new McpServer({
  name: "ai-employee-email",
  version: "1.0.0",
});

// ---------------------------------------------------------------------------
// Tool 1: draft_email — Create a draft for human review (HITL gate)
// ---------------------------------------------------------------------------
server.tool(
  "draft_email",
  "Prepare an email draft for human review. Does NOT send — the human must approve first using send_email. Always use this before sending.",
  {
    to: z.string().email().describe("Recipient email address"),
    subject: z.string().min(1).describe("Email subject line"),
    body: z.string().min(1).describe("Email body text (plain text)"),
    cc: z.string().optional().describe("CC recipients (comma-separated, optional)"),
  },
  async ({ to, subject, body, cc }) => {
    const draftId = randomUUID().slice(0, 8);
    const draft = {
      to,
      subject,
      body,
      cc: cc || "",
      created_at: nowISO(),
    };

    drafts.set(draftId, draft);

    const preview = [
      `DRAFT CREATED — ID: ${draftId}`,
      ``,
      `============ EMAIL PREVIEW ============`,
      `From:    ${GMAIL_ADDRESS}`,
      `To:      ${to}`,
      cc ? `CC:      ${cc}` : null,
      `Subject: ${subject}`,
      `---------------------------------------`,
      body,
      `=======================================`,
      ``,
      `STATUS: Waiting for human approval.`,
      ``,
      `To send this email, ask the user:`,
      `"Should I send this email? (Draft ID: ${draftId})"`,
      ``,
      `Then call send_email with draft_id="${draftId}" once approved.`,
      `To cancel, call discard_draft with draft_id="${draftId}".`,
    ]
      .filter((line) => line !== null)
      .join("\n");

    return { content: [{ type: "text", text: preview }] };
  }
);

// ---------------------------------------------------------------------------
// Tool 2: send_email — Send a previously drafted email (post-approval)
// ---------------------------------------------------------------------------
server.tool(
  "send_email",
  "Send a previously drafted email AFTER the human has approved it. Requires a draft_id from draft_email. NEVER call this without explicit user confirmation.",
  {
    draft_id: z
      .string()
      .min(1)
      .describe("The draft ID returned by draft_email"),
  },
  async ({ draft_id }) => {
    const draft = drafts.get(draft_id);

    if (!draft) {
      return {
        content: [
          {
            type: "text",
            text: `ERROR: Draft "${draft_id}" not found. It may have been already sent or discarded.\n\nActive drafts: ${drafts.size === 0 ? "none" : [...drafts.keys()].join(", ")}`,
          },
        ],
        isError: true,
      };
    }

    try {
      const mailOptions = {
        from: GMAIL_ADDRESS,
        to: draft.to,
        subject: draft.subject,
        text: draft.body,
      };

      if (draft.cc) {
        mailOptions.cc = draft.cc;
      }

      const info = await transporter.sendMail(mailOptions);

      // Log to vault
      logSentEmail(draft, info.messageId);

      // Remove from drafts
      drafts.delete(draft_id);

      return {
        content: [
          {
            type: "text",
            text: [
              `EMAIL SENT SUCCESSFULLY`,
              ``,
              `To:         ${draft.to}`,
              draft.cc ? `CC:         ${draft.cc}` : null,
              `Subject:    ${draft.subject}`,
              `Message ID: ${info.messageId}`,
              `Sent at:    ${nowISO()}`,
              ``,
              `A log has been saved to vault/Sent_Emails/.`,
            ]
              .filter((l) => l !== null)
              .join("\n"),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `SEND FAILED: ${err.message}\n\nThe draft is still saved (ID: ${draft_id}). Fix the issue and try again.`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool 3: list_drafts — Show all pending drafts
// ---------------------------------------------------------------------------
server.tool(
  "list_drafts",
  "List all pending email drafts waiting for human approval.",
  {},
  async () => {
    if (drafts.size === 0) {
      return {
        content: [{ type: "text", text: "No pending drafts." }],
      };
    }

    const lines = ["PENDING EMAIL DRAFTS", "====================", ""];

    for (const [id, draft] of drafts) {
      lines.push(
        `Draft ID: ${id}`,
        `  To:      ${draft.to}`,
        `  Subject: ${draft.subject}`,
        `  Created: ${draft.created_at}`,
        ""
      );
    }

    lines.push(`Total: ${drafts.size} draft(s) awaiting approval.`);

    return { content: [{ type: "text", text: lines.join("\n") }] };
  }
);

// ---------------------------------------------------------------------------
// Tool 4: discard_draft — Cancel without sending
// ---------------------------------------------------------------------------
server.tool(
  "discard_draft",
  "Discard an email draft without sending it.",
  {
    draft_id: z
      .string()
      .min(1)
      .describe("The draft ID to discard"),
  },
  async ({ draft_id }) => {
    if (!drafts.has(draft_id)) {
      return {
        content: [
          {
            type: "text",
            text: `Draft "${draft_id}" not found. It may have been already sent or discarded.`,
          },
        ],
        isError: true,
      };
    }

    drafts.delete(draft_id);

    return {
      content: [
        {
          type: "text",
          text: `Draft "${draft_id}" has been discarded. No email was sent.`,
        },
      ],
    };
  }
);

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("[email_mcp] MCP server running on stdio. Waiting for Claude...");
}

main().catch((err) => {
  console.error("[email_mcp] Fatal error:", err);
  process.exit(1);
});
