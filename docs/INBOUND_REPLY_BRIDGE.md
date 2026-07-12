# Corporate Mailbox Reply Bridge

The application sends with the configured Resend sender and sets each logged-in salesperson's `reply_to_email`. Customer replies therefore arrive in the salesperson's corporate mailbox. Resend delivery webhooks alone cannot see those mailbox replies.

Connect Outlook, Microsoft Graph, Power Automate, or another mailbox automation to:

```text
POST https://global-autoleads.vertu.cn/webhooks/inbound-email
Content-Type: application/json
X-Inbound-Secret: <INBOUND_EMAIL_WEBHOOK_SECRET>
```

Payload:

```json
{
  "from": "customer@example.com",
  "to": "salesperson@vertu.com",
  "subject": "Re: Possible Vertu channel fit",
  "text": "Customer reply body",
  "message_id": "inbound-message-id",
  "in_reply_to": "original-resend-message-id"
}
```

Matching order:

1. `in_reply_to` matches the original sent event's `message_id`.
2. Otherwise `from` matches the contact's email address.
3. Unmatched senders return `inbound_sender_not_matched` and are not attached to a customer.

Successful processing records a `replied` email event, creates a lifecycle reply activity, changes the contact status to `replied`, and advances SABCD to `B`.

Generate separate random secrets for `TRACKING_SIGNING_SECRET` and `INBOUND_EMAIL_WEBHOOK_SECRET`. Do not place either value in Git.
