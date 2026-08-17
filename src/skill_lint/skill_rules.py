"""Rules for SKILL.md files.

The rules are opinionated about one thing above all: a skill is only useful if
the model can *decide to use it*. Most broken skills are not broken because the
body is wrong — they are broken because the description never says when to
reach for them, so the router never picks them and nobody notices.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

from .core import Finding, Report, est_tokens, parse_frontmatter

KNOWN_KEYS = {"name", "description", "license", "allowed-tools", "allowed_tools",
              "metadata", "version", "author", "source", "risk", "date_added",
              "disable-model-invocation", "model", "compat", "origin", "tags"}

# Phrasing that signals the description tells the router WHEN to fire.
TRIGGER_HINTS = re.compile(
    r"\b(use (this )?(skill )?(when|for|if|to)|invoke when|triggers? on|"
    r"applies? when|call (this|it) when|whenever|for (tasks|requests|when)|"
    r"use in|reach for|when the user|"
    # non-English equivalents, so multilingual skill libraries are not
    # blanket-flagged by an English-only heuristic
    r"usar cuando|utilizar cuando|cuando el usuario|se usa cuando|"
    r"utiliser (quand|lorsque)|quand l'utilisateur|"
    r"verwenden wenn|wenn der (nutzer|benutzer)|"
    r"usar quando|quando o usu[aá]rio|"
    r"usare quando|quando l'utente)\b", re.I)

# Cheap English detector. If a description is not English we cannot judge its
# trigger phrasing, so SL007 is downgraded rather than asserted.
ENGLISH_MARKERS = re.compile(
    r"\b(the|and|for|with|that|this|from|when|your|are|use|to|of|in|on|it)\b", re.I)

FIRST_PERSON = re.compile(r"\b(I will|I can|I'll|let me|I am an?|as an ai)\b", re.I)
NAME_RE = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SCRIPT_RE = re.compile(r"(?:python3?|bash|sh|node|uv run)\s+([\w./-]+\.(?:py|sh|js|ts))")

MAX_DESC = 1024
MIN_DESC = 40
BODY_TOKEN_WARN = 5000
BODY_TOKEN_ERROR = 15000
STOPWORDS = set("""a an the and or of for to in on with use used using this that
these those when whenever if is are be it its as by from into at your you their
skill skills task tasks user users""".split())


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9+-]{2,}", s.lower())
            if w not in STOPWORDS}


def lint_skill_file(path: str) -> list[Finding]:
    out: list[Finding] = []
    rel = path
    try:
        with open(path, errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        return [Finding("SL000", "error", f"cannot read file: {e}", rel)]

    fm, body, err = parse_frontmatter(text)
    if err:
        return [Finding("SL013", "error", "frontmatter did not parse", rel, err,
                        "Start the file with a --- delimited YAML block "
                        "containing at least name and description.")]

    name = str(fm.get("name") or "").strip()
    desc = str(fm.get("description") or "").strip()
    dirname = os.path.basename(os.path.dirname(os.path.abspath(path)))

    if not name:
        out.append(Finding("SL001", "error", "missing 'name' in frontmatter", rel,
                           fix="Add `name: <kebab-case-name>`."))
    else:
        if not NAME_RE.match(name):
            out.append(Finding("SL004", "warn", "name is not kebab-case", rel, name,
                               "Lowercase letters, digits and hyphens only — "
                               "names are used as slash commands."))
        if len(name) > 64:
            out.append(Finding("SL004", "warn", "name longer than 64 chars", rel, name))
        if dirname and name != dirname:
            out.append(Finding("SL003", "warn",
                               "name does not match its directory", rel,
                               f"name={name!r} dir={dirname!r}",
                               "Discovery falls back to the directory name; a "
                               "mismatch makes the skill hard to invoke."))

    if not desc:
        out.append(Finding("SL002", "error", "missing 'description'", rel,
                           fix="Without a description the model has nothing to "
                               "route on and the skill will effectively never fire."))
    else:
        if len(desc) < MIN_DESC:
            out.append(Finding("SL005", "warn", "description is very short", rel,
                               f"{len(desc)} chars",
                               "Say what it does AND when to use it — short "
                               "descriptions lose the routing coin-flip."))
        if len(desc) > MAX_DESC:
            out.append(Finding("SL006", "warn", "description is very long", rel,
                               f"{len(desc)} chars (~{est_tokens(desc)} tokens)",
                               "Every listed skill's description sits in the "
                               "prompt on every turn. Trim to the trigger."))
        if not TRIGGER_HINTS.search(desc):
            english = len(ENGLISH_MARKERS.findall(desc)) >= max(2, len(desc.split()) // 12)
            out.append(Finding(
                "SL007", "warn" if english else "info",
                "description never says WHEN to use the skill", rel,
                desc[:90] + ("…" if len(desc) > 90 else "")
                + ("" if english else "  [non-English: trigger detection is "
                                      "best-effort, verify by hand]"),
                "Add an explicit trigger clause: \"Use when …\", "
                "\"Triggers on …\". This is the single most common "
                "reason a working skill never gets invoked."))
        if FIRST_PERSON.search(desc):
            out.append(Finding("SL008", "info",
                               "description is written in first person", rel,
                               fix="Describe the skill, not the assistant — "
                                   "\"Generates X when Y\", not \"I will help you\"."))

    unknown = [k for k in fm if k not in KNOWN_KEYS and not k.startswith("_")]
    if unknown:
        out.append(Finding("SL014", "info", "unrecognised frontmatter keys", rel,
                           ", ".join(sorted(unknown)),
                           "Harmless, but check for typos such as "
                           "`allowed-tool` or `descripton`."))

    stripped = body.strip()
    if not stripped:
        out.append(Finding("SL010", "error", "skill has no body", rel,
                           fix="Frontmatter alone gives the model nothing to act on."))
    else:
        tk = est_tokens(stripped)
        if tk > BODY_TOKEN_ERROR:
            out.append(Finding("SL009", "error", "skill body is very large", rel,
                               f"~{tk:,} tokens",
                               "Move detail into reference files the skill points "
                               "to, so it is read on demand instead of every load."))
        elif tk > BODY_TOKEN_WARN:
            out.append(Finding("SL009", "warn", "skill body is large", rel,
                               f"~{tk:,} tokens",
                               "Consider splitting rarely-needed detail into "
                               "separate files."))

    base = os.path.dirname(os.path.abspath(path))
    referenced = set(LINK_RE.findall(body)) | set(SCRIPT_RE.findall(body))
    for ref in sorted(referenced):
        if re.match(r"\A(https?:|mailto:|#)", ref) or os.path.isabs(ref):
            continue
        target = os.path.normpath(os.path.join(base, ref.split("#")[0]))
        if not os.path.exists(target):
            out.append(Finding("SL011", "warn", "references a missing file", rel, ref,
                               "The model will try to read this and fail."))
    return out


def lint_skill_roots(roots: list[str]) -> Report:
    """Lint every SKILL.md under the given roots, plus cross-skill checks."""
    rep = Report()
    files: list[str] = []
    for root in roots:
        root = os.path.expanduser(root)
        if os.path.isfile(root):
            files.append(root)
            continue
        for dirpath, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in
                       (".git", "node_modules", "__pycache__", ".venv")]
            if "SKILL.md" in names:
                files.append(os.path.join(dirpath, "SKILL.md"))

    seen: dict[str, list[str]] = defaultdict(list)
    descs: dict[str, tuple[str, str]] = {}

    for f in sorted(set(files)):
        rep.scanned += 1
        rep.add(*lint_skill_file(f))
        with open(f, errors="replace") as fh:
            fm, _b, err = parse_frontmatter(fh.read())
        if err or not fm:
            continue
        nm = str(fm.get("name") or os.path.basename(os.path.dirname(f)))
        seen[nm].append(f)
        d = str(fm.get("description") or "")
        if d:
            descs[f] = (nm, d)

    for nm, paths in seen.items():
        if len(paths) > 1:
            rep.add(Finding("SL012", "error", "duplicate skill name", paths[0],
                            f"{nm} also at: " + ", ".join(paths[1:][:3]),
                            "Whichever root wins precedence silently shadows the "
                            "others. Rename or remove the duplicates."))

    # Routing ambiguity: two skills whose descriptions are near-identical cannot
    # be told apart by the model, so selection between them is effectively random.
    items = [(p, nm, _words(d)) for p, (nm, d) in descs.items() if len(_words(d)) >= 5]
    for i in range(len(items)):
        pi, ni, wi = items[i]
        for j in range(i + 1, len(items)):
            pj, nj, wj = items[j]
            if ni == nj:
                continue
            inter = len(wi & wj)
            union = len(wi | wj) or 1
            if inter / union >= 0.72:
                rep.add(Finding("SL016", "warn",
                                "two skills have near-identical descriptions", pi,
                                f"{ni} vs {nj} ({inter/union:.0%} overlap)",
                                "The model cannot reliably choose between them. "
                                "Differentiate the trigger conditions."))
    return rep
