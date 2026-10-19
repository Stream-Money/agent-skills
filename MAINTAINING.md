# Maintaining the Stream plugin

Internal notes for the Stream team. Merchants never need this file.

## Where things come from

| Part | Source of truth | How it gets here |
|---|---|---|
| Structure, generic rules (`references/*`, scripts) | Adapted from paytech's MIT-licensed `psp-payments` skill v1.10.0 (`github.com/paytech-saas-official/plugins`) | Copied once and edited. Their attribution stays in `LICENSE`. |
| `openapi/gateway-api.json` | `../stream_docs_scalar` (`specs/` + `content/examples/` + `content/guides/`) | `python3 scripts/build-specs.py` in the docs repo writes both the docs copy and this one. The plugin copy also keeps the upstream per-method examples (wallet tokens, PIX, …), which the docs picker leaves out. |
| `references/regional-methods.md` | `../stream_docs_scalar/content/examples/*.md` | Generated once from the same files the docs use. After changing an example there, regenerate this file or edit it to match. |
| Webhook signing facts and test vector | The docs' Webhook signing page | `scripts/verify_webhook.py selftest` and `tools/check_release.py` guard the vector. |
| Stream values (URLs, contacts, entity) | `skills/stream-payments/wl-config.md` | Edit by hand. |

Stream-specific changes compared with the upstream skill:

- Gateway API 1.0.352 only. No Merchant API, and no `GET /payments/{id}/operations`.
- The signature is pinned to **lowercase hex** everywhere; upstream accepted hex or base64.
- `regional-methods.md` and `reference-data.md` are new. `testing-and-sandbox.md` has India
  UPI amount-driven outcomes and our spec's test cards.
- PHP/Laravel is a fourth covered language (`code-examples-php.md`).
- `psp_call.py` defaults to an India UPI smoke test, adds `execute` (the S2S `PATCH`), and knows
  `app-demo.stream.money` is a sandbox (upstream's guard would have refused it).
- Environment variables are `STREAM_*`.

## Before the first publish

```bash
python3 tools/set_placeholders.py --repo <owner>/<repo> --docs-url https://<docs host>
python3 tools/check_release.py
npx -y @anthropic-ai/claude-code plugin validate .              # marketplace
npx -y @anthropic-ai/claude-code plugin validate plugins/stream-payments # plugin
```

The docs URL must be where `stream_docs_scalar/public/` is served. The skill links to
`/reference/#tag/webhook-signing` and `/reference/#tag/regional-reference-values` under it.

Have legal review `TERMS.md`, `PRIVACY.md` and `SECURITY.md`. They are adapted from the
upstream repository and name Stream Ventures Limited.

## Releasing

1. Bump the version in **five** places: both `plugin.json` files, the marketplace entry in
   `.claude-plugin/marketplace.json`, `SKILL.md` `metadata.version`, and `wl-config.md`.
   `check_release.py` fails if they disagree.
2. If the API version changed, also update `wl-api-version` in `SKILL.md` and the table in
   `wl-config.md`.
3. `python3 tools/check_release.py`
4. Commit, tag `v<version>`, push. Claude Code and Codex users with auto-update get it on their
   next session.
5. **OpenAI plugin directory (ChatGPT + Codex):** run `python3 tools/package_openai.py`, then
   upload `dist/stream-payments-<version>.zip` at platform.openai.com/plugins → *Upload new or existing
   plugin*. This needs an org owner (or the *Apps Management Write* permission) and a verified
   business identity. Every upload is a new version with its own automated checks and review.
   This is a skills-only plugin, so it needs no MCP review cases or demo video.

## Testing the code examples

`code-examples-{java,node,python,php}.md` are only "tested" if someone runs them. When you
change one, rebuild its project from the markdown blocks and run its test suite, against
PostgreSQL in Docker. Never run them against the real API.
