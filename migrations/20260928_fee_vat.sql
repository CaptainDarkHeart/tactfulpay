-- TactfulPay: add VAT tracking to fees
-- Debt collection fees are excluded from the VAT financial services exemption
-- (Sch 9 Group 5, VATA 1994), so the fee is standard rated once VAT registered.
-- vat_amount is 0 until settings.vat_registered is true (see src/billing/fee_calculator.py).

alter table fees add column vat_amount decimal not null default 0;
