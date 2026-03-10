---
type: business_goals
version: 1.0
last_updated: 2026-02-19
owner: CEO
reviewed_by_ai: weekly (Sunday 23:00 PKT)
---

# Business Goals & Performance Targets

> This file is read by the AI Employee every Sunday at 23:00 PKT.
> It generates a Monday Morning CEO Briefing based on these targets + live Odoo data.
> Edit any section freely — the AI adapts automatically.

---

## 1. Revenue Targets

| Period      | Target (PKR) | Target (USD) | Notes                        |
|-------------|-------------|--------------|------------------------------|
| Monthly     | 500,000     | 1,800        | Minimum to cover expenses    |
| Quarterly   | 1,800,000   | 6,500        | Q1 2026 goal                 |
| Annual      | 8,000,000   | 28,500       | FY 2026 target               |
| Per Invoice | 50,000      | 180          | Average deal size goal       |

**Currency:** PKR primary, USD secondary (rate: ~278 PKR/USD)

---

## 2. Growth Targets

| Metric                  | Current Baseline | Monthly Target | Quarterly Target |
|-------------------------|-----------------|----------------|-----------------|
| Revenue MoM Growth      | 0%              | +15%           | +50%            |
| New Clients per Month   | 0               | 3              | 10              |
| Client Retention Rate   | —               | > 90%          | > 90%           |
| Invoice Collection Rate | —               | > 85%          | > 90%           |
| Average Invoice Value   | —               | 50,000 PKR     | 75,000 PKR      |

---

## 3. Expense & Cash Flow Rules

| Rule                          | Threshold       | Action if Breached           |
|-------------------------------|-----------------|------------------------------|
| Monthly operating expenses    | < 200,000 PKR   | Flag in CEO Briefing          |
| Outstanding A/R > 30 days     | > 100,000 PKR   | Flag + draft follow-up email  |
| Outstanding A/R > 60 days     | Any amount      | RED ALERT in briefing         |
| Net cash flow (monthly)       | > 0             | Flag if negative              |
| Vendor bills unpaid > 15 days | Any amount      | List in briefing              |

---

## 4. Subscription Audit Rules

> The AI scans all vendor bills in Odoo each week and flags anything matching
> these keywords OR exceeding the per-item threshold.

### Subscription Budget
- **Maximum total monthly subscriptions:** 50,000 PKR (≈ $180 USD)
- **Maximum single subscription:** 15,000 PKR/month without explicit approval
- **Review cycle:** Weekly (flagged in CEO Briefing if new or changed)

### Keywords to Flag
```
keywords:
  - subscription
  - monthly fee
  - annual plan
  - saas
  - software license
  - cloud storage
  - hosting
  - domain
  - renewal
  - auto-renew
  - membership
  - retainer
```

### Known + Approved Subscriptions
List subscriptions you have already approved (AI will NOT flag these):
```
approved_subscriptions:
  - name: "Claude Pro"
    amount_pkr: 5600
    billing: monthly
    approved_date: 2026-01-01

  - name: "GitHub Copilot"
    amount_pkr: 2800
    billing: monthly
    approved_date: 2026-01-01

  - name: "Hostinger VPS"
    amount_pkr: 4500
    billing: monthly
    approved_date: 2026-01-01
```

### Auto-Cancel Candidates
Subscriptions to immediately flag for cancellation review:
- Any subscription not used in the last 30 days (manual note required)
- Any duplicate service (e.g. two cloud storage services)
- Any subscription > 6 months old without business impact noted

---

## 5. Key Performance Indicators (KPIs)

These are pulled from Odoo data automatically each week:

| KPI                          | How Measured                     | Target         | Red Flag        |
|------------------------------|----------------------------------|----------------|-----------------|
| Revenue collected this week  | Posted inbound payments (Odoo)   | ≥ 125,000 PKR  | < 50,000 PKR    |
| New invoices issued          | out_invoice created this week    | ≥ 3            | 0               |
| Overdue invoices (>30d)      | invoice_date_due < today, unpaid | 0              | Any             |
| Vendor bills outstanding     | in_invoice posted, unpaid        | < 3            | > 5             |
| Gross margin estimate        | Revenue - Expenses (this month)  | > 60%          | < 40%           |

---

## 6. Social Media Targets

| Platform   | Metric              | Weekly Target | Monthly Target |
|------------|---------------------|---------------|----------------|
| LinkedIn   | Posts               | 3             | 12             |
| Facebook   | Posts               | 5             | 20             |
| Instagram  | Posts               | 5             | 20             |
| Twitter/X  | Tweets              | 10            | 40             |
| LinkedIn   | Engagement Rate     | > 2%          | > 2.5%         |
| Instagram  | Engagement Rate     | > 3%          | > 3.5%         |

---

## 7. Client Pipeline Rules

| Stage             | Max Days Allowed | Action                          |
|-------------------|------------------|---------------------------------|
| Proposal sent     | 7 days           | Follow-up email                 |
| Invoice unpaid    | 15 days          | Reminder (auto-draft)           |
| Invoice overdue   | 30 days          | Escalation (flag in briefing)   |
| No contact        | 45 days          | Re-engagement campaign          |

---

## 8. Weekly Audit Checklist

The AI Employee checks these every Sunday and reports pass/fail:

- [ ] Revenue on track vs. monthly target (prorated)
- [ ] All invoices from this week have been issued
- [ ] No invoices overdue > 30 days
- [ ] Total subscription spend within budget
- [ ] No unapproved subscriptions found in vendor bills
- [ ] Net cash flow positive this week
- [ ] Social media posting targets met
- [ ] Pending email actions cleared from vault
- [ ] Odoo data synced (no draft invoices > 3 days old)

---

## 9. Strategic Goals (Qualitative)

These are referenced by the AI for context in recommendations:

1. **Primary:** Build a profitable AI automation consultancy serving SMEs in Pakistan
2. **Secondary:** Launch one productized service package by Q2 2026
3. **Tertiary:** Reach 1,000 LinkedIn followers by end of Q2 2026
4. **Q1 2026 Focus:** Close first 5 paying clients, generate first PKR 500K revenue

---

## 10. Notes for the AI

- Always compare weekly numbers against the prorated monthly target (monthly ÷ 4)
- Flag anything that contradicts a goal — do not soften bad news
- If no Odoo data is available, say so explicitly — never invent numbers
- Social media data comes from the latest vault/Plans/META_SUMMARY_*.md and vault/Plans/TWITTER_SUMMARY_*.md
- Tone for briefing: direct, data-driven, no fluff. CEO wants facts + actions, not encouragement
