#!/usr/bin/env python3
"""Fill in the repository-wide placeholders.

    python3 tools/set_placeholders.py --repo <owner>/<repo> --docs-url https://docs.example.com

<GITHUB_REPO> becomes `owner/repo` (the marketplace path merchants type into
`/plugin marketplace add`), <DOCS_URL> becomes the public API docs base URL
without a trailing slash. Either flag may be given alone. Run
tools/check_release.py afterwards.
"""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEXT = {".md", ".json", ".py", ".example", ""}

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("--repo", help="GitHub owner/repo, e.g. stream-money/plugins")
p.add_argument("--docs-url", help="public docs base URL, e.g. https://docs.stream.money")
a = p.parse_args()
if not (a.repo or a.docs_url):
    p.error("give --repo and/or --docs-url")
if a.repo and not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", a.repo):
    sys.exit(f"--repo must look like owner/repo, got {a.repo!r}")
if a.docs_url and not re.fullmatch(r"https://[^\s<>]+", a.docs_url):
    sys.exit(f"--docs-url must be an https:// URL, got {a.docs_url!r}")

subs = {}
if a.repo:
    subs["<GITHUB_REPO>"] = a.repo
if a.docs_url:
    subs["<DOCS_URL>"] = a.docs_url.rstrip("/")

changed = 0
for path in ROOT.rglob("*"):
    if ".git" in path.parts or "tools" in path.parts or not path.is_file() or path.suffix not in TEXT:
        continue
    text = path.read_text(encoding="utf-8")
    new = text
    for k, v in subs.items():
        new = new.replace(k, v)
    if new != text:
        path.write_text(new, encoding="utf-8")
        changed += 1
        print(f"  updated {path.relative_to(ROOT)}")
print(f"{changed} file(s) updated.")
