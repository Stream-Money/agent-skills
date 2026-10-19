# Stream.Money Payments

**Add Stream payments to an application you already have, with Claude Code or
Codex.** The agent reads your project and picks the right flow. It then writes
the integration (backend client, webhook handling, order-state mapping,
idempotency, tests) and verifies it against the Stream sandbox before you touch
production.

## What it can do

- **Take UPI payments in India**: hosted checkout redirect, server-to-server
  (QR / intent, no redirect) or P2P. Includes IMPS / RTGS bank payouts.
- **Regional methods**: South Africa EFT, Kenya and West & Central Africa mobile
  money, Colombia bank payments, Bre-B QR and Bre-B key payouts. Each uses the
  exact request shape Stream expects.
- **Take card payments**: hosted payment page, embedded checkout, or Hosted
  Fields when the card form stays on your page. Apple Pay / Google Pay too.
- **Refund and pay out**, without stranding money on a failed request.
- **Charge again later**: card tokens, recurring and subscription flows.
- **Handle webhooks correctly**: lowercase-hex HMAC verification against the raw
  body, and an inbox that survives retries and out-of-order delivery.
- **Review an integration you already have**: point it at existing code and it
  reports what will break in production.
- **Test against the sandbox**: it makes real sandbox calls while building. UPI
  outcomes are driven by the amount, so the whole flow runs without a phone.

## Supported stacks

Tested examples ship for **Java/Spring**, **Node/TypeScript**,
**Python/FastAPI** and **PHP/Laravel**. Other languages work, because the rules,
the SQL and the state machine are language-agnostic. For those, the agent says
up front that the framework idioms are not verified rather than pretending they
are. Webhook signature verifiers for Go, C# and Ruby are on the public docs'
Webhook signing page.

## 1. Install

**As a plugin (recommended).** This is how updates reach you; a copied folder
never changes on its own.

Claude Code:

```
/plugin marketplace add Stream-Money/agent-skills
/plugin install stream@stream
/reload-plugins
```

Codex:

```
codex plugin marketplace add Stream-Money/agent-skills
codex plugin add stream@stream
```

**Or copy the folder**, if you would rather pin one version by hand. Copy
`skills/stream-payments/` into your project's skills directory:

- **Claude Code:** `.claude/skills/stream-payments/`
- **OpenAI Codex:** `.agents/skills/stream-payments/`

The plugin is called `stream`; the skill inside it is `stream-payments`. Check
that your agent found it before relying on it. In Claude Code, run `/skills`: it
lists `stream-payments` for a copied folder, and `stream:stream-payments` for
the plugin (plugin skills are namespaced by the plugin's name). In Codex, ask it
to list its available skills.

You do not need to tell the agent to read the skill. It activates on any task
that mentions Stream, UPI, payments, refunds, payouts or payment webhooks.

## 2. Keep it up to date

In Claude Code, auto-update is **off by default** for third-party marketplaces.
Turn it on once per machine:

1. run `/plugin`
2. open **Marketplaces** and select `stream`
3. choose **Enable auto-update**

Claude Code then refreshes the marketplace in the background shortly after a
session starts, and the new version loads on the next launch. To update on
demand instead, run `/plugin update stream@stream`. In Codex, refresh the
catalog with `codex plugin marketplace upgrade stream`.

For a whole team, declare the marketplace in the project's
`.claude/settings.json`. Everyone who trusts the repository is then offered it,
with auto-update already on:

```json
{
  "extraKnownMarketplaces": {
    "stream": {
      "source": { "source": "github", "repo": "Stream-Money/agent-skills" },
      "autoUpdate": true
    }
  }
}
```

## 3. Provide sandbox credentials

Your shop's `STREAM_API_KEY` and `STREAM_SIGNING_KEY` come from Stream (back
office or support). Make them available as environment variables, using
**one** of these options:

**A. Export them in the terminal** where you run the agent (and later the app):

```
export STREAM_API_URL=https://app-demo.stream.money
export STREAM_API_KEY=<your shop API key>
export STREAM_SIGNING_KEY=<your shop signing key>
```

**B. Use a local env file.** Copy the skill's `assets/env.example` to a new
`.env` in your project root, put the real values there, add `.env` to
`.gitignore`, and load it (your framework's dotenv, or `source .env`).

Never hardcode or commit credentials. Sandbox and production keys differ, and
production is `https://app.stream.money`.

## 4. Use it

Give the agent a task naming the operations, method and flow, for example:

> Integrate UPI deposits via Stream: India, INR, hosted checkout redirect,
> with webhooks and refunds.

> Add server-to-server UPI deposits (PATCH with the customer's IP) to our
> existing Stream flow.

It analyzes your project and chooses the right flow. It then implements the
backend client, webhook handling, state mapping, idempotency, configuration and
tests, and smoke-tests against the sandbox. It adapts to your language,
framework and existing order model, mapping onto your existing order statuses
rather than adding new ones.

## 5. Webhooks

Stream delivers the final payment result by webhook. In the back office, set
the shop's webhook URL to a public HTTPS URL that reaches your backend's
webhook endpoint. The `Signature` header is the lowercase hex HMAC-SHA256 of the
raw body. The docs' Webhook signing page has a test vector you can check your
endpoint against.

## Notes

- Start on the **sandbox**; move to production only after it works there.
- Payment methods and the server-to-server flow are enabled **per shop**. Ask
  Stream support which ones are live for you.
- The agent maps payment states onto your existing order statuses. If your enum
  cannot represent a state it needs, it asks rather than inventing one.
- A browser signal never means "paid". Only a verified webhook or a status check
  does, and the skill implements it that way. Do not ask it to shortcut this.

## Legal and support

- [LICENSE](https://github.com/Stream-Money/agent-skills/blob/main/LICENSE): MIT
- [SECURITY.md](https://github.com/Stream-Money/agent-skills/blob/main/SECURITY.md)
- [PRIVACY.md](https://github.com/Stream-Money/agent-skills/blob/main/PRIVACY.md)
- [TERMS.md](https://github.com/Stream-Money/agent-skills/blob/main/TERMS.md)
- Integration support: https://github.com/Stream-Money/agent-skills/issues

Stream.Money is a trading name of Stream Ventures Limited.
