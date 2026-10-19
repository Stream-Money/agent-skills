#!/usr/bin/env python3
"""Pre-release check for the Stream plugin marketplace.

    python3 tools/check_release.py

Exits non-zero when the repository is not publishable: unresolved placeholders,
manifests that disagree on name or version, a skill whose frontmatter breaks the
Agent Skills limits, files named in SKILL.md's file map that do not exist, a
bundled spec whose version differs from the one the skill claims, leftover
upstream branding, or a webhook verifier that fails the published test vector.
"""
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "stream-payments"
SKILL = PLUGIN / "skills" / "stream-payments"

PLACEHOLDER = re.compile(r"<(GITHUB_REPO|DOCS_URL)>")
TEXT = {".md", ".json", ".py", ".example", ""}

problems, notes = [], []


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — report any parse failure
        problems.append(f"{path.relative_to(ROOT)}: invalid JSON ({e})")
        return {}


# 1. placeholders that must be filled before publishing
hits = {}
for path in ROOT.rglob("*"):
    if ".git" in path.parts or "tools" in path.parts or not path.is_file():
        continue
    if path.suffix not in TEXT:
        continue
    for m in PLACEHOLDER.finditer(path.read_text(encoding="utf-8", errors="replace")):
        hits.setdefault(m.group(0), set()).add(str(path.relative_to(ROOT)))
for token, files in sorted(hits.items()):
    problems.append(f"unresolved {token} in {len(files)} file(s): {', '.join(sorted(files))} "
                    f"— run tools/set_placeholders.py")

# 2. manifests agree on name and version
claude_mkt = load(ROOT / ".claude-plugin" / "marketplace.json")
codex_mkt = load(ROOT / ".agents" / "plugins" / "marketplace.json")
claude_plugin = load(PLUGIN / ".claude-plugin" / "plugin.json")
codex_plugin = load(PLUGIN / ".codex-plugin" / "plugin.json")

skill_md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
fm = re.match(r"\A---\n(.*?)\n---\n", skill_md, re.S)
front = fm.group(1) if fm else ""
skill_version = (re.search(r"^\s+version:\s*(\S+)", front, re.M) or [None, None])[1]
api_version = (re.search(r'wl-api-version:\s*"?([\d.]+)', front) or [None, None])[1]
wl = (SKILL / "wl-config.md").read_text(encoding="utf-8")
wl_version = (re.search(r"\| Skill version \| ([\d.]+) \|", wl) or [None, None])[1]

versions = {
    ".claude-plugin/marketplace.json": next((p.get("version") for p in claude_mkt.get("plugins", [])), None),
    "plugins/stream-payments/.claude-plugin/plugin.json": claude_plugin.get("version"),
    "plugins/stream-payments/.codex-plugin/plugin.json": codex_plugin.get("version"),
    "SKILL.md metadata.version": skill_version,
    "wl-config.md Skill version": wl_version,
}
if len(set(versions.values())) != 1:
    problems.append("versions disagree: " + ", ".join(f"{k}={v}" for k, v in versions.items()))
else:
    notes.append(f"version {skill_version} everywhere")

names = {claude_plugin.get("name"), codex_plugin.get("name"), PLUGIN.name,
         *(p.get("name") for p in claude_mkt.get("plugins", [])),
         *(p.get("name") for p in codex_mkt.get("plugins", []))}
if names != {"stream-payments"}:
    problems.append(f"plugin names disagree: {sorted(map(str, names))}")

# 3. Agent Skills frontmatter limits
name = (re.search(r"^name:\s*(\S+)", front, re.M) or [None, None])[1]
desc = re.search(r"^description:\s*>?\s*\n?(.*?)(?=^\S)", front, re.S | re.M)
desc_text = " ".join(desc.group(1).split()) if desc else ""
if name != SKILL.name:
    problems.append(f"SKILL.md name {name!r} != folder {SKILL.name!r}")
if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name or "") or len(name or "") > 64:
    problems.append(f"SKILL.md name {name!r} is not a valid skill name")
if not desc_text or len(desc_text) > 1024:
    problems.append(f"SKILL.md description is {len(desc_text)} chars (must be 1–1024)")
else:
    notes.append(f"skill description {len(desc_text)}/1024 chars")

# 4. every file in SKILL.md's file map exists
fmap = re.search(r"## File map\n\n```\n(.*?)```", skill_md, re.S)
section = None
for line in (fmap.group(1) if fmap else "").splitlines():
    m = re.match(r"^(\S+/)\s*$|^(\S+/)\s{2,}", line)
    if m:
        section = m.group(1) or m.group(2)
        continue
    m = re.match(r"^(\s*)([\w.\-]+\.\w+)", line)
    if m:
        rel = (section if m.group(1) else "") + m.group(2)
        if not (SKILL / rel).exists():
            problems.append(f"SKILL.md file map names {rel}, which does not exist")
    elif line and not line.startswith(" "):
        section = None
for ref in sorted(set(re.findall(r"`references/([\w.\-]+\.md)`", skill_md))):
    if not (SKILL / "references" / ref).exists():
        problems.append(f"SKILL.md mentions references/{ref}, which does not exist")

# 5. bundled spec matches the version the skill claims
spec = load(SKILL / "openapi" / "gateway-api.json")
if spec.get("info", {}).get("version") != api_version:
    problems.append(f"openapi/gateway-api.json is {spec.get('info', {}).get('version')}, "
                    f"SKILL.md claims {api_version}")
if any("/operations" in p for p in spec.get("paths", {})):
    problems.append("openapi/gateway-api.json still contains a /operations path")
if (SKILL / "openapi" / "merchant-api.json").exists():
    problems.append("openapi/merchant-api.json is bundled — this plugin is Gateway API only")

# 6. no upstream branding outside the attribution in LICENSE and MAINTAINING.md
ATTRIBUTION = {"LICENSE", "MAINTAINING.md"}
for path in ROOT.rglob("*"):
    if ".git" in path.parts or "tools" in path.parts or not path.is_file() or path.name in ATTRIBUTION:
        continue
    if path.suffix not in TEXT:
        continue
    if re.search(r"pay\.tech|paytech|engine(-sandbox)?\.pay", path.read_text(encoding="utf-8", errors="replace"), re.I):
        problems.append(f"upstream branding left in {path.relative_to(ROOT)}")

# 7. OpenAI plugin-directory listing limits (developers.openai.com/plugins/deploy/submission)
iface = codex_plugin.get("interface", {})
LIMITS = {"displayName": 30, "shortDescription": 30, "longDescription": 4000, "developerName": 80}
for key, limit in LIMITS.items():
    if not iface.get(key):
        problems.append(f"codex interface.{key} is required")
    elif len(iface[key]) > limit:
        problems.append(f"codex interface.{key} is {len(iface[key])} chars (max {limit})")
if len(codex_plugin.get("description", "")) > 4000:
    problems.append("codex description exceeds 4000 chars")
prompts = iface.get("defaultPrompt", [])
if len(prompts) > 3 or any(len(x) > 128 for x in prompts):
    problems.append("codex interface.defaultPrompt: at most 3 prompts of 128 chars each")
for key in ("websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL"):
    url = iface.get(key, "")
    if not url.startswith("https://") and not PLACEHOLDER.search(url):
        problems.append(f"codex interface.{key} must be an https URL")
for key in ("composerIcon", "logo"):
    rel = iface.get(key, "")
    img = PLUGIN / rel
    if not rel.startswith("./") or not img.is_file():
        problems.append(f"codex interface.{key} must be a ./relative path to an existing file")
    elif img.suffix == ".png":
        import struct
        w, h = struct.unpack(">II", img.read_bytes()[16:24])
        if w != h or w < 48 or w > 4096 or img.stat().st_size > 5 * 1024 * 1024:
            problems.append(f"{key} must be square, 48–4096 px, ≤ 5 MiB (is {w}x{h})")
notes.append("codex listing fields within OpenAI limits" if not any("codex" in x for x in problems) else "")

# 8. the webhook verifier still passes the published test vector
run = subprocess.run([sys.executable, "-I", str(SKILL / "scripts" / "verify_webhook.py"), "selftest"],
                     capture_output=True, text=True)
if run.returncode != 0:
    problems.append("verify_webhook.py selftest failed: " + (run.stdout + run.stderr).strip())
else:
    notes.append("verify_webhook.py selftest OK")

for n in filter(None, notes):
    print(f"  ok  {n}")
if problems:
    print("\nNOT PUBLISHABLE:")
    for p in problems:
        print(f"  - {p}")
    sys.exit(1)
print("\nPublishable.")
