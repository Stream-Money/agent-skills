# Stream configuration

This file holds the values that are specific to Stream: name, endpoints,
credential configuration, supported payment methods and contacts.
Everything under `references/` refers to these values through `$STREAM_*`
environment placeholders, so this is the one file to read when you need a
concrete URL or the name of an environment variable.

## Identity

| Key | Value |
|-----|-------|
| PSP name | Stream (Stream.Money) |
| Skill version | 1.0.0 |
| Supported Gateway API version | 1.0.352 (OpenAPI 3.1.1, bundled in `openapi/gateway-api.json`) |

## Endpoints

| Purpose | URL |
|---------|-----|
| Production API base (`STREAM_API_URL`) | `https://app.stream.money` |
| Sandbox API base (`STREAM_API_URL`) | `https://app-demo.stream.money` |
| Gateway API base path | `/api/v1` |
| Public API reference (interactive, with per-method examples) | `https://docs.stream.money/reference/` |
| Webhook signing guide (code in 7 languages, test vector) | `https://docs.stream.money/reference/#tag/webhook-signing` |
| Regional reference values (bank codes, operators, document types) | `https://docs.stream.money/reference/#tag/regional-reference-values` |

This plugin covers the **Gateway API** only.

## Authentication configuration

| Credential | Env var | Used for |
|------------|---------|----------|
| Shop API Key | `STREAM_API_KEY` | `Authorization: Bearer <key>` on every API call |
| Shop Signing Key | `STREAM_SIGNING_KEY` | HMAC-SHA256 verification of **inbound webhooks** only. The `Signature` header is the **lowercase hex** digest of the raw body (see `references/authentication.md` §3). |

Sandbox and production keys are **different**. Keys are issued per shop in the
Stream back office or by Stream support.

## Support, legal and distribution

These feed the public-facing documents (LICENSE, SECURITY.md, PRIVACY.md,
TERMS.md, the plugin's README). A value still written as `<...>` is unresolved,
and `tools/check_release.py` refuses to call the repository publishable until it
is filled in.

| Key | Value |
|-----|-------|
| Copyright holder (legal entity) | Stream Ventures Limited (company no. 13560798, England and Wales) |
| Plugin marketplace repository | `Stream-Money/agent-skills`: where merchants add the marketplace from |
| Integration support | `https://github.com/Stream-Money/agent-skills/issues` |
| Security contact | `https://github.com/Stream-Money/agent-skills/security/advisories/new` |

## Supported payment methods

The set of payment methods is configured **per shop / terminal** by Stream.
The spec's `PaymentMethod` enum lists every method the platform knows. That
list is not what a given shop can use.

Methods with first-class guidance in this plugin:

| Region | Methods | Reference |
|--------|---------|-----------|
| India | `UPI` (redirect, server-to-server, P2P), `IMPS` / `RTGS` withdrawals | `references/regional-methods.md` §2 |
| South Africa | `NETBANKING` + `bankTransferMethod: EFT` | §3 |
| Kenya, West & Central Africa | `MOBILE_MONEY` | §3 |
| Colombia | `NETBANKING` (bank payment, Bre-B QR, bank and Bre-B key withdrawals) | §4 |
| Global | `BASIC_CARD`, `APPLEPAY`, `GOOGLEPAY` | `references/deposit-and-withdrawal.md`, `references/hosted-fields-and-wallets.md` |

Confirm the enabled list for the merchant's shop with Stream support, or with
one sandbox call, before building around a method.

## Stream-specific notes

- The server-to-server flow ("skip redirection to checkout page",
  `PATCH /api/v1/payments/{id}`) must be enabled for the shop by Stream support
  (see `references/deposit-and-withdrawal.md`, `references/regional-methods.md` §1).
- If the redirect is never opened and the `PATCH` is never made, the payment is
  closed automatically, by default 20 minutes after creation.
- India UPI on the sandbox: the amount decides the outcome
  (`references/testing-and-sandbox.md` §2).
- Hosted Fields SDK script host and checkout page domain are environment-specific.
  Take them from Stream's onboarding materials.
