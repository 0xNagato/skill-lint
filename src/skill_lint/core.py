"""Shared types and the frontmatter parser.

No third-party dependencies anywhere in this package — a linter that agent
developers install should not drag a dependency tree into their environment,
and a zero-dep MCP server starts fast enough to run on every tool call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

Severity = Literal["error", "warn", "info"]
SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


@dataclass
class Finding:
    code: str
    severity: Severity
    message: str
    target: str                 # file path or tool name
    detail: str = ""
    fix: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    scanned: int = 0

    def add(self, *f: Finding) -> None:
        self.findings.extend(x for x in f if x)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warns(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warn"]

    def sorted(self) -> list[Finding]:
        return sorted(self.findings,
                      key=lambda f: (SEVERITY_ORDER[f.severity], f.target, f.code))


def est_tokens(text: str) -> int:
    """Rough token estimate.

    Deliberately crude — ~4 chars/token is close enough for English prose plus
    code, and being wrong by 15% does not change any decision this tool informs.
    Precision here would mean shipping a tokenizer dependency for no gain.
    """
    return max(1, len(text) // 4)


# --------------------------------------------------------------- frontmatter

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)


def parse_frontmatter(text: str) -> tuple[dict[str, Any] | None, str, str | None]:
    """Return (frontmatter, body, error).

    A deliberately small YAML subset: scalars, quoted scalars, inline `[a, b]`
    lists, and `- item` block lists. Skills only ever use this much, and parsing
    a subset keeps the package dependency-free. Anything outside the subset is
    reported rather than silently dropped.
    """
    m = FM_RE.match(text)
    if not m:
        return None, text, "no YAML frontmatter block (file must start with ---)"

    raw, body = m.group(1), m.group(2)
    data: dict[str, Any] = {}
    key: str | None = None
    for lineno, line in enumerate(raw.splitlines(), start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        indented = line[:1].isspace()

        # Block list item under the current key.
        if indented and stripped.startswith("- ") and key:
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(_scalar(stripped[2:]))
            continue

        # Nested block map under a key that was declared empty.
        if indented and key and data.get(key) == "" and _looks_like_pair(stripped):
            k2, _, v2 = stripped.partition(":")
            data.setdefault(key, {})
            if isinstance(data[key], dict):
                data[key][k2.strip()] = _scalar(v2)
            continue

        # Continuation of a multi-line plain scalar. Very common in
        # descriptions, which are the longest field in every skill file —
        # treating these as syntax errors was this parser's worst failure mode.
        if indented and key and isinstance(data.get(key), str):
            data[key] = (data[key] + " " + stripped).strip()
            continue

        # Opaque flow-style block ({...} / [...]); keep it, do not fail on it.
        if stripped[0] in "{}[]" and key:
            if isinstance(data.get(key), str):
                data[key] = (data[key] + " " + stripped).strip()
            continue

        if not _looks_like_pair(stripped):
            if key and isinstance(data.get(key), str):
                data[key] = (data[key] + " " + stripped).strip()
                continue
            return None, body, (f"line {lineno}: expected 'key: value', "
                                f"got {stripped[:60]!r}")

        k, _, v = line.partition(":")
        key = k.strip()
        v = v.strip()
        if v == "":
            data[key] = ""
        elif v.startswith("[") and v.endswith("]"):
            data[key] = [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
        elif v in ("|", ">", "|-", ">-", "|+", ">+"):
            data[key] = ""          # block scalar; continuation lines append
        else:
            data[key] = _scalar(v)
    return data, body, None


KEY_RE = re.compile(r"\A[A-Za-z_][\w.-]*\s*:(\s|\Z)")


def _looks_like_pair(s: str) -> bool:
    """A real `key: value`, not prose that merely contains a colon.

    'Use when: the user asks' is a continuation line, not a new key.
    """
    return bool(KEY_RE.match(s))


def _scalar(v: str) -> Any:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v
