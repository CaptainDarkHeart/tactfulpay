# TactfulPay

## Project Overview

AI powered collections agent that chases overdue invoices on behalf of SMEs using psychological escalation techniques (Chris Voss tactical empathy). Operates across email, AI voice, and LinkedIn DM with four behavioural phases over a strict 21 day cycle.

Business model: outcome only pricing. 10% fee on invoices over GBP 5,000, or GBP 500 flat fee for stalled invoices 60+ days overdue. Zero upfront cost to the SME.

## Architecture

Three brain agentic workflow:

**Sentry** (`src/sentry/`) Integration brain. Monitors accounting software (Codat, Xero, QuickBooks, CSV), identifies overdue invoices, pulls contact metadata, handles OAuth token management, and processes Codat and Stripe webhooks with idempotency.

**Strategist** (`src/strategist/`) Psychological brain. LLM powered via OpenRouter with two separate pinned model fallback lists (see `src/config.py`), not the OpenRouter auto-router, to keep message tone and classification output format consistent: message generation leads with Qwen3 235B (tone/nuance), classification leads with GLM 4.7 Flash (cheap, fixed-category output, lower stakes since a parse failure just falls back to STALL). Manages phase state machine with `phase_start_date` based escalation timing. Classifies responses into 8 categories. Generates all messages using tactical empathy prompt templates with post generation guardrails.

**Executor** (`src/executor/`) Multi channel brain. Sends emails (Resend), voice calls (Vapi/ElevenLabs), LinkedIn DMs. Handles variable cadence and custom sending domain setup.

## Tech Stack

Python 3.11+ with OpenRouter (pinned model fallback, not the auto-router), PostgreSQL (Supabase with Row Level Security), Resend, Vapi/ElevenLabs, Codat, Stripe, FastAPI dashboard with JWT auth.

## Key Constraints

The agent must NEVER hallucinate discounts, payment terms, or legal threats. Discount offers gated by `discount_authorised` boolean and phase specific limits (see `src/strategist/constraints.py`). On DISPUTE or HOSTILE classification, agent pauses immediately and human must clear flag. "External compliance partner" is the strongest language permitted. Variable send cadence ensures the agent never looks automated and never contacts same person twice in one day. Max 30 cold emails per day per inbox.

All generated messaging must use Chris Voss tactical empathy principles (late night FM DJ voice, calibrated questions, empathy mirrors, labelling, accusation audits). Semicolons and hyphens are strictly prohibited in all generated output.

## Database Security

The database layer enforces tenant isolation through PostgreSQL Row Level Security (RLS). Dashboard sessions use JWT tokens via the Supabase anon key. Backend processes use the service role key for administrative operations. The RLS migration is at `migrations/20260328_rls_policies.sql`.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest
```

## Running

```bash
# Run tests
pytest

# Run daily processing cycle
python -m scripts.run_daily_sync

# Seed test data
python -m scripts.seed_test_data

# Start dashboard
uvicorn src.dashboard.app:app --reload --port 8000
```

## Current Status

Phase 2 complete. 276 tests passing. Key recent updates include phase_start_date based escalation for accurate 21 day cycle, Row Level Security migration, JWT authentication for the dashboard, tactical empathy prompt standardization, and post generation punctuation guardrails.

## Deployment

Live at https://tactfulpay-production.up.railway.app (Railway project `tactfulpay`, service deployed from repo `Dockerfile`, redeployed via `railway up`). Currently demo mode: no `SUPABASE_URL` or other API keys set, so the dashboard runs on in-memory demo data. `PORT=8000` and the domain's target port are set explicitly since Railway did not autodetect them.

`railway.json` config is deprecated in favor of `.railway/railway.ts` (existing file keeps working until 2026-12-01, run `railway config migrate` to switch).

A `wrangler.toml` / Cloudflare Containers deploy path also exists in the repo but is unused: it requires the Workers Paid plan ($5/mo) on the Cloudflare account, which isn't enabled, and pushing the built image 401s without it. Railway was used instead since it needed no billing change.

To go to full production: set `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, and the other keys listed in `.env.example` via `railway variables --set KEY=value`.

### Cloudflare roadmap: two-path decision (2026-09-24, undecided)

Cloudflare is on the product roadmap. `worker/index.ts` today is a thin lift-and-shift: it's a proxy + cron trigger wrapping the existing FastAPI app inside a Cloudflare Container (Durable Object running the repo `Dockerfile` unmodified). No business logic has moved into TypeScript, it's all plumbing. Two paths forward, not yet chosen:

1. **Stay lift-and-shift** (low effort). Just enable Workers Paid ($5/mo) and `wrangler deploy`. Python remains the only real stack; TS never grows past this proxy file.
2. **Go edge-native** (higher effort, bigger payoff). Move latency-sensitive bits out of the container into the Worker itself, e.g. Codat/Stripe webhook receipt + idempotency checks via D1 or KV (sub-ms, no container cold-start), leaving the container for Strategist's OpenRouter-powered logic and the dashboard. This is where TypeScript becomes a genuine second stack instead of glue. Keep Supabase Postgres as the source of truth regardless (RLS, tenant isolation); D1/KV would only be edge-local caching, not the main DB.

Immediate unblock either way: enable Workers Paid plan so this stops being unused scaffolding.
