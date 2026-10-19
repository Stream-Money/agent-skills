#!/usr/bin/env python3
"""Build the ZIP for the OpenAI plugin directory (ChatGPT + Codex).

    python3 tools/package_openai.py            # → dist/stream-<version>.zip

The ZIP's root is the plugin root (plugins/stream/): .codex-plugin/plugin.json,
skills/, assets/. Refuses to build while tools/check_release.py fails. Upload it
at platform.openai.com/plugins → "Upload new or existing plugin".
"""
import json
import pathlib
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "stream"
# Never ship anything that could hold a secret or local state.
EXCLUDE_NAMES = {".env", ".DS_Store", "__pycache__"}

check = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "check_release.py")])
if check.returncode != 0:
    sys.exit("\nnot packaging: fix the problems above first.")

version = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())["version"]
out = ROOT / "dist" / f"stream-{version}.zip"
out.parent.mkdir(exist_ok=True)
files = sorted(p for p in PLUGIN.rglob("*")
               if p.is_file() and not EXCLUDE_NAMES & set(p.parts) and p.suffix != ".pyc")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in files:
        z.write(p, p.relative_to(PLUGIN).as_posix())
print(f"\nwrote {out.relative_to(ROOT)} — {len(files)} files, {out.stat().st_size:,} bytes")
