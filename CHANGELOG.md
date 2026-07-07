# Changelog

## 2026-07-07

- Forced outbound email signatures to use the logged-in sales account:
  - emails now end with `Best regards, <login display name> You, BD Manager Of Media East Region | VERTU`;
  - manual drafts, custom sends, and queued sequence sends share the same backend signature normalization;
  - sender `reply_to_email` continues to route customer replies to the logged-in sales account mailbox.
- Updated the customer workspace default email draft preview to match the production signature format.
- Added regression tests for sales-account signatures and reply-to behavior.

## 2026-07-06

- Fixed Resend webhook feedback handling for production email callbacks:
  - verifies Resend/Svix webhook signatures;
  - matches contacts by Resend `email_id` / `message_id`;
  - falls back to recipient email matching when message metadata is incomplete;
  - marks successfully processed webhook deliveries with `processed_at`;
  - allows Resend retries to reprocess previously failed webhook deliveries instead of permanently treating them as duplicates.
- Added safer outbound filtering before queueing or sending:
  - skips common role-based inboxes such as `info@`, `sales@`, `support@`, `contact@`;
  - blocks low-score or assistant/support style contacts from automatic sends;
  - annotates risky delivery payloads for bounce and complaint follow-up.
- Added per-user `reply_to_email` support so sales accounts can receive replies in their own inbox when configured.
- Improved LinkedIn public search / regional sourcing logic, including MENA-focused search term expansion.
- Updated admin and contact pipeline UI build for the latest permissions, customer list, email feedback, and sales account tooling.
- Added account export and sales training PPT helper scripts under `tools/`.
- Added tests for webhook fallback matching, reply-to handling, outbound guard logic, and LinkedIn public search behavior.
