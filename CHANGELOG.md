# Changelog

## 2026-07-08

- Upgraded the customer-insight workflow from generic profiling to pain-led outreach:
  - customer profiles now include `pain_point_strategy` with suspected pain, outreach angle, evidence, question, and avoid-list;
  - profiles now include a 14-day follow-up plan for Day 1 / Day 3 / Day 7 / Day 14 outreach;
  - AI profile prompts now require strict, evidence-bound JSON and avoid invented pain points.
- Improved personalized email generation:
  - AI drafts now follow a pain-led five-part structure;
  - fallback drafts now use the customer pain strategy and keep a low-barrier "brief reply" ask.
- Updated the customer workspace UI to show pain-point strategy and 14-day follow-up plan directly in the customer detail view.
- Added regression coverage for pain strategy and follow-up plan generation.

## 2026-07-07

- Reworked the frontend navigation around a six-agent outbound workflow:
  - 工作台 shows the six AI employee map and operating metrics;
  - 市场与获客 contains sourcing and import workflows;
  - 客户背调 contains the customer list and profile/research entry point;
  - 邮件触达 contains sent email history and delivery feedback;
  - 跟进任务 contains follow-up tasks, lifecycle funnel, and SABCD progression;
  - 主管周报 contains operations reporting and team/provider statistics.
- Raised production outbound capacity targets:
  - default sales user send quota is now 200 emails per day;
  - global daily send quota is now 6000 emails per day for 30-seat operation;
  - sender account daily limit is raised so the internal sender pool does not block the per-user quota.
- Forced outbound email signatures to use the logged-in sales account:
  - emails now end with `Best regards, <login display name> You, BD Manager Of Media East Region | VERTU`;
  - manual drafts, custom sends, and queued sequence sends share the same backend signature normalization;
  - sender `reply_to_email` continues to route customer replies to the logged-in sales account mailbox.
- Updated the customer workspace default email draft preview to match the production signature format.
- Added an operations script to align sales users and reply-to mailboxes in production without resetting existing passwords.
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
