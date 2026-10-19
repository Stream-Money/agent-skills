#!/usr/bin/env python3
"""Build and send Stream Gateway API requests (sandbox by default).

Stdlib only. Credentials come from the environment (never hardcode them):

    STREAM_API_URL   base URL, e.g. the sandbox URL from wl-config.md
    STREAM_API_KEY   Shop API Key (Bearer)

Usage:
    python3 psp_call.py deposit  --body deposit_request.json [--dry-run]
    python3 psp_call.py payment  <payment-id>          # GET /payments/{id}
    python3 psp_call.py execute  <payment-id> --customer-ip 203.0.113.10
                                                       # PATCH /payments/{id}: server-to-server step 2
    python3 psp_call.py find     <reference-id>        # reconcile by referenceId
    python3 psp_call.py refund   <parent-payment-id> --amount 10.01 --currency EUR
    python3 psp_call.py capture  <payment-id> [--amount 10.01]
    python3 psp_call.py void     <payment-id>
    python3 psp_call.py post     /api/v1/payments --body raw.json   # arbitrary POST
    python3 psp_call.py demo-deposit [--outcome completed|declined|cancelled]
                                                       # sandbox smoke test: India UPI, INR
    python3 psp_call.py demo-deposit --method card     # sandbox smoke test: card, EUR

`--dry-run` prints the equivalent curl command instead of sending.

This tool is meant for the SANDBOX. Money-moving commands refuse to run unless
the base URL looks like a sandbox, or you pass `--i-know-this-is-production`.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API_PREFIX = "/api/v1"

# Commands that create or move money — guarded against accidental production use.
MONEY_MOVING = {"deposit", "demo-deposit", "execute", "refund", "capture", "void", "post"}

# Hosts from wl-config.md. Anything else falls back to the name heuristic below.
SANDBOX_HOSTS = {"app-demo.stream.money"}
PRODUCTION_HOSTS = {"app.stream.money"}

# India UPI sandbox: the amount decides the final state (testing-and-sandbox.md §2).
UPI_SANDBOX_OUTCOMES = {"completed": 2000, "declined": 4000, "cancelled": 3000}


def env(name):
    value = os.environ.get(name)
    if not value:
        sys.exit(f"error: {name} is not set. See wl-config.md for the correct value.")
    return value


def guard_production(command, allow_production):
    """Refuse money-moving calls against a non-sandbox-looking base URL.

    Known hosts come from wl-config.md; for any other host this is a heuristic
    looking for 'sandbox', 'demo', 'uat' or 'test'. Passing
    `--i-know-this-is-production` is the deliberate override.
    """
    if command not in MONEY_MOVING or allow_production:
        return
    host = (urllib.parse.urlparse(env("STREAM_API_URL")).hostname or "").lower()
    looks_sandbox = host in SANDBOX_HOSTS or (
        host not in PRODUCTION_HOSTS
        and any(m in host for m in ("sandbox", "demo", "uat", "test", "localhost")))
    if not looks_sandbox:
        sys.exit(
            f"refusing to run '{command}' against '{host}': it does not look like a\n"
            "sandbox host. Point STREAM_API_URL at the sandbox (see wl-config.md), or\n"
            "pass --i-know-this-is-production if you really mean it."
        )


def request(method, path, body=None, dry_run=False):
    base = env("STREAM_API_URL").rstrip("/")
    key = env("STREAM_API_KEY")
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    # Some WL environments sit behind a WAF (e.g. Cloudflare) that blocks the
    # urllib default User-Agent ("Python-urllib/x.y") with 403 error code 1010.
    # Send an explicit, non-bot User-Agent so the sandbox smoke test works there.
    headers = {
        "Authorization": f"Bearer {key}",
        "User-Agent": "stream-payments-skill/psp_call",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"

    if dry_run:
        curl = [f"curl -X {method} '{url}'", "-H 'Authorization: Bearer $STREAM_API_KEY'"]
        if data is not None:
            curl += ["-H 'Content-Type: application/json'", f"-d '{json.dumps(body)}'"]
        print(" \\\n  ".join(curl))
        return None

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            payload = json.loads(e.read().decode())
        except Exception:
            payload = {"raw": "non-JSON error body"}
    except urllib.error.URLError as e:
        # Transport failure: for a POST the outcome is UNKNOWN, not a failure —
        # reconcile with `find <referenceId>` before retrying anything.
        sys.exit(f"error: could not reach {url}: {e.reason}\n"
                 + ("the request may or may not have been processed — reconcile with\n"
                    "`psp_call.py find <referenceId>` before retrying."
                    if method == "POST" else ""))
    print(f"HTTP {status}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return payload


def load_body(path):
    with open(path) as f:
        return json.load(f)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command")
    p.add_argument("arg", nargs="?", help="payment id or path, depending on command")
    p.add_argument("--body", help="JSON file with the request body")
    p.add_argument("--amount", type=float)
    p.add_argument("--currency")
    p.add_argument("--reference-id", help="your unique referenceId for this attempt")
    p.add_argument("--method", choices=("upi", "card"), default="upi",
                   help="demo-deposit only: India UPI (default) or card")
    p.add_argument("--outcome", choices=sorted(UPI_SANDBOX_OUTCOMES), default="completed",
                   help="demo-deposit --method upi only: the sandbox result to simulate")
    p.add_argument("--customer-ip", help="execute only: the customer's real public IP")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--i-know-this-is-production", action="store_true",
                   help="override the sandbox-only guard on money-moving commands")
    a = p.parse_args()

    if not a.dry_run:
        guard_production(a.command, a.i_know_this_is_production)

    if a.command == "deposit":
        if not a.body:
            sys.exit("error: 'deposit' requires --body <file.json> (the payment request).")
        body = load_body(a.body)
        body.setdefault("paymentType", "DEPOSIT")
        if a.reference_id:
            body["referenceId"] = a.reference_id
        request("POST", f"{API_PREFIX}/payments", body, a.dry_run)
    elif a.command == "demo-deposit":
        # Minimal smoke test: referenceId is optional (Stream assigns the payment
        # id), so we omit it. Pass --reference-id if you want to reconcile later.
        if a.method == "upi":
            # India UPI on the sandbox: the amount picks the final state, reached
            # after ~2 minutes; the webhook then fires as in production.
            body = {
                "paymentType": "DEPOSIT",
                "paymentMethod": "UPI",
                "amount": UPI_SANDBOX_OUTCOMES[a.outcome],
                "currency": "INR",
                "customer": {"referenceId": "skill-test-customer",
                             "email": "test@example.com", "phone": "91 9895270649"},
                "billingAddress": {"countryCode": "IN"},
            }
        else:
            body = {
                "paymentType": "DEPOSIT",
                "paymentMethod": "BASIC_CARD",
                "amount": 10.01,
                "currency": a.currency or "EUR",
                "customer": {"referenceId": "skill-test-customer", "email": "test@example.com"},
            }
        if a.reference_id:
            body["referenceId"] = a.reference_id
        request("POST", f"{API_PREFIX}/payments", body, a.dry_run)
    elif a.command == "payment":
        request("GET", f"{API_PREFIX}/payments/{a.arg}", None, a.dry_run)
    elif a.command == "execute":
        if not a.customer_ip:
            sys.exit("error: execute requires --customer-ip (the CUSTOMER's public IP,\n"
                     "not your server's).")
        request("PATCH", f"{API_PREFIX}/payments/{a.arg}", {"customerIp": a.customer_ip},
                a.dry_run)
    elif a.command == "find":
        query = urllib.parse.urlencode({"referenceId.eq": a.arg})
        request("GET", f"{API_PREFIX}/payments?{query}", None, a.dry_run)
    elif a.command == "refund":
        if not a.reference_id:
            sys.exit("error: refund requires --reference-id (a unique reference for\n"
                     "this refund attempt). Persist it before calling, so a timeout\n"
                     "can be reconciled with `find` instead of retried blindly.")
        body = {
            "paymentType": "REFUND",
            "parentPaymentId": a.arg,
            "referenceId": a.reference_id,
        }
        if a.amount is not None:
            body["amount"] = a.amount
        if a.currency:
            body["currency"] = a.currency
        request("POST", f"{API_PREFIX}/payments", body, a.dry_run)
    elif a.command == "capture":
        body = {"amount": a.amount} if a.amount is not None else None
        request("POST", f"{API_PREFIX}/payments/{a.arg}/capture", body, a.dry_run)
    elif a.command == "void":
        request("POST", f"{API_PREFIX}/payments/{a.arg}/void", None, a.dry_run)
    elif a.command == "post":
        request("POST", a.arg, load_body(a.body) if a.body else None, a.dry_run)
    else:
        p.error(f"unknown command {a.command!r}")


if __name__ == "__main__":
    main()
