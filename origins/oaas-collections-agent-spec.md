# OaaS Collections Agent: Claude Code Project Specification

**Project codename:** OaaS (Outcome-as-a-Service)
**Authors:** Dan Taylor & Stewart Rogers
**Date:** 22 March 2026
**Status:** Pre-MVP
**FrieNDA:** In full effect

---

## 1. Executive Summary

Build an AI-powered collections agent that chases overdue invoices on behalf of SMEs using psychological escalation techniques drawn from Chris Voss's tactical empathy methodology. The agent operates across email, AI voice calls, and LinkedIn DM, escalating through four behavioural phases over a 21+ day cycle.

The business model is pure outcome-based: the SME pays nothing upfront. For every invoice the agent successfully recovers, we take a fee (10% of invoice value for invoices over GBP 5,000, or a GBP 500 flat fee for stalled invoices older than 60 days). If we recover nothing, we cost the SME nothing.

### Why This Exists

Stewart Rogers (ex-VentureBeat, Dataconomy) and Dan Taylor identified the "collections gap" as a universal, unsolved pain point for SMEs. The core insight: the biggest hurdle is not sending a reminder, it is navigating the power dynamic between a small vendor and a large, slow-moving corporate accounts payable department. Most collections approaches fail because they default to adversarial, threatening language that triggers defensiveness. This agent uses behavioural psychology to move the invoice to the top of the "to-pay" pile by positioning itself as a collaborative ally, not a bill collector.

### Competitive Landscape

The main funded competitor is **Ophelos** (ophelos.com), a UK/EU AI-native debt collection platform that has raised significant venture capital and works with large enterprise clients. Other players include HighRadius (enterprise AR automation), Skit.ai (voice-first collections), and CollectWise (late-stage recovery). Our differentiation is:

1. **Outcome-only pricing** with zero upfront cost (most competitors charge SaaS fees or retainers)
2. **SME-first** positioning (competitors target enterprise and large agency clients)
3. **Tactical empathy methodology** baked into the agent's personality (competitors use generic dunning sequences)
4. **Lightweight integration** via unified accounting APIs rather than heavy enterprise onboarding

---

## 2. Architecture Overview

The system uses a **three-brain agentic workflow** operating in a continuous loop:

### 2.1 The Sentry (Integration Brain)

**Purpose:** Monitors accounting software via API. Identifies overdue invoices and pulls contact metadata.

**Responsibilities:**
- Connect to Xero, QuickBooks, FreshBooks, and Sage via a unified accounting API
- Watch for invoices crossing the 60-day overdue threshold
- Pull the `customerRef` to resolve the contact's name, email, phone, and historical payment behaviour
- Run a daily sync (08:00 UTC) to identify new targets
- Listen for Codat/Nango webhooks to detect status changes (e.g., invoice marked "Paid")

**Technical notes:**
- Use **Codat** or **Nango** as the unified accounting API layer. Codat is the more established option with strong Xero/QuickBooks coverage and a mature Link UI for SME onboarding. However, note that Codat has been narrowing its product focus toward lending use cases through 2025-2026 and deprecated several legacy products in February 2026. Nango is open-source, more flexible, and supports 700+ APIs but requires more implementation work. **Recommendation: start with Codat for MVP speed, evaluate Nango for Phase 3 if Codat's roadmap diverges further from our needs.**
- For MVP, also support a **CSV upload fallback** where the SME manually provides a spreadsheet of overdue invoices (name, email, phone, invoice number, amount, due date). This removes the integration dependency for early testing and sales demos.

### 2.2 The Strategist (Psychological Brain)

**Purpose:** The core LLM. Analyses the state of each invoice negotiation and selects the appropriate persona, channel, and message.

**Responsibilities:**
- Maintain a state machine for each invoice tracking its current phase (1-4) and all prior interactions
- Analyse the "vibe" of incoming replies using sentiment classification
- Select the appropriate persona based on response patterns:
  - Client says "We're busy" or similar deflection: deploy **Helpful Ally** persona
  - Client ignores three consecutive emails: shift to **Audit Compliance** persona
  - Client says "Will pay Friday": send confirmation + calendar invite, update accounting notes
  - Client disputes the work: **immediately pause the agent** and alert the human business owner
  - No response after Phase 4: flag for human review and potential handoff to legal
- Decide channel escalation (email first, then voice, then LinkedIn DM)
- Enforce hard-coded constraints on any offers (max 3% discount for payment within 24 hours; anything beyond requires human approval)

**LLM selection:**
- Use **Claude Sonnet 4** via the Anthropic API as the primary model. It provides the best balance of intelligence, speed, and cost for this use case. Opus is overkill for templated-but-personalised outreach; Haiku lacks the nuance for psychological framing.
- All prompts must include explicit constraints to prevent hallucination of discounts, payment terms, or legal threats that are not authorised.

### 2.3 The Executor (Multi-Channel Brain)

**Purpose:** Sends the actual messages via the most effective channel at the right time.

**Responsibilities:**
- Send emails via **Instantly.ai** (handles sender rotation, warm-up, and deliverability)
- Place AI voice calls via **Vapi** (with ElevenLabs TTS for natural-sounding voicemail drops)
- Send LinkedIn DMs via browser automation or LinkedIn API (Phase 3 only)
- Implement a **variable cadence** schedule that avoids bot-like patterns:
  - Never ping every 24 hours
  - Example rhythm: 9:15 AM on a Tuesday, then 4:45 PM on a Thursday
  - Randomise send times within business-hours windows
- Use a **Discovery sub-agent** to identify the actual person who clicks the "Approve" button in AP, not just the generic accounts payable inbox

---

## 3. The Psychological Escalation Sequence

This is the core intellectual property. The agent moves through four phases, each with a distinct persona, psychology, system prompt, and channel strategy.

### Phase 1: "The Helpful Project Liaison" (Days 1-5)

**Goal:** Assume the delay is a technical error or oversight. Never mention "debt" or "overdue."

**Psychology:** The Yes-Ladder. Get them to agree they received the invoice first.

**Channel:** Email only

**System prompt:**
```
You are a friendly Project Coordinator named [AGENT_NAME] working at [SME_NAME].
Your tone is collaborative and warm. Your goal is to verify receipt of Invoice
#[INVOICE_NUMBER] without being accusatory. Never use the words "overdue",
"late", "debt", "owed", or "collections". Frame everything as a helpful
check-in. If they confirm receipt, thank them and gently ask about the
processing timeline. If they say they haven't received it, offer to re-send
the invoice with a fresh payment link.

CONSTRAINTS:
- Do not offer any discounts
- Do not mention legal action
- Do not mention credit impacts
- Maximum email length: 120 words
- Sign off with your name and a direct reply-to address
```

**Email template (Day 1):**
```
Subject: Quick check on invoice #[INVOICE_NUMBER]

Hi [NAME],

I was just updating our project folder and noticed the system hasn't
checked off the latest invoice (#[INVOICE_NUMBER]) as received yet.
I know how messy email threads get - did that land in your inbox
okay, or should I send a fresh link?

Best,
[AGENT_NAME]
[SME_NAME]
```

**Cadence:**
- Day 1: Email 1 (receipt verification)
- Day 3: Follow-up if no response (short, casual: "Just bumping this up in case it got buried")
- Day 5: If no response, escalate to Phase 2

### Phase 2: "The Internal Advocate" (Days 7-10)

**Goal:** Position the agent as an ally trying to protect the client from their own accounting department's strict rules.

**Psychology:** Labelling (Chris Voss) and the Common Enemy (the auditor). The agent deflects pressure to a fictional third party ("my finance lead").

**Channel:** Email (Day 7) + AI Voice Call (Day 10)

**System prompt:**
```
You are [AGENT_NAME], a Senior Project Liaison at [SME_NAME]. You are
trying to save the client from administrative hassle. Use phrases like
"I'd hate for this to get flagged" or "I want to make sure your account
stays in good standing." Reference a fictional "finance lead" or
"end-of-month audit" as the pressure source - never yourself.

Your tone is empathetic and slightly conspiratorial, as if you and the
recipient are on the same team against bureaucracy. Use the labelling
technique: start observations with "It seems like..." to defuse tension.

CONSTRAINTS:
- Do not offer discounts above 2% for payment within 48 hours
- Do not threaten legal action
- Maximum email length: 100 words
- Voice messages must be under 30 seconds
```

**Email template (Day 7):**
```
Subject: Re: Invoice #[INVOICE_NUMBER] - trying to keep this off the report

Hi [NAME],

It seems like there's a hurdle on the processing side. I'd hate for
our finance lead to flag this for a manual audit next week - it's a
huge paperwork headache for everyone.

Is there anything I can provide to help you get this pushed through
today?

[AGENT_NAME]
```

**AI Voice Script (Day 10):**
```
Hi [NAME], just a quick 10-second follow-up on my email. I'm trying
to keep Invoice [INVOICE_NUMBER] off the overdue report before the
Friday cutoff. Give me a shout if you need me to re-send the portal
link. Thanks!
```

**Voice implementation notes:**
- Use Vapi to place the outbound call
- If voicemail is detected, play the pre-generated ElevenLabs audio
- If a human answers, the Vapi agent should follow the Phase 2 system prompt conversationally
- Log the call outcome (voicemail left / spoke to human / no answer)

### Phase 3: "The Loss Aversion Pivot" (Days 14-17)

**Goal:** Introduce a cost to the delay without being aggressive.

**Psychology:** Loss aversion. People are roughly twice as motivated to avoid a loss as they are to achieve a gain. The agent introduces consequences framed as things the client will lose (priority status, early-payment benefits, scheduling slots).

**Channel:** Email (Day 14) + Optional voice follow-up (Day 17)

**System prompt:**
```
You are [AGENT_NAME] from [SME_NAME]. Introduce a gentle consequence
for the delay. Focus on loss of future priority, scheduling slots, or
early-payment benefits. Never threaten legal action. Frame consequences
as systemic ("our system automatically de-prioritises") rather than
personal ("I will have to escalate").

If the original invoice terms included an early-payment discount that
has lapsed, you may offer to honour the original discounted rate as a
one-time courtesy for payment within 48 hours. This is the "Loss
Aversion" pivot - they are losing a deal they could have had.

CONSTRAINTS:
- Maximum discount: 3% for payment within 24 hours (requires
  [SME_NAME] pre-authorisation flag = true)
- Do not fabricate discounts that were not in the original terms
- Do not mention lawyers, courts, or legal proceedings
- Maximum email length: 110 words
```

**Email template (Day 14):**
```
Subject: Re: Invoice #[INVOICE_NUMBER] - project priority update

Hi [NAME],

I'm writing because I'm concerned about our project priority for next
quarter. Our system automatically de-prioritises accounts with open
balances, and I'd really hate for your team to lose their preferred
scheduling slot.

If we can settle this by EOD, I can manually keep your priority status
locked in.

[AGENT_NAME]
```

### Phase 4: "The Regulatory/Formal Shift" (Day 21+)

**Goal:** Transition to a bureaucratic persona. The agent is no longer a person; it is a process.

**Psychology:** Social proof and authority. The tone shifts to cold, AP-style professional language. References to "compliance", "records", and "vendor ecosystem" invoke institutional authority.

**Channel:** Formal email (Day 21) + LinkedIn DM (Day 23)

**System prompt:**
```
Shift to a cold, professional tone. Use Associated Press style - no
fluff, no emojis, no pleasantries. Mention "Compliance" and "Records."
Reference the client's "credit profile within our vendor ecosystem."
Introduce the concept of the file moving to an "external compliance
partner" if unresolved.

This is the final automated phase. If this does not produce a response
or payment commitment within 7 days, the invoice is flagged for HUMAN
REVIEW and the agent ceases automated outreach.

CONSTRAINTS:
- Do not name any specific law firm or legal entity
- Do not threaten court action directly
- Do not use language that could constitute harassment under UK/EU
  debt collection regulations
- "External compliance partner" is the strongest language permitted
- Maximum email length: 80 words
```

**Email template (Day 21):**
```
Subject: Invoice #[INVOICE_NUMBER] - compliance notice

Regarding Invoice #[INVOICE_NUMBER]:

Our records indicate this balance is now [DAYS] days past terms. To
ensure your credit profile within our vendor ecosystem remains
accurate, we require confirmation of the wire transfer by [DATE].

Please attach the remittance advice to this thread to prevent the
file from moving to our external compliance partner.

[AGENT_NAME]
Accounts, [SME_NAME]
```

**LinkedIn DM (Day 23):**
```
Hi [NAME], I sent a compliance notice regarding Invoice
#[INVOICE_NUMBER] on [DATE]. I'd like to resolve this before it
escalates. Could you point me to the right person if this isn't
your area? Thanks.
```

---

## 4. Response Classification Engine

When the agent receives a reply at any phase, it classifies the response using the Strategist LLM before taking action.

### Classification prompt:
```
Classify the following email reply into exactly one of these categories:

1. PROMISE_TO_PAY - The sender commits to a specific payment date or
   says payment has been initiated
2. PAYMENT_PENDING - The sender says a check/transfer is in progress
   but provides no specific date
3. DISPUTE - The sender disputes the invoice amount, the work delivered,
   or the existence of the debt
4. REDIRECT - The sender says they are not the right person and provides
   or suggests an alternative contact
5. STALL - The sender acknowledges the invoice but provides vague
   non-commitments ("we're working on it", "it's in the queue")
6. HOSTILE - The sender is angry, threatens legal action, or demands
   no further contact
7. NO_RESPONSE - No reply received within the phase window

Reply with ONLY the category name and a one-sentence justification.

Email reply:
"""
[REPLY_TEXT]
"""
```

### Action matrix:

| Classification | Agent Action | Accounting Update | Next Step |
|---|---|---|---|
| PROMISE_TO_PAY | Send "thank you" + calendar reminder for promised date | Update note: "Payer promised [DATE]" | Monitor. If payment not received by DATE+3, re-engage with Phase 2 tone |
| PAYMENT_PENDING | Request check/transfer reference number (social accountability) | Update note: "Payment pending, ref requested" | Follow up in 3 business days |
| DISPUTE | **Immediately pause agent.** Alert human business owner via email/Slack notification | Flag: "DISPUTED - Human intervention required" | Agent does not re-engage until human clears the flag |
| REDIRECT | Thank sender, add new contact to the sequence at Phase 1 | Update note: "Redirected to [NEW_CONTACT]" | Begin Phase 1 with new contact |
| STALL | Acknowledge, then continue current phase escalation on accelerated timeline | No change | Reduce phase interval by 2 days |
| HOSTILE | **Immediately pause agent.** Alert human business owner. Do NOT respond. | Flag: "HOSTILE - Human review required" | Agent does not re-engage until human clears the flag |
| NO_RESPONSE | Escalate to next phase | No change | Move to next phase per cadence |

---

## 5. Tech Stack

### Core Infrastructure

| Component | Tool | Purpose | Estimated Cost |
|---|---|---|---|
| LLM | Claude Sonnet 4 (Anthropic API) | Strategist brain, response classification, message generation | ~USD 3/1M input tokens |
| Email delivery | Instantly.ai | Sender rotation, warm-up, deliverability, inbox placement | USD 37-97/month |
| AI Voice | Vapi | Outbound calls, voicemail detection, conversational agent | ~USD 0.15-0.33/min all-in |
| TTS | ElevenLabs (via Vapi) | Natural-sounding voice for voicemail scripts | Included in Vapi stack |
| Accounting API | Codat (MVP) / Nango (Phase 3 evaluation) | Unified access to Xero, QuickBooks, FreshBooks, Sage | Codat: custom pricing, contact sales |
| Payment links | Stripe | One-click payment links sent when client commits to pay | 2.9% + 30c per transaction |
| State management | PostgreSQL (Supabase) | Invoice states, contact records, interaction logs, phase tracking | Free tier to start |
| Job scheduler | Python + cron / Supabase Edge Functions | Daily sync, cadence scheduling, follow-up triggers | Minimal |
| Dashboard | React (Next.js) or Supabase Studio | SME-facing dashboard showing invoice status, agent activity, recovery stats | Build cost only |
| Notifications | Email + Slack webhook | Alert human business owners on DISPUTE/HOSTILE flags | Free |

### Email Deliverability Strategy

This is a critical risk area. Sending collection-adjacent emails can trigger spam filters. Mitigation:

1. Use **Instantly.ai** for all outbound email. It handles sender account rotation, IP sharding, warm-up, and inbox placement monitoring.
2. Set up **dedicated sending domains** per SME client (e.g., accounts.clientname.com) with proper SPF, DKIM, and DMARC.
3. Keep emails **short** (under 120 words), **plain text** (no HTML templates in early phases), and **conversational** in tone.
4. The "reply to your sent email" technique Stewart described (forwarding your own sent email with "Sending this again in case it didn't arrive...") should be implemented as a specific follow-up pattern in Phase 1. This achieves two things: it bypasses most spam filters because the filter interprets it as a conversation thread, and it removes blame from the recipient.
5. **Never send more than 30 cold emails per day per inbox.** Use slow ramp for new domains.
6. Monitor inbox placement daily using Instantly's automated placement tests.

### Hallucination Prevention

This is the highest-risk technical concern. The agent must never:

- Invent a discount that was not pre-authorised
- Fabricate invoice amounts or dates
- Threaten legal action beyond the approved language
- Claim authority it does not have

**Hard-coded guardrails:**
- All discount offers are gated by a boolean flag (`discount_authorised: true/false`) set by the SME during onboarding
- Maximum discount percentage is stored as a config value, not in the prompt
- The agent's system prompts explicitly list what it CANNOT say
- All outbound messages are logged and can be audited
- Phase 4 language is templated, not generated, to eliminate hallucination risk in the highest-stakes phase

---

## 6. Data Model

### Core entities:

```
SME (client)
  - id: uuid
  - company_name: string
  - contact_email: string
  - contact_phone: string
  - accounting_platform: enum (xero, quickbooks, freshbooks, sage, csv)
  - codat_company_id: string (nullable)
  - stripe_customer_id: string (nullable)
  - discount_authorised: boolean (default: false)
  - max_discount_percent: decimal (default: 0)
  - onboarded_at: timestamp
  - status: enum (active, paused, churned)

Invoice
  - id: uuid
  - sme_id: fk -> SME
  - invoice_number: string
  - debtor_company: string
  - amount: decimal
  - currency: string (default: GBP)
  - due_date: date
  - days_overdue: computed
  - current_phase: enum (1, 2, 3, 4, human_review, resolved, disputed)
  - status: enum (active, paused, paid, disputed, written_off)
  - created_at: timestamp
  - resolved_at: timestamp (nullable)
  - fee_charged: boolean (default: false)
  - fee_amount: decimal (nullable)

Contact
  - id: uuid
  - invoice_id: fk -> Invoice
  - name: string
  - email: string
  - phone: string (nullable)
  - linkedin_url: string (nullable)
  - role: string (nullable, e.g. "AP Manager", "CFO")
  - is_primary: boolean
  - source: enum (csv_upload, codat_sync, discovery_agent, redirect)

Interaction
  - id: uuid
  - invoice_id: fk -> Invoice
  - contact_id: fk -> Contact
  - phase: integer (1-4)
  - channel: enum (email, voice, linkedin, sms)
  - direction: enum (outbound, inbound)
  - message_type: enum (initial, follow_up, response, escalation)
  - content: text
  - classification: enum (promise_to_pay, payment_pending, dispute,
    redirect, stall, hostile, no_response) (nullable, for inbound only)
  - sent_at: timestamp
  - delivered: boolean
  - opened: boolean (nullable)
  - replied: boolean
  - metadata: jsonb (call duration, voicemail detected, etc.)

Fee
  - id: uuid
  - invoice_id: fk -> Invoice
  - sme_id: fk -> SME
  - fee_type: enum (percentage, flat)
  - fee_amount: decimal
  - invoice_amount_recovered: decimal
  - stripe_payment_intent_id: string (nullable)
  - status: enum (pending, charged, failed, waived)
  - created_at: timestamp
  - charged_at: timestamp (nullable)
```

---

## 7. Development Roadmap

### Phase 1: Read-Only MVP (Weeks 1-3)

**Goal:** Prove the concept works with CSV-uploaded invoices and email-only outreach.

**Deliverables:**
1. **CSV intake:** Upload a spreadsheet of overdue invoices. Parse and validate.
2. **Supabase database:** Set up the data model above.
3. **Strategist engine:** Claude Sonnet 4 API integration with the Phase 1-4 system prompts.
4. **Email sender:** Integrate Instantly.ai API for outbound email with sender rotation.
5. **Response classifier:** Inbound email parsing + LLM classification.
6. **State machine:** Automated phase progression based on cadence rules.
7. **Basic dashboard:** Simple web UI showing invoice status, current phase, and interaction history.
8. **Notification system:** Email alerts to the SME owner on DISPUTE/HOSTILE flags.
9. **Manual "reply to sent" follow-up:** Implement Stewart's technique as a Phase 1 follow-up pattern.

**Out of scope for Phase 1:** Accounting integrations, voice calls, LinkedIn DMs, Stripe billing, one-click payment links.

**Testing approach:**
- Use 5-10 test invoices with team members playing the debtor role
- Test each response classification category
- Verify that the agent correctly pauses on DISPUTE and HOSTILE
- Verify that the agent does not hallucinate discounts when `discount_authorised = false`
- Check email deliverability with Instantly's inbox placement tests

### Phase 2: Multi-Channel Escalation (Weeks 4-6)

**Goal:** Add voice calls, Codat integration, and the one-click payment link.

**Deliverables:**
1. **Vapi integration:** Outbound calls with voicemail detection and scripted messages.
2. **ElevenLabs voice:** Generate a consistent agent voice for all voicemail drops.
3. **Codat integration:** Connect to the SME's Xero/QuickBooks via the Codat Link flow.
4. **Webhook listener:** Codat webhook to detect when an invoice status changes to "Paid."
5. **Stripe payment links:** When a debtor commits to pay, immediately reply with a one-click Stripe payment link.
6. **Write-back:** Update the "Notes" field in the SME's accounting software when the agent receives a promise-to-pay or other status change.
7. **Discovery sub-agent:** Use web search + LinkedIn to identify the actual AP approver (not just the generic inbox).

### Phase 3: Closing Logic + Billing (Weeks 7-9)

**Goal:** Automate the full revenue cycle including fee billing.

**Deliverables:**
1. **Automated fee calculation:** When Codat webhook confirms "Paid", calculate the fee (10% or GBP 500 flat, whichever applies).
2. **SME billing:** Charge the fee to the SME's saved payment method via Stripe.
3. **LinkedIn DM integration:** Automated outreach via LinkedIn for Phase 4 escalation.
4. **SME onboarding flow:** Self-service sign-up, Codat Link connection, Stripe payment method capture.
5. **Reporting dashboard:** Recovery rate, average days-to-payment, fee revenue, channel effectiveness breakdown.
6. **Evaluate Nango vs Codat:** Based on Phase 2 experience, decide whether to migrate to Nango for broader integration support.

### Phase 4: Scale + Optimise (Weeks 10+)

**Goal:** Productise and grow.

**Deliverables:**
1. **Multi-tenant architecture:** Support multiple SME clients simultaneously.
2. **A/B testing framework:** Test different email copy, subject lines, and cadence patterns.
3. **Analytics:** Which phases convert best? Which channels? Which times of day?
4. **White-label option:** Allow SMEs to use their own branding on the agent's communications.
5. **Compliance review:** Engage a solicitor to review all templates against UK/EU debt collection regulations.
6. **Marketing site:** "Give me your list of 60-day overdue invoices. I will deploy a dedicated Relationship Manager agent to each one. You pay nothing upfront. For every invoice we get settled without a lawyer, we take GBP 500. If we don't get you paid, we cost you GBP 0."

---

## 8. The Codat Integration (Detail)

### Invoice Retrieval Script

```python
import requests
import datetime

CODAT_API_KEY = "YOUR_CODAT_API_KEY"
BASE_URL = "https://api.codat.io"

def get_overdue_invoices(company_id: str, min_days_overdue: int = 60):
    """
    Pull overdue invoices from Codat's unified accounting API.
    Uses Codat Query Language (CQL) to filter for open invoices
    past their due date.
    """
    today = datetime.date.today().isoformat()

    # CQL filter: status is Open AND due date is before today
    query_filter = f"status=Open&&dueDate<{today}"

    url = f"{BASE_URL}/companies/{company_id}/data/invoices"
    headers = {
        "Authorization": f"Basic {CODAT_API_KEY}",
        "Content-Type": "application/json"
    }
    params = {
        "query": query_filter,
        "pageSize": 50
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        overdue_list = data.get("results", [])

        # Filter for minimum days overdue
        targets = []
        for inv in overdue_list:
            due_date = datetime.date.fromisoformat(inv["dueDate"][:10])
            days_overdue = (datetime.date.today() - due_date).days
            if days_overdue >= min_days_overdue:
                targets.append({
                    "invoice_id": inv.get("id"),
                    "invoice_number": inv.get("invoiceNumber"),
                    "customer_name": inv.get("customerRef", {}).get(
                        "companyName", "Unknown"
                    ),
                    "customer_id": inv.get("customerRef", {}).get("id"),
                    "amount_due": inv.get("totalAmount", 0),
                    "currency": inv.get("currency", "GBP"),
                    "due_date": inv["dueDate"][:10],
                    "days_overdue": days_overdue
                })

        return targets

    except requests.exceptions.RequestException as e:
        print(f"Error fetching from Codat: {e}")
        return []
```

### Critical Implementation Notes

1. **Customer contact resolution:** The invoice `customerRef` contains only the company name and an ID. You must make a secondary call to `/data/customers/{customerId}` to get the actual email address and phone number of the AP contact.

2. **Write-back for audit trail:** When the agent receives a promise-to-pay, use Codat's write endpoint to update the invoice's "Notes" field. This keeps the human business owner informed without requiring them to check the dashboard.

3. **Webhook for payment detection:** Set up a Codat webhook on the "Invoice status changed" event. When the status flips to "Paid", this is your proof-of-success trigger for billing the fee.

4. **SME onboarding flow:** SMEs will never give you their accounting credentials directly. Use the **Codat Link URL** flow: you generate a unique link, the SME clicks it, logs into their Xero/QuickBooks through Codat's OAuth flow, and Codat hands you a secure token. This is critical for trust and compliance.

---

## 9. Business Model Maths

Assuming modest early traction:

| Metric | Value |
|---|---|
| SME clients | 5 |
| Overdue invoices per client per month | 10 |
| Total invoices worked | 50 |
| Success rate (conservative) | 50% |
| Successful recoveries | 25 |
| Fee per recovery | GBP 500 |
| **Monthly revenue** | **GBP 12,500** |
| Estimated costs (API, tools, infra) | ~GBP 500-800/month |
| **Net margin** | **~94%** |

At scale (20 SME clients, 200 invoices/month, 50% success rate): GBP 50,000/month.

---

## 10. Legal and Compliance Considerations

**This section requires professional legal review before launch.**

Key areas to address:

1. **UK Late Payment of Commercial Debts (Interest) Act 1998:** Understand what rights the SME already has and how the agent's communications interact with statutory rights.
2. **GDPR:** The agent processes personal data (contact names, emails, phone numbers). Ensure proper data processing agreements are in place.
3. **FCA regulatory perimeter:** Confirm that this service does not constitute regulated debt collection activity in the UK. B2B invoice recovery is generally outside FCA scope, but this needs legal confirmation.
4. **Email marketing regulations (PECR):** These are not marketing emails, they are transactional communications related to an existing commercial relationship. But confirm this interpretation.
5. **AI voice call regulations:** Automated calls in the UK are regulated by Ofcom. Ensure voicemail drops and conversational AI calls comply with the relevant rules.
6. **LinkedIn Terms of Service:** Automated LinkedIn messaging violates LinkedIn's ToS. Phase 3 LinkedIn DM integration carries platform risk.

---

## 11. File Structure for Claude Code

```
oaas-collections-agent/
  README.md
  requirements.txt
  .env.example
  pyproject.toml

  src/
    __init__.py
    config.py                  # Environment variables, API keys, constants
    main.py                    # Entry point, scheduler

    sentry/                    # The Integration Brain
      __init__.py
      codat_client.py          # Codat API wrapper
      csv_importer.py          # CSV upload parser
      invoice_sync.py          # Daily sync job
      webhook_handler.py       # Codat webhook listener

    strategist/                # The Psychological Brain
      __init__.py
      state_machine.py         # Phase progression logic
      response_classifier.py   # LLM-based reply classification
      message_generator.py     # LLM-based message composition
      prompts/
        phase_1.txt            # System prompt for Phase 1
        phase_2.txt            # System prompt for Phase 2
        phase_3.txt            # System prompt for Phase 3
        phase_4.txt            # System prompt for Phase 4
        classifier.txt         # Response classification prompt
      constraints.py           # Hard-coded guardrails (discount limits, etc.)

    executor/                  # The Multi-Channel Brain
      __init__.py
      email_sender.py          # Instantly.ai integration
      voice_caller.py          # Vapi integration
      linkedin_dm.py           # LinkedIn outreach (Phase 3)
      payment_link.py          # Stripe payment link generator
      cadence.py               # Variable timing engine

    billing/
      __init__.py
      fee_calculator.py        # 10% or GBP 500 flat logic
      stripe_billing.py        # Charge fees to SME

    dashboard/                 # Web UI (Next.js or similar)
      ...

    db/
      models.py                # SQLAlchemy / Supabase models
      migrations/              # Database migrations

    notifications/
      __init__.py
      slack_webhook.py         # Slack alerts for DISPUTE/HOSTILE
      email_alerts.py          # Email alerts to SME owner

  tests/
    test_classifier.py
    test_state_machine.py
    test_cadence.py
    test_constraints.py
    test_fee_calculator.py

  scripts/
    seed_test_data.py          # Create test invoices for development
    run_daily_sync.py          # Manual trigger for the daily sync job
```

---

## 12. Environment Variables

```
# .env.example

# Anthropic
ANTHROPIC_API_KEY=

# Codat
CODAT_API_KEY=
CODAT_WEBHOOK_SECRET=

# Instantly.ai
INSTANTLY_API_KEY=

# Vapi
VAPI_API_KEY=

# ElevenLabs (if using directly rather than via Vapi)
ELEVENLABS_API_KEY=

# Stripe
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=

# Supabase
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=

# Slack (notifications)
SLACK_WEBHOOK_URL=

# Application
AGENT_DEFAULT_NAME=Alex
AGENT_DEFAULT_EMAIL=
DEFAULT_CURRENCY=GBP
MAX_DISCOUNT_PERCENT=3
FEE_FLAT_AMOUNT=500
FEE_PERCENTAGE=10
FEE_PERCENTAGE_THRESHOLD=5000
```

---

## 13. Getting Started with Claude Code

To kick off development, run this sequence:

```bash
# 1. Initialise the project
mkdir oaas-collections-agent && cd oaas-collections-agent
git init

# 2. Set up Python environment
python -m venv .venv
source .venv/bin/activate
pip install anthropic requests supabase stripe python-dotenv

# 3. Create the file structure
# (Claude Code: create the directory tree from Section 11)

# 4. Start with the MVP components in this order:
#    a) CSV importer (src/sentry/csv_importer.py)
#    b) Database models (src/db/models.py)
#    c) State machine (src/strategist/state_machine.py)
#    d) Response classifier (src/strategist/response_classifier.py)
#    e) Message generator (src/strategist/message_generator.py)
#    f) Email sender via Instantly (src/executor/email_sender.py)
#    g) Cadence engine (src/executor/cadence.py)
#    h) Main loop / scheduler (src/main.py)

# 5. Write tests for the classifier and state machine first
# 6. Seed test data and run end-to-end with fake invoices
```

---

## 14. Key Design Principles

1. **Never sound like a bot.** Every message should read as if a competent human wrote it in 30 seconds. Short, warm, specific.

2. **Never threaten.** The hardest language permitted is "external compliance partner." The agent wins through empathy and psychology, not intimidation.

3. **Fail safe.** On DISPUTE or HOSTILE, the agent stops immediately. A human must clear the flag before it re-engages. False positives (pausing unnecessarily) are preferable to false negatives (continuing to message an angry debtor).

4. **Variable cadence.** The agent must never look automated. Randomise send times within business-hour windows. Never contact the same person twice in one day.

5. **Audit everything.** Every outbound message and every classification decision is logged with full context. This protects both the SME and us.

6. **Start with CSV, graduate to API.** The MVP must work without any accounting integration. A spreadsheet upload is good enough to close the first five clients.

7. **The "reply to sent" trick.** Stewart's technique of replying to your own sent email with "Sending this again in case it didn't arrive..." is a first-class follow-up pattern, not a hack. Implement it properly in the cadence engine.
