# Stream.Money plugins for coding agents

A plugin marketplace: add it once, and your agent can install the plugins below
and receive updates as they are released. Works with **Claude Code** and
**Codex**. The payload is identical; each host reads its own manifest.

## Plugins

| Plugin | What it does |
|--------|--------------|
| [`stream`](plugins/stream/README.md) | Integrate Stream payments into an existing application. Covers UPI and other regional methods (India, South Africa, Kenya, West Africa, Colombia), cards, Apple Pay / Google Pay, refunds, payouts, recurring and webhooks. It is implemented in your codebase and verified against the Stream sandbox. |

## Add the marketplace

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

Credentials, the first task to give the agent, and how updates work are on the
plugin's own page: [`plugins/stream/README.md`](plugins/stream/README.md).

## Legal and support

- [LICENSE](LICENSE): MIT
- [SECURITY.md](SECURITY.md): how to report a vulnerability
- [PRIVACY.md](PRIVACY.md): what this plugin does and does not collect
- [TERMS.md](TERMS.md): terms of use
- Integration support: https://github.com/Stream-Money/agent-skills/issues
