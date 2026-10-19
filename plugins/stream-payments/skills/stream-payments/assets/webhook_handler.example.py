"""Reference webhook handler for PSP payment notifications (Flask).

This is a TEMPLATE showing the required behavior; adapt it to the merchant's
actual framework, ORM and logging. Equivalents for Spring Boot, Express/NestJS
and FastAPI — including the raw-body recipe for each — are in
`references/code-examples-java.md`, `references/code-examples-node.md` and
`references/code-examples-python.md`; the rules they implement are in
`references/integration-patterns.md`.

The five properties that must survive any adaptation:

  1. Verify the HMAC-SHA256 `Signature` header against the RAW body first.
  2. Be idempotent — the same webhook may be delivered more than once, and a
     webhook may race with your own GET /payments/{id} polling.
  3. Map payment state -> order state through an explicit whitelist;
     never mark an order paid on anything but a final success state.
  4. Answer 2xx quickly; do slow work (emails, fulfilment) asynchronously.
  5. Never log full card numbers, tokens or the signing key.
"""
import hashlib
import hmac
import json
import os

from flask import Flask, request

app = Flask(__name__)

SIGNING_KEY = os.environ["STREAM_SIGNING_KEY"].encode()

# Payment state -> merchant order state. Explicit whitelist: anything not
# listed here must NOT change the order.
#
# AUTHORIZED means the funds are only held: a capture is still required before
# the money is yours, so it must not trigger fulfilment. Only COMPLETED is
# "paid". See references/payment-lifecycle.md.
FINAL_STATE_MAPPING = {
    "COMPLETED": "PAID",
    "AUTHORIZED": "AUTHORIZED",
    "DECLINED": "PAYMENT_FAILED",
    "CANCELLED": "PAYMENT_FAILED",
}

# Order states that mean "money is settled" — the only ones allowed to kick off
# fulfilment.
FULFILMENT_STATES = {"PAID"}


def signature_valid(raw_body: bytes, header_value: str) -> bool:
    """Constant-time check of the inbound Signature header.

    The header is the LOWERCASE HEX HMAC-SHA256 of the raw body, keyed with
    the Shop Signing Key. Bytes are compared, not str: compare_digest() raises
    TypeError on non-ASCII str, which would turn a garbage header into a 500.
    """
    if not header_value:
        return False
    expected = hmac.new(SIGNING_KEY, raw_body, hashlib.sha256).hexdigest().encode()
    provided = header_value.strip().lower().encode("utf-8", "replace")
    return hmac.compare_digest(expected, provided)


@app.post("/webhooks/psp")
def psp_webhook():
    raw = request.get_data()  # RAW bytes — do not use request.json for signing
    if not signature_valid(raw, request.headers.get("Signature", "")):
        # Wrong key or forged request: reject, do not reveal details.
        return {"error": "invalid signature"}, 401

    event = json.loads(raw)
    payment_id = event.get("id")
    payment_state = event.get("state")

    order = find_order_by_payment(payment_id, event.get("referenceId"))
    if order is None:
        # The payment may be genuinely unknown, OR this webhook simply overtook
        # the create-payment response that stores the id — a real race. Either
        # way the event must NOT be dropped: persist it as unresolved and let a
        # replay job re-apply it once the payment is linked. Acknowledge with
        # 2xx so the PSP does not keep retrying an event we have safely stored.
        record_unresolved_event(payment_id, event.get("referenceId"), raw)
        app.logger.warning("webhook for unlinked payment %s — stored for replay",
                           payment_id)
        return {"status": "deferred"}, 200

    new_state = FINAL_STATE_MAPPING.get(payment_state)
    if new_state is None:
        # Non-final or unrecognized state: acknowledge, change nothing.
        app.logger.info("webhook for payment %s in state %s — ignored",
                        payment_id, payment_state)
        return {"status": "ignored"}, 200

    # Idempotency: a transition applied twice must be a no-op.
    if not order_transition(order, new_state, payment_id=payment_id, event=event):
        return {"status": "duplicate"}, 200

    if new_state in FULFILMENT_STATES:
        enqueue_post_payment_work(order)  # async: emails, fulfilment, etc.
    return {"status": "ok"}, 200


# --- integrate these with the merchant's persistence layer -------------------

def find_order_by_payment(payment_id, reference_id):
    raise NotImplementedError


def order_transition(order, new_state, **audit) -> bool:
    """Atomically apply the transition; return False if already applied.

    Implement with a conditional write over an explicit whitelist of source
    states, e.g.
    `UPDATE orders SET status = :new WHERE id = :id AND status IN (:allowed_from)`
    and return whether a row was affected — never read-then-write. The
    whitelist, not just `status <> :new`, is what stops a late or out-of-order
    delivery from downgrading an already-final order.

    Book the amount/currency from the webhook payload, not from what you
    requested: for some payment methods the final amount differs.
    """
    raise NotImplementedError


def record_unresolved_event(payment_id, reference_id, raw_body):
    """Persist a webhook that could not be linked to an order yet.

    Store the raw body (signature already verified) plus a received timestamp,
    and have a periodic job retry these — and reconcile long-unresolved ones via
    `GET /api/v1/payments/{id}`. Redelivery is not documented by the PSP, so do
    not count on the event arriving again.

    Two traps when you write that replay job (see
    `references/hardening-concurrency.md` for the full pattern):

    - The stored event may be *stale*. If a receipt says `AUTHORIZED` but the
      payment has since reached `COMPLETED`, apply the current state and then
      close the original row as superseded — otherwise it stays unresolved
      forever and, with a LIMIT on the sweep, starves newer events.
    - Claim rows with `FOR UPDATE SKIP LOCKED` (or an equivalent lease) so two
      sweeps do not process the same receipt, and do not hold a row lock across
      the network call.
    """
    raise NotImplementedError


def enqueue_post_payment_work(order):
    raise NotImplementedError
