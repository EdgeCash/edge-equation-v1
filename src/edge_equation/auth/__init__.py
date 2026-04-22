"""
Authentication + subscription state.

Passwordless email-magic-link sign-in; opaque session cookies stored in the
same SQLite (or Turso) DB the rest of the engine writes to.

Modules:
- users.py         UserStore (find_or_create, get_by_id / by_email, set_stripe_customer_id).
- tokens.py        AuthTokenStore (mint, consume) for magic-link round-trips.
- sessions.py      SessionStore (create, lookup, revoke) + cookie helpers.
- subscriptions.py SubscriptionStore (upsert from Stripe webhook, active lookup).
- email.py         send_magic_link() using the same stdlib SMTP config as the
                   publisher and failsafe layers.

All stores are frozen dataclasses + staticmethod classes, matching the rest
of the persistence layer. Nothing here calls Stripe -- that's in
stripe_client.py at the repo top level.
"""
