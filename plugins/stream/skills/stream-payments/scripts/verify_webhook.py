#!/usr/bin/env python3
"""Verify (or compute) the Stream webhook signature.

Stream sends a `Signature` header with every webhook: the LOWERCASE HEX
HMAC-SHA256 of the raw JSON body, keyed with the Shop Signing Key
(64 characters, no prefix, no timestamp).

IMPORTANT: always verify against the RAW request body bytes, not a re-serialized
JSON object — any re-ordering or whitespace change breaks the hash.

Usage:
    # verify an inbound webhook (signing key from env STREAM_SIGNING_KEY)
    python3 verify_webhook.py verify body.raw.json "<signature-header-value>"

    # print the signature for a body
    python3 verify_webhook.py sign body.raw.json

    # check this script (and your understanding) against the published test vector
    python3 verify_webhook.py selftest
"""
import hashlib
import hmac
import os
import sys

# The test vector published in references/webhooks.md §4 and on the docs'
# "Webhook signing" page.
TEST_KEY = b"sk_test_51f0c3a9d2e84b7c"
TEST_BODY = (b'{"id":"9e9003d7f3324fc6828481c665d8bab5","paymentType":"DEPOSIT",'
             b'"state":"CANCELLED","amount":2000,"currency":"CLP","errorCode":"1.04",'
             b'"errorMessage":"Cancelled by Timeout"}')
TEST_SIGNATURE = "6438a8077670ef1909ebcc1f9729e1c4c08d56a1ddeb02c928fbb1acab393f7b"


def signature(key: bytes, body: bytes) -> str:
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def valid(key: bytes, body: bytes, provided: str) -> bool:
    # Compare BYTES: compare_digest() raises TypeError on non-ASCII str input,
    # which would turn a garbage header into a crash instead of a rejection.
    expected = signature(key, body).encode()
    return hmac.compare_digest(expected, provided.strip().lower().encode("utf-8", "replace"))


def diagnose(key: bytes, body: bytes, provided: str) -> str:
    """Name the most likely cause of a mismatch, for the common mistakes."""
    p = provided.strip()
    if body.endswith(b"\n") and valid(key, body.rstrip(b"\r\n"), p):
        return ("the file ends with a newline that the original request did not have — "
                "save the body exactly as received (e.g. curl --data-binary, no editor).")
    if len(p) != 64 or any(c not in "0123456789abcdefABCDEF" for c in p):
        return (f"the header value is {len(p)} characters and not plain hex — Stream sends "
                "64 lowercase hex characters. Check you copied the whole `Signature` header.")
    return ("check that you hashed the raw body bytes (not re-serialized JSON) and used the "
            "Signing Key for the right environment (sandbox vs production).")


def selftest() -> None:
    got = signature(TEST_KEY, TEST_BODY)
    if got != TEST_SIGNATURE or not valid(TEST_KEY, TEST_BODY, TEST_SIGNATURE.upper()):
        sys.exit(f"selftest FAILED: computed {got}, expected {TEST_SIGNATURE}")
    if valid(TEST_KEY, TEST_BODY.replace(b"2000", b"2001"), TEST_SIGNATURE):
        sys.exit("selftest FAILED: a tampered body verified")
    print("selftest OK — the published test vector verifies; a tampered body does not.")


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["selftest"]:
        selftest()
        return
    if len(argv) < 2 or argv[0] not in ("sign", "verify"):
        sys.exit(__doc__)
    mode, body_file = argv[0], argv[1]
    if mode == "verify" and len(argv) < 3:
        sys.exit("error: `verify` needs the Signature header value as the third "
                 "argument.\n\n" + __doc__)

    key = os.environ.get("STREAM_SIGNING_KEY")
    if not key:
        sys.exit("error: STREAM_SIGNING_KEY is not set. See wl-config.md.")
    try:
        with open(body_file, "rb") as f:
            body = f.read()
    except OSError as e:
        sys.exit(f"error: cannot read {body_file}: {e}")

    if mode == "sign":
        print(signature(key.encode(), body))
        return

    if valid(key.encode(), body, argv[2]):
        print("VALID")
        return
    print("INVALID — " + diagnose(key.encode(), body, argv[2]))
    sys.exit(1)


if __name__ == "__main__":
    main()
