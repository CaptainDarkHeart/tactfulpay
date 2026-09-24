-- OaaS Collections Agent: Initial Schema
-- Run this against your Supabase project's SQL editor

-- Enable UUID generation
create extension if not exists "uuid-ossp";

-- Enum types
create type accounting_platform as enum ('xero', 'quickbooks', 'freshbooks', 'sage', 'csv');
create type sme_status as enum ('active', 'paused', 'churned');
create type invoice_phase as enum ('1', '2', '3', '4', 'human_review', 'resolved', 'disputed');
create type invoice_status as enum ('active', 'paused', 'paid', 'disputed', 'written_off');
create type channel as enum ('email', 'voice', 'linkedin', 'sms');
create type direction as enum ('outbound', 'inbound');
create type message_type as enum ('initial', 'follow_up', 'response', 'escalation');
create type classification as enum ('promise_to_pay', 'payment_pending', 'dispute', 'redirect', 'stall', 'hostile', 'no_response');
create type contact_source as enum ('csv_upload', 'codat_sync', 'discovery_agent', 'redirect');
create type fee_type as enum ('percentage', 'flat');
create type fee_status as enum ('pending', 'charged', 'failed', 'waived');

-- SMEs (our clients)
create table smes (
    id uuid primary key default uuid_generate_v4(),
    company_name text not null,
    contact_email text not null,
    contact_phone text not null default '',
    accounting_platform accounting_platform not null default 'csv',
    codat_company_id text,
    stripe_customer_id text,
    discount_authorised boolean not null default false,
    max_discount_percent decimal not null default 0,
    onboarded_at timestamptz not null default now(),
    status sme_status not null default 'active'
);

-- Invoices being chased
create table invoices (
    id uuid primary key default uuid_generate_v4(),
    sme_id uuid not null references smes(id) on delete cascade,
    invoice_number text not null,
    debtor_company text not null,
    amount decimal not null,
    currency text not null default 'GBP',
    due_date date not null,
    current_phase invoice_phase not null default '1',
    status invoice_status not null default 'active',
    created_at timestamptz not null default now(),
    resolved_at timestamptz,
    fee_charged boolean not null default false,
    fee_amount decimal
);

-- Contacts at debtor companies
create table contacts (
    id uuid primary key default uuid_generate_v4(),
    invoice_id uuid not null references invoices(id) on delete cascade,
    name text not null,
    email text not null,
    phone text,
    linkedin_url text,
    role text,
    is_primary boolean not null default true,
    source contact_source not null default 'csv_upload'
);

-- All interactions (outbound messages + inbound replies)
create table interactions (
    id uuid primary key default uuid_generate_v4(),
    invoice_id uuid not null references invoices(id) on delete cascade,
    contact_id uuid not null references contacts(id) on delete cascade,
    phase integer not null check (phase between 1 and 4),
    channel channel not null,
    direction direction not null,
    message_type message_type not null,
    content text not null,
    classification classification,
    sent_at timestamptz not null default now(),
    delivered boolean not null default false,
    opened boolean,
    replied boolean not null default false,
    metadata jsonb not null default '{}'::jsonb
);

-- Fees charged to SMEs on successful recovery
create table fees (
    id uuid primary key default uuid_generate_v4(),
    invoice_id uuid not null references invoices(id) on delete cascade,
    sme_id uuid not null references smes(id) on delete cascade,
    fee_type fee_type not null,
    fee_amount decimal not null,
    invoice_amount_recovered decimal not null,
    stripe_payment_intent_id text,
    status fee_status not null default 'pending',
    created_at timestamptz not null default now(),
    charged_at timestamptz
);

-- Indexes for common queries
create index idx_invoices_sme_status on invoices(sme_id, status);
create index idx_invoices_phase on invoices(current_phase) where status = 'active';
create index idx_contacts_invoice on contacts(invoice_id);
create index idx_interactions_invoice on interactions(invoice_id);
create index idx_interactions_sent_at on interactions(invoice_id, sent_at desc);

-- Row Level Security (enable but allow service role full access)
alter table smes enable row level security;
alter table invoices enable row level security;
alter table contacts enable row level security;
alter table interactions enable row level security;
alter table fees enable row level security;

-- Service role bypass policies
create policy "Service role full access" on smes for all using (true) with check (true);
create policy "Service role full access" on invoices for all using (true) with check (true);
create policy "Service role full access" on contacts for all using (true) with check (true);
create policy "Service role full access" on interactions for all using (true) with check (true);
create policy "Service role full access" on fees for all using (true) with check (true);
