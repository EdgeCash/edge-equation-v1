"""
Stripe subscription endpoints.

  POST /stripe/create-checkout-session   (authenticated)
      Ensures the user has a Stripe customer id, then starts a hosted
      Checkout Session for the configured recurring price. Returns the
      Checkout URL; the frontend redirects the browser there.

  POST /stripe/create-portal-session     (authenticated)
      Starts a hosted Billing Portal session so the user can manage or
      cancel. Returns the portal URL.

  POST /stripe/webhook                   (unauthenticated; Stripe-signed)
      Verifies the Stripe-Signature header, maps the event to a
      subscription upsert, and returns 200. Supports:
        - checkout.session.completed
        - customer.subscription.updated
        - customer.subscription.deleted

Note: naming is stripe_router.py (not stripe.py) to avoid shadowing the
official `stripe` package in any future dep addition.
"""
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from api.routers.auth import require_user
from edge_equation.auth.subscriptions import SubscriptionStore
from edge_equation.auth.users import User, UserStore
from edge_equation.persistence.db import Database
from edge_equation.stripe_client import (
    ENV_PRICE_ID,
    StripeClient,
    StripeError,
)


router = APIRouter(prefix="/stripe", tags=["stripe"])


WEBSITE_BASE_URL_ENV = "WEBSITE_BASE_URL"


def _open_db():
    conn = Database.open(Database.resolve_path(None))
    Database.migrate(conn)
    return conn


def _website_base() -> str:
    return os.environ.get(WEBSITE_BASE_URL_ENV, "http://localhost:3000").rstrip("/")


def _require_price_id() -> str:
    pid = os.environ.get(ENV_PRICE_ID)
    if not pid:
        raise HTTPException(status_code=503, detail=f"{ENV_PRICE_ID} not set")
    return pid


# The Stripe client factory is exposed for test injection -- tests pass their
# own httpx.MockTransport-backed client here.
def _stripe_client() -> StripeClient:
    return StripeClient()


@router.post("/create-checkout-session")
def create_checkout_session(
    user: User = Depends(require_user),
    stripe: StripeClient = Depends(_stripe_client),
) -> dict:
    price_id = _require_price_id()
    conn = _open_db()
    try:
        try:
            if not user.stripe_customer_id:
                customer = stripe.create_customer(
                    email=user.email,
                    metadata={"user_id": user.user_id},
                )
                UserStore.set_stripe_customer_id(conn, user.user_id, customer["id"])
                customer_id = customer["id"]
            else:
                customer_id = user.stripe_customer_id

            base = _website_base()
            session = stripe.create_checkout_session(
                customer_id=customer_id,
                price_id=price_id,
                success_url=f"{base}/account?checkout=ok",
                cancel_url=f"{base}/premium-edge?checkout=cancelled",
                metadata={"user_id": user.user_id},
            )
        except StripeError as e:
            raise HTTPException(status_code=502, detail=f"stripe: {e}")
    finally:
        conn.close()

    return {"url": session.get("url"), "id": session.get("id")}


@router.post("/create-portal-session")
def create_portal_session(
    user: User = Depends(require_user),
    stripe: StripeClient = Depends(_stripe_client),
) -> dict:
    if not user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="no Stripe customer for user")
    try:
        session = stripe.create_billing_portal_session(
            customer_id=user.stripe_customer_id,
            return_url=f"{_website_base()}/account",
        )
    except StripeError as e:
        raise HTTPException(status_code=502, detail=f"stripe: {e}")
    return {"url": session.get("url"), "id": session.get("id")}


@router.post("/webhook")
async def webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(default=None),
) -> JSONResponse:
    body = await request.body()
    try:
        event = StripeClient.verify_webhook(
            payload=body,
            signature_header=stripe_signature or "",
        )
    except StripeError as e:
        raise HTTPException(status_code=400, detail=f"bad signature: {e}")

    event_type = event.get("type") or ""
    data_object = ((event.get("data") or {}).get("object")) or {}

    conn = _open_db()
    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(conn, data_object)
        elif event_type in ("customer.subscription.updated",
                            "customer.subscription.created",
                            "customer.subscription.deleted"):
            _handle_subscription_event(conn, data_object)
        # Other events: accept and ignore (idempotent; Stripe will retry on
        # non-2xx so we mustn't 4xx random events we don't care about).
    finally:
        conn.close()
    return JSONResponse(content={"received": True})


def _handle_checkout_completed(conn, session_obj: dict) -> None:
    customer_id = session_obj.get("customer")
    subscription_id = session_obj.get("subscription")
    metadata = session_obj.get("metadata") or {}
    user_id: Optional[int] = None
    if "user_id" in metadata:
        try:
            user_id = int(metadata["user_id"])
        except (TypeError, ValueError):
            user_id = None
    if user_id is None and customer_id:
        user = UserStore.get_by_stripe_customer_id(conn, customer_id)
        user_id = user.user_id if user else None
    if user_id is None or not subscription_id:
        return  # webhook predates the user or is malformed; ignore
    # Persist the Stripe customer id on the user row if it wasn't already.
    if customer_id:
        existing = UserStore.get_by_id(conn, user_id)
        if existing and not existing.stripe_customer_id:
            UserStore.set_stripe_customer_id(conn, user_id, customer_id)
    # The subscription state itself is authoritative from the subscription
    # object; checkout.session.completed fires before subscription.created
    # in some flows. Seed with 'active' to entitle immediately; the follow-up
    # subscription.updated will overwrite with the canonical state.
    SubscriptionStore.upsert(
        conn,
        user_id=user_id,
        stripe_subscription_id=subscription_id,
        status="active",
        current_period_end=None,
        cancel_at_period_end=False,
    )


def _handle_subscription_event(conn, sub_obj: dict) -> None:
    stripe_sub_id = sub_obj.get("id")
    customer_id = sub_obj.get("customer")
    status = sub_obj.get("status") or "incomplete"
    current_period_end_unix = sub_obj.get("current_period_end")
    cancel_at_period_end = bool(sub_obj.get("cancel_at_period_end"))
    if not stripe_sub_id or not customer_id:
        return
    user = UserStore.get_by_stripe_customer_id(conn, customer_id)
    if user is None:
        return
    current_period_end: Optional[str] = None
    if isinstance(current_period_end_unix, (int, float)):
        from datetime import datetime
        current_period_end = datetime.utcfromtimestamp(
            int(current_period_end_unix)
        ).isoformat()
    SubscriptionStore.upsert(
        conn,
        user_id=user.user_id,
        stripe_subscription_id=stripe_sub_id,
        status=status,
        current_period_end=current_period_end,
        cancel_at_period_end=cancel_at_period_end,
    )
