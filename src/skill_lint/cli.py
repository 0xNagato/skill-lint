"""Command line entry point."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .core import Report
from .mcp_rules import lint_schema_file
from .skill_rules import lint_skill_roots

ICON = {"error": "✖", "warn": "▲", "info": "·"}
COLOR = {"error": "\033[31m", "warn": "\033[33m", "info": "\033[90m"}
RESET = "\033[0m"


def _tty() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def render_text(rep: Report, root_hint: str = "") -> str:
    if not rep.findings:
        return f"✓ {rep.scanned} item(s) checked, nothing to report"
    lines, last = [], None
    for f in rep.sorted():
        target = os.path.relpath(f.target, root_hint) if root_hint and os.path.isabs(f.target) else f.target
        if target != last:
            lines.append(f"\n{target}")
            last = target
        c, r = (COLOR[f.severity], RESET) if _tty() else ("", "")
        lines.append(f"  {c}{ICON[f.severity]} {f.code}{r} {f.message}"
                     + (f" — {f.detail}" if f.detail else ""))
        if f.fix:
            lines.append(f"      → {f.fix}")
    n_e, n_w = len(rep.errors), len(rep.warns)
    n_i = len(rep.findings) - n_e - n_w
    lines.append(f"\n{rep.scanned} checked · {n_e} error(s) · {n_w} warning(s) · {n_i} info")
    return "\n".join(lines).lstrip("\n")


def render_github(rep: Report) -> str:
    """GitHub Actions annotations, so this is useful in CI on day one."""
    lvl = {"error": "error", "warn": "warning", "info": "notice"}
    return "\n".join(
        f"::{lvl[f.severity]} file={f.target},title={f.code}::"
        f"{f.message}{(' — ' + f.detail) if f.detail else ''}"
        f"{(' | Fix: ' + f.fix) if f.fix else ''}"
        for f in rep.sorted())


def render_json(rep: Report) -> str:
    return json.dumps({
        "scanned": rep.scanned,
        "summary": {"error": len(rep.errors), "warn": len(rep.warns),
                    "info": len(rep.findings) - len(rep.errors) - len(rep.warns)},
        "findings": [f.to_dict() for f in rep.sorted()],
    }, indent=2)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="skill-lint",
        description="Lint agent SKILL.md files and MCP tool schemas.")
    p.add_argument("paths", nargs="*", default=["."],
                   help="skill directories/files, or a tools/list JSON with --mcp")
    p.add_argument("--mcp", action="store_true",
                   help="treat paths as MCP tool-schema JSON files")
    p.add_argument("--format", choices=["text", "json", "github"], default="text")
    p.add_argument("--fail-on", choices=["error", "warn", "info", "never"],
                   default="error", help="minimum severity that exits non-zero")
    p.add_argument("--select", help="only report these comma-separated codes")
    p.add_argument("--ignore", help="suppress these comma-separated codes")
    a = p.parse_args(argv)

    paths = a.paths or ["."]
    if a.mcp:
        rep = Report()
        for path in paths:
            sub = lint_schema_file(path)
            rep.scanned += sub.scanned
            rep.add(*sub.findings)
    else:
        rep = lint_skill_roots(paths)

    if a.select:
        keep = {c.strip().upper() for c in a.select.split(",")}
        rep.findings = [f for f in rep.findings if f.code in keep]
    if a.ignore:
        drop = {c.strip().upper() for c in a.ignore.split(",")}
        rep.findings = [f for f in rep.findings if f.code not in drop]

    out = {"text": render_text, "json": render_json,
           "github": render_github}[a.format]
    print(out(rep) if a.format != "text"
          else render_text(rep, os.path.abspath(paths[0]) if os.path.isdir(paths[0]) else ""))

    if a.fail_on == "never":
        return 0
    thresh = {"error": 0, "warn": 1, "info": 2}[a.fail_on]
    order = {"error": 0, "warn": 1, "info": 2}
    return 1 if any(order[f.severity] <= thresh for f in rep.findings) else 0


if __name__ == "__main__":
    sys.exit(main())
