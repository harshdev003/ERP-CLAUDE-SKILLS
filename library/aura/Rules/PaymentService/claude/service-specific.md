---
description: Rules that apply to PaymentService only
globs:
alwaysApply: true
---

# PaymentService rules

Rules for this service only. The rules shared by every backend service are in the other files in this directory and are identical in all 13 services; a general rule goes there, not here.

- **`POST /payments/stripe/getCustomerId(/:userId)` is a read-only lookup.** It returns `{customerId, userId}`, with `customerId: null` when the user has no Stripe customer, and never creates a customer or attaches a card. Creating a customer happens only on the checkout paths (`charge`, `subscribe`, `createCard`, payment intents), which call the model's `getCustomerId` internally. Never route a display or support lookup through a get-or-create path. Why: the Front support sidebar (AuraFrontPlugin) used to call the old get-or-create route on every user it opened and created 23 Stripe customers on 2026-10-23, including one for a person asking to be erased (ENGBACK-1874).
