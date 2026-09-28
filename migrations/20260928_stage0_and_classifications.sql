-- TactfulPay: add Stage 0 (pre-due admin verification) and two new
-- inbound classification categories (from Stewart's TactfulPay v2 spec).
--
-- Stage 0 runs days -3 to 0 before the due date, catching PO/vendor
-- onboarding issues before an invoice is even overdue.
--
-- CHECK_OR_TRANSFER_INITIATED and INABILITY_TO_PAY are added alongside the
-- existing categories, not replacing them: HOSTILE and WRITE_OFF_CLAIMED stay,
-- since HOSTILE is a hard pause safeguard (see constraints.PAUSE_CLASSIFICATIONS).

alter type invoice_phase add value if not exists '0';
alter type classification add value if not exists 'check_or_transfer_initiated';
alter type classification add value if not exists 'inability_to_pay';

alter table interactions drop constraint if exists interactions_phase_check;
alter table interactions add constraint interactions_phase_check check (phase between 0 and 4);
