# Changelog

## 2026-07-12

- Reduced sales-page startup work by loading sourcing, customer, outreach, reporting, and admin modules only after their first visit; dashboard data now requests only what the active page needs.
- Shortened the mobile customer review flow from 25 to 10 rows per page, added five direct customer filters, reset scroll position on route changes, and removed the duplicate four-step navigation on narrow screens.
- Added a direct customer picker to the email workspace and split the administrator console into account, sender, automation, assignment, and audit sections.
- Added configurable database-backed region assignment rules, post-sourcing fallback profiles, automatic private-pool assignment, and optional preparation of evidence-backed email drafts without automatic sending.
- Added team conversion-funnel and blocker metrics covering assignment, valid email, profile, draft, approval, send, open, reply, qualification, and signing.
- Added owner-isolated email work queues for missing drafts, pending approval, and approved sends; background task results now link directly to customer verification and email approval.
- Split email outreach into a customer workspace and an on-demand paginated send/feedback log, preventing 150 historical emails from loading or rendering during normal drafting.
- Fixed customer-list enrichment so fallback profile intelligence is applied after database reads, and added an end-to-end mocked send-to-reply lifecycle regression test.
- Improved administrator and customer-list accessibility with labeled per-user sender fields and larger high-frequency table actions while preserving compact desktop density.
- Reorganized the sales UI into a guided four-step workflow with prioritized next actions, one primary action per customer, actionable follow-up cards, and private-pool-only lifecycle reporting.
- Replaced synchronous bulk company imports with durable background automation runs that survive refreshes and process restarts, expose progress, and support pause, resume, and retry.
- Added mandatory email draft approval: sales users can only send content that exactly matches the latest approved draft; edits revoke the local approval state and direct bulk-send APIs are administrator-only.
- Added automatic three-touch sequence closure: engaged contacts without replies move to the waiting pool, while contacts with no engagement move to abandoned after the configured cooling period.
- Fixed manual contact creation without a LinkedIn URL by generating a stable internal contact identity.
- Added administrator task monitoring and fixed public-pool contacts leaking into personal follow-up and SABCD views.
- Verified 128 tests, zero Chrome console errors, responsive layouts without page overflow, and 100 Lighthouse scores for accessibility, best practices, SEO, and agentic browsing.

## 2026-07-11

- Added production SMTP transport with SSL/STARTTLS, multipart text/HTML, signed Reply-To, safe envelope sender handling, and no ambiguous automatic retry.
- Added centralized-mailbox safety: per-user From aliases remain disabled unless the SMTP administrator grants explicit Send As permission.
- Added SMTP readiness checks, environment templates, regression coverage, and `docs/SMTP_TRANSPORT_SETUP.md`.

## 2026-07-10

- Added centralized outbound identity: each sales user gets a customer-visible alias on one verified sending subdomain without sharing individual SMTP credentials.
- Added HMAC-signed per-message reply addresses that restore replies to the current or original sales owner and flag truly unassigned replies for review.
- Added Resend `email.received` content retrieval so lifecycle records store the customer's actual reply text instead of metadata only.
- Added user-level sender alias administration, reply deduplication, a `unassigned_replies` customer filter, migration `023_centralized_outbound_identity.sql`, and a production runbook.
- Added evidence-based exact-person matching using name, company website, title, country, and industry.
- Added persisted company/person/news research with Brave, Tavily, and Google CSE fallback sources.
- Grounded AI email drafts in saved research evidence and persisted drafts per contact and sales user.
- Added a six-stage customer workflow showing identity, email, research, draft, send, and feedback status.
- Added signed tracking and unsubscribe tokens to prevent forged contact events.
- Added Resend idempotency keys and database send reservations to block duplicate sends.
- Added secure inbound reply ingestion; matched replies create lifecycle activities and enter SABCD C without downgrading later stages.
- Preserved pre-migration open and unsubscribe links while requiring signed tokens for all newly generated emails.
- Made automatic SABCD movement monotonic: first touch/reply C, multi-round communication B, commercial work A, signed customer S.
- Simplified the sales dashboard to metrics and four direct actions; removed training and AI-workforce marketing content.
- Mounted protected React modules only after authentication, removing duplicate unauthorized API requests on the login screen.
- Hid team reports and the administrator console from sales navigation and blocked direct hash navigation to both views.
- Added static-asset caching, local fonts, accessible form labels, stronger color contrast, and a page description; desktop Lighthouse now passes all audited categories.
- Administrator password resets now revoke every existing session for the affected user.
- Reduced the customer table from 16 columns to 7 business-focused columns for production usability.
- Added migration `022_identity_research_pipeline.sql` and expanded production readiness checks.

## 2026-07-09

- Added Odoo / VPS SSO support:
  - frontend detects `session_id` + `user_id` from VPS/Odoo entry URLs and exchanges them for the local session;
  - backend verifies the Odoo session via `/web/session/get_session_info` and rejects uid mismatches;
  - sales users can be matched or auto-created by Odoo user id, employee barcode, or reply-to email;
  - iframe cookie mode supports `SameSite=None; Secure` in HTTPS production.

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
- Improved production UI readiness:
  - dashboard now includes a sales playbook / admin launch checklist;
  - import batches now show clear next-step advice based on email coverage and sent count;
  - bounced, unsubscribed, and complained contacts no longer expose queue/send actions in the sales table;
  - sent email history now shows the reply-to mailbox used for customer replies.
- Added LinkedIn contact enrichment documentation for internal implementation and external partner explanation.

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
