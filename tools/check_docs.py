#!/usr/bin/env python3
"""Docs checks for CI: every relative Markdown link points at a file or directory that
exists (and, for #anchors into Markdown, at a heading that exists), and every ADR file
is listed in design/adr/README.md. Exit 1 with a list of problems."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SKIP_DIRS = {".git", "node_modules", ".venv", "build", "dist"}


def slug(heading: str) -> str:
    h = re.sub(r"[`*_~]", "", heading.strip().lower())
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    out, seen = set(), {}
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
        if in_code or not line.startswith("#"):
            continue
        s = slug(line.lstrip("#"))
        n = seen.get(s, 0)
        seen[s] = n + 1
        out.add(s if n == 0 else f"{s}-{n}")
    return out


def main() -> int:
    problems = []
    for md in sorted(ROOT.rglob("*.md")):
        if SKIP_DIRS & set(md.relative_to(ROOT).parts):
            continue
        text = md.read_text(encoding="utf-8")
        text = re.sub(r"```.*?```", "", text, flags=re.S)  # links in code blocks are examples
        for target in LINK.findall(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#") and not target[1:]:
                continue  # http:, mailto:, computer: ...
            path_part, _, anchor = target.partition("#")
            dest = md if not path_part else (md.parent / path_part).resolve()
            rel = md.relative_to(ROOT)
            if not dest.exists():
                problems.append(f"{rel}: broken link {target}")
            elif anchor and dest.suffix == ".md" and anchor.lower() not in anchors(dest):
                problems.append(f"{rel}: no heading #{anchor} in {path_part or rel}")
    adr_dir = ROOT / "design" / "adr"
    index = (adr_dir / "README.md").read_text(encoding="utf-8")
    for adr in sorted(adr_dir.glob("ADR-*.md")):
        if f"({adr.name})" not in index:
            problems.append(f"design/adr/README.md does not list {adr.name}")
    for p in problems:
        print(p)
    print(f"{'FAIL' if problems else 'PASS'}: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
