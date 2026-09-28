# TactfulPay

AI powered collections agent that recovers overdue invoices for SMEs using psychological escalation techniques rooted in Chris Voss tactical empathy methodology.

## How It Works

The agent deploys a 4 phase escalation sequence over a strict 21+ day behavioral cycle. Each phase uses calibrated questions, empathy mirrors, and the late night FM DJ voice technique to move invoices to the top of the pay pile.

**Phase 1 (Days 1 to 5)** Helpful Project Liaison. Friendly verification of invoice receipt. Calibrated open ended questions. Email only.

**Phase 2 (Days 7 to 10)** Internal Advocate. Empathetic ally against bureaucracy. Labelling technique ("It seems like..."). Email and voice.

**Phase 3 (Days 14 to 17)** Loss Aversion Pivot. Gentle systemic consequences. Accusation audit for defensiveness. Email and voice.

**Phase 4 (Day 21+)** Formal Professional Shift. Cold AP style compliance language. External compliance partner is the strongest language permitted. Email, voice, and LinkedIn.

## Three Brain Architecture

The system uses a strict separation of concerns across three logical brains.

| Brain | Module | Responsibility |
|---|---|---|
| Sentry | `src/sentry/` | Integration layer. Monitors accounting software via Codat, Xero, QuickBooks, and CSV. Pulls contact metadata. Handles OAuth and webhooks. |
| Strategist | `src/strategist/` | Psychological layer. LLM powered via OpenRouter with pinned model fallback lists (Qwen3 235B for message generation, GLM 4.7 Flash for classification), not the auto-router, to keep tone and output format consistent. Manages the phase state machine, classifies inbound responses, and generates all outbound messaging. |
| Executor | `src/executor/` | Delivery layer. Sends emails via Resend, voice calls via Vapi and ElevenLabs, and LinkedIn DMs. Manages variable cadence and custom sending domains. |

## Business Model

Outcome only pricing. The SME pays nothing upfront.

| Condition | Fee |
|---|---|
| Recovered invoice over GBP 5,000 | 10% of original invoice amount |
| Stalled invoice 60+ days overdue | GBP 500 flat fee |
| Nothing recovered | Zero cost to the SME |

Fee attribution is enforced by tracking `first_contacted_at` in the database. If payment arrives after the agent made contact, regardless of payment method, the fee applies. Fees are calculated on the original invoice amount (not the Stripe checkout total) to prevent partial payment threshold exploits.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest
```

### Docker (Demo Mode)

No API keys required. The dashboard runs with in memory demo data.

```bash
docker compose up dashboard
```

### Docker (Production)

Requires a populated `.env` file with API keys and Supabase credentials.

```bash
docker compose up dashboard scheduler
```

## Tech Stack

| Component | Tool |
|---|---|
| Language | Python 3.11+ |
| LLM | OpenRouter, pinned model fallback lists (Qwen3 235B, DeepSeek V3.2, GLM), not the auto-router |
| Email | Resend (transactional) |
| Voice | Vapi + ElevenLabs |
| Accounting API | Codat, Xero (OAuth), QuickBooks (OAuth), CSV upload |
| Payments | Stripe (debtor payment links + SME fee billing) |
| Database | PostgreSQL (Supabase) with Row Level Security |
| Dashboard | FastAPI with JWT authentication |
| Config | pydantic settings with .env |
| CI/CD | GitHub Actions (tests + Docker image) |

## Project Structure

```
src/
  config.py                    Settings via pydantic settings
  main.py                      Daily cycle orchestrator

  sentry/                      Integration Brain
    invoice_sync.py             Codat + OAuth sync, external payment detection
    xero_client.py              Xero API client
    quickbooks_client.py        QuickBooks API client
    codat_client.py             Codat API client
    webhook_handler.py          Codat + Stripe webhook handlers (idempotent)
    write_back.py               Write payments back to accounting software
    oauth.py                    Token encryption and refresh
    csv_importer.py             Manual CSV upload import
    normalised_invoice.py       Normalised invoice data model

  strategist/                   Psychological Brain
    state_machine.py            Phase transitions (phase_start_date based)
    response_classifier.py      LLM based reply classification
    message_generator.py        LLM based message generation with guardrails
    constraints.py              Discount and language guardrails
    prompts/                    Tactical empathy prompt templates (phases 1 to 4)

  executor/                     Multi Channel Brain
    email_sender.py             Resend email sending
    payment_link.py             Stripe payment link creation
    cadence.py                  Variable send timing
    domain_manager.py           Custom sending domain setup
    voice_caller.py             Vapi voice call integration (placeholder)
    linkedin_dm.py              LinkedIn DM integration (placeholder)

  billing/                      Fee Calculation
    fee_calculator.py           10 percent or GBP 500 fee logic
    stripe_billing.py           SME fee checkout sessions

  dashboard/                    Web Dashboard
    app.py                      FastAPI dashboard with JWT auth support
    static/                     Logo assets

  db/                           Database Layer
    models.py                   Pydantic models + Supabase client (RLS aware)

  notifications/                Alerts
    slack_webhook.py            Slack alert notifications
    email_alerts.py             Email alert notifications

  utils/
    retry.py                    Exponential backoff retry decorator

migrations/                     PostgreSQL migration scripts
  20260325_critical_improvements.sql
  20260326_email_domains.sql
  20260328_rls_policies.sql     Row Level Security tenant isolation

scripts/
  run_daily_sync.py             Cron entry point
  seed_test_data.py             Seed database with test invoices

tests/                          266 passing tests (15 more fail locally on a blank CODAT_WEBHOOK_SECRET/CODAT_API_KEY, not a code bug)
```

## Payment Detection

The system detects payments via three paths.

**Stripe Payment Link** The debtor pays via a link included in the outreach. Webhook fires immediately. Fee created and invoice resolved.

**Codat Sync** Daily pull from the connected accounting platform detects invoices marked as paid.

**OAuth Direct Check** During each sync cycle, active invoices with an `external_id` are queried directly in Xero or QuickBooks.

In all cases, a fee is created only if `first_contacted_at` is set (meaning the agent actually contacted the debtor). If no prior contact, payment is marked resolved with no fee charged.

## Reply Classification

Inbound replies are classified by OpenRouter's pinned classifier model (GLM 4.7 Flash leading) into one of eight categories.

| Classification | Agent Action |
|---|---|
| `PROMISE_TO_PAY` | Monitor and re engage if payment misses promised date |
| `PAYMENT_PENDING` | Request reference number and follow up in 3 days |
| `DISPUTE` | Pause immediately and alert SME. Human must clear. |
| `REDIRECT` | Add new contact to Phase 1 sequence |
| `STALL` | Continue current phase on accelerated timeline |
| `HOSTILE` | Hard stop. Do not respond. Human review required. |
| `WRITE_OFF_CLAIMED` | Pause and alert SME with two explicit CTAs |
| `NO_RESPONSE` | Escalate to next phase after phase duration elapsed |

### Write Off Claimed

When a debtor claims the invoice was written off or cancelled, the agent pauses and alerts the SME with two options.

**Confirm Write Off** closes the invoice as `WRITTEN_OFF`. Fee discussion follows if the agent had already contacted the debtor.

**Debtor Lied and Resume** restores the invoice to active at Phase 3 minimum, regardless of where the sequence was. The pre claim phase is stored in `pre_write_off_phase` so the system always resumes in the right place.

## Database Security

The database layer enforces tenant isolation through PostgreSQL Row Level Security.

**Dashboard sessions** use JWT tokens via the Supabase anon key. All queries are scoped to the authenticated user through RLS policies on `smes`, `invoices`, `contacts`, `interactions`, `fees`, and `accounting_connections` tables.

**Backend processes** (daily sync, webhook handlers) use the service role key for administrative operations that span tenants.

The RLS migration is located at `migrations/20260328_rls_policies.sql`.

## Messaging Standards

All generated messaging strictly adheres to the following rules.

**Chris Voss Tactical Empathy** Every phase prompt employs the late night FM DJ voice, calibrated open ended questions, empathy mirrors, labelling techniques, and accusation audits as appropriate to the phase.

**Prohibited Characters** Semicolons and hyphens are strictly forbidden in all generated output. Prompt templates explicitly instruct the LLM to avoid them, and a post generation regex guardrail in `_enforce_banned_words` strips any that leak through.

**Phase 1 Banned Words** The words overdue, late, debt, owed, and collections are automatically replaced with softer alternatives (outstanding, pending, balance, accounts) if the LLM generates them.

## Safety Guardrails

The agent never hallucinates discounts or legal threats. Discount offers are gated by the `discount_authorised` flag and per SME `max_discount_percent`. Classifications of `DISPUTE`, `HOSTILE`, and `WRITE_OFF_CLAIMED` all pause the agent immediately. Human must act before re engagement. Phase 4 uses the strongest language permitted which is "external compliance partner". Variable cadence ensures outreach never looks automated and never contacts the same person twice in one day. Maximum 30 cold emails per day per inbox. Fees are calculated on the original invoice amount to prevent partial payment threshold exploits.

## Webhook Idempotency

Both Codat and Stripe webhook endpoints implement idempotency via the `processed_events` table. Duplicate events return `{duplicate: true}` and are not reprocessed. This prevents double fee creation and duplicate sync triggers.

## Disconnect Protection

If an SME disconnects their accounting integration while contacted invoices are still active, a Slack alert fires immediately. This prevents the exploit where an SME collects payment and removes visibility before the daily sync runs.

## Running

```bash
# Run the full test suite
pytest

# Run the daily processing cycle
python -m scripts.run_daily_sync

# Seed test data
python -m scripts.seed_test_data

# Start the dashboard locally
uvicorn src.dashboard.app:app --reload --port 8000
```

## Status

Phase 2 complete. 266 tests passing, 15 failing locally on a blank `CODAT_WEBHOOK_SECRET`/`CODAT_API_KEY` (intentional fail-closed behavior, not a code bug).

Recent improvements include the phase start date based escalation fix ensuring the 21 day behavioral cycle maintains accuracy, Row Level Security migration for tenant isolation, JWT based dashboard authentication (placeholder for Supabase Auth), the Strategist LLM swap from Claude to pinned OpenRouter models, Chris Voss tactical empathy prompt standardization across all four phases, and post generation punctuation guardrails.

Live demo at https://tactfulpay-production.up.railway.app. See `CLAUDE.md` for deployment details and the Railway vs Cloudflare hosting decision.
