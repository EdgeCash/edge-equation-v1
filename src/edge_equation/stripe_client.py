"""
Stripe HTTP client.

Thin httpx-based wrapper around the four Stripe REST endpoints we actually
use -- no extra dependency on the official `stripe` SDK. Matches the
"minimal deps, deterministic, auditable" ethos of the rest of the codebase.

Endpoints we talk to:
  POST /v1/customers                   (lazy-create on first checkout)
  POST /v1/checkout/sessions           (start a subscription purchase)
  POST /v1/billing_portal/sessions     (manage / cancel)
  GET  /v1/subscriptions/{id}          (fetch status at any time)

Plus webhook signature verification: compute HMAC-SHA256 over the raw
request body prefixed by the Stripe timestamp, and constant-time compare
with every candidate signature in the Stripe-Signature header.

Env vars:
  STRIPE_SECRET_KEY        Required for any API call.
  STRIPE_WEBHOOK_SECRET    Required by verify_webhook.
  STRIPE_PRICE_ID          Recurring price id used for checkout sessions.

All methods return the parsed JSON body. HTTP errors raise StripeError.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional
import hashlib
import hmac
import os
import time

import httpx


API_BASE = "https://api.stripe.com"

ENV_SECRET_KEY = "STRIPE_SECRET_KEY"
ENV_WEBHOOK_SECRET = "STRIPE_WEBHOOK_SECRET"
ENV_PRICE_ID = "STRIPE_PRICE_ID"

DEFAULT_TOLERANCE_SECONDS = 5 * 60  # 5 minutes, Stripe's recommendation


class StripeError(RuntimeError):
    """Raised when a Stripe HTTP call fails or a webhook signature is invalid."""


def _flatten_form(data: Mapping[str, Any], parent: str = "") -> List[tuple]:
    """
    Stripe's REST API takes application/x-www-form-urlencoded bodies with
    bracket-notation for nested fields. Turn {"metadata": {"user_id": 1}}
    into [("metadata[user_id]", "1")].
    """
    out: List[tuple] = []
    for k, v in data.items():
        key = f"{parent}[{k}]" if parent else k
        if isinstance(v, Mapping):
            out.extend(_flatten_form(v, key))
        elif isinstance(v, (list, tuple)):
            for idx, item in enumerate(v):
                if isinstance(item, Mapping):
                    out.extend(_flatten_form(item, f"{key}[{idx}]"))
                else:
                    out.append((f"{key}[{idx}]", str(item)))
        elif isinstance(v, bool):
            out.append((key, "true" if v else "false"))
        elif v is None:
            # Stripe treats empty strings as unset; skip outright.
            continue
        else:
            out.append((key, str(v)))
    return out


class StripeClient:
    """
    Thin Stripe REST client:
    - create_customer(email, metadata=None)
    - create_checkout_session(customer_id, price_id, success_url, cancel_url, metadata=None)
    - create_billing_portal_session(customer_id, return_url)
    - get_subscription(subscription_id)
    - verify_webhook(payload_bytes, signature_header, secret=None, now=None, tolerance=300)
      -> parsed event dict (raises StripeError on mismatch / replay)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
        api_base: str = API_BASE,
    ):
        self.api_key = api_key or os.environ.get(ENV_SECRET_KEY)
        self._http = http_client or httpx.Client(timeout=30.0)
        self._owns_client = http_client is None
        self.api_base = api_base.rstrip("/")

    def close(self) -> None:
        if self._owns_client:
            try:
                self._http.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # -------------------------------------------------- HTTP helpers

    def _require_key(self) -> str:
        if not self.api_key:
            raise StripeError(f"{ENV_SECRET_KEY} not set")
        return self.api_key

    def _post(self, path: str, data: Mapping[str, Any]) -> Dict[str, Any]:
        from urllib.parse import urlencode
        key = self._require_key()
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        body = urlencode(_flatten_form(data))
        resp = self._http.post(
            f"{self.api_base}{path}",
            content=body.encode("utf-8"),
            headers=headers,
        )
        if resp.status_code >= 400:
            raise StripeError(f"HTTP {resp.status_code} on {path}: {resp.text[:300]}")
        return resp.json()

    def _get(self, path: str) -> Dict[str, Any]:
        key = self._require_key()
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        }
        resp = self._http.get(f"{self.api_base}{path}", headers=headers)
        if resp.status_code >= 400:
            raise StripeError(f"HTTP {resp.status_code} on {path}: {resp.text[:300]}")
        return resp.json()

    # -------------------------------------------------- API surface

    def create_customer(
        self,
        email: str,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {"email": email}
        if metadata:
            data["metadata"] = dict(metadata)
        return self._post("/v1/customers", data)

    def create_checkout_session(
        self,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "customer": customer_id,
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items": [{"price": price_id, "quantity": 1}],
            "allow_promotion_codes": True,
        }
        if metadata:
            data["metadata"] = dict(metadata)
        return self._post("/v1/checkout/sessions", data)

    def create_billing_portal_session(
        self,
        customer_id: str,
        return_url: str,
    ) -> Dict[str, Any]:
        return self._post(
            "/v1/billing_portal/sessions",
            {"customer": customer_id, "return_url": return_url},
        )

    def get_subscription(self, subscription_id: str) -> Dict[str, Any]:
        return self._get(f"/v1/subscriptions/{subscription_id}")

    # -------------------------------------------------- webhook verify

    @staticmethod
    def verify_webhook(
        payload: bytes,
        signature_header: str,
        secret: Optional[str] = None,
        now: Optional[int] = None,
        tolerance: int = DEFAULT_TOLERANCE_SECONDS,
    ) -> Dict[str, Any]:
        """
        Verify Stripe's Stripe-Signature header over the raw POST body. Returns
        the parsed event dict if valid; raises StripeError otherwise.

        Header format: "t=<unix-ts>,v1=<hex-sig>[,v1=<hex-sig>...]"
        signed payload: "<t>.<body>"
        sig: HMAC-SHA256(secret, signed_payload).hexdigest()
        """
        if not signature_header:
            raise StripeError("missing Stripe-Signature header")
        key = secret if secret is not None else os.environ.get(ENV_WEBHOOK_SECRET)
        if not key:
            raise StripeError(f"{ENV_WEBHOOK_SECRET} not set")

        timestamp: Optional[str] = None
        candidate_sigs: List[str] = []
        for part in signature_header.split(","):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            if k == "t":
                timestamp = v
            elif k == "v1":
                candidate_sigs.append(v)
        if timestamp is None or not candidate_sigs:
            raise StripeError("malformed Stripe-Signature header")

        now_ts = int(now) if now is not None else int(time.time())
        try:
            ts_int = int(timestamp)
        except ValueError:
            raise StripeError("non-integer timestamp in Stripe-Signature")
        if abs(now_ts - ts_int) > int(tolerance):
            raise StripeError(
                f"webhook timestamp {ts_int} outside tolerance ({tolerance}s) of now {now_ts}"
            )

        signed_payload = f"{timestamp}.".encode("utf-8") + payload
        expected = hmac.new(
            key.encode("utf-8"), signed_payload, hashlib.sha256,
        ).hexdigest()
        for sig in candidate_sigs:
            if hmac.compare_digest(expected, sig):
                import json
                return json.loads(payload.decode("utf-8"))
        raise StripeError("no matching v1 signature in Stripe-Signature header")
