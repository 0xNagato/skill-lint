"""Rules for MCP tool schemas.

An MCP server's tool list is a prompt. Every tool description and every
parameter description is injected into the model's context on every request,
and every under-specified parameter is somewhere a hostile document can steer
the agent. So these rules care about two things: what the schema costs you, and
what it exposes.

Accepts either a tools/list response (`{"tools": [...]}`) or a bare array.
"""
from __future__ import annotations

import json
import re

from .core import Finding, Report, est_tokens

SNAKE = re.compile(r"\A[a-z][a-z0-9_]*\Z")

# Verb stems plus common inflections. Word boundaries alone are not enough:
# '\bdelete\b' matches neither 'delete_record' (underscore is a word char, so
# there is no boundary) nor 'Deletes'. Tool names are almost always snake_case
# and descriptions almost always conjugate, so both cases matter.
DESTRUCTIVE = re.compile(
    r"(?:\b|_)(delete|destroy|drop|remove|purge|wipe|truncate|revoke|kill|"
    r"terminate|send|post|publish|deploy|transfer|pay|charge|refund|merge|"
    r"force|overwrite|reset|shutdown|uninstall)(?:e?[sd]|ing)?(?:\b|_)", re.I)


def _wordify(name: str) -> str:
    """delete_record / deleteRecord -> 'delete record', so verb matching works."""
    s = re.sub(r"[_\-.]+", " ", name)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)


def _lede(desc: str) -> str:
    """The opening sentence — where a tool states what it actually does."""
    m = re.search(r"[.!?](\s|\Z)", desc)
    return desc[:m.start() + 1] if m else desc[:160]

CONFIRM_PARAM = re.compile(r"(confirm|dry.?run|force|yes|approve|acknowledge)", re.I)
# Only text that *instructs the model to check first* counts as a gate.
# "Irreversible" and "permanent" describe the danger without doing anything
# about it — a tool that says it is irreversible and still takes no confirmation
# is exactly the case this rule exists to catch.
CONFIRM_TEXT = re.compile(
    r"(requires? (explicit )?(confirmation|approval)|ask (the )?user( first)?|"
    r"confirm (with|before)|check with the user|must be confirmed|"
    r"only after confirmation)", re.I)

# Params whose free-form value becomes an instruction, a path or a target.
INJECTION_PRONE = re.compile(
    r"\A(command|cmd|script|shell|exec|eval|code|query|sql|path|file|filepath|"
    r"file_path|dir|directory|url|uri|endpoint|host|target|prompt|template)\Z", re.I)

# A schema constraint is not the only valid mitigation. Some values — a regex,
# a path — are inherently free-form, and the real defence is server-side
# validation. This rule's own guidance says so, so the check has to honour it:
# a parameter that documents concrete validation is mitigated, not unconstrained.
# Deliberately demanding: vague reassurance like "safe" or "trusted" does not
# count, only a stated mechanism.
VALIDATION_DOCUMENTED = re.compile(
    r"(validated server[- ]side|server[- ]side validation|"
    r"(resolved|canonicalis|canonicaliz|normalis|normaliz)\w*\b[^.]{0,80}\b"
    r"(refus|reject|denied|confined|restricted)|"
    r"(refus|reject|denied|confined|restricted)\w*\b[^.]{0,80}\b"
    r"(root|allowlist|allow[- ]list|whitelist|sandbox|workspace)|"
    r"confined to|restricted to the|must resolve (inside|within)|"
    r"validated before|checked against an? (allowlist|allow[- ]list))", re.I)

TOOL_COUNT_WARN = 40
DESC_TOKEN_WARN = 250
TOTAL_TOKEN_WARN = 8000


def _tools(payload) -> list[dict]:
    if isinstance(payload, dict):
        for key in ("tools", "result"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict) and isinstance(v.get("tools"), list):
                return v["tools"]
        return []
    return payload if isinstance(payload, list) else []


def _schema(tool: dict) -> dict:
    for k in ("inputSchema", "input_schema", "parameters", "schema"):
        v = tool.get(k)
        if isinstance(v, dict):
            return v
    return {}


def lint_tools(payload, source: str = "<schema>") -> Report:
    rep = Report()
    tools = _tools(payload)
    rep.scanned = len(tools)
    if not tools:
        rep.add(Finding("MC000", "error", "no tools found in schema", source,
                        fix="Pass a tools/list response or an array of tool objects."))
        return rep

    total_tokens = 0
    names: dict[str, int] = {}

    for tool in tools:
        name = str(tool.get("name") or "<unnamed>")
        names[name] = names.get(name, 0) + 1
        desc = str(tool.get("description") or "").strip()
        schema = _schema(tool)
        props = schema.get("properties") or {}
        required = schema.get("required")

        total_tokens += est_tokens(name + desc + json.dumps(schema))

        if not SNAKE.match(name):
            rep.add(Finding("MC005", "info", "tool name is not snake_case", name,
                            fix="Most servers use snake_case; mixed conventions "
                                "in one agent's tool list read as sloppy."))
        if not desc:
            rep.add(Finding("MC001", "error", "tool has no description", name,
                            fix="The model cannot choose a tool it cannot read. "
                                "This tool will effectively never be called."))
        elif len(desc) < 25:
            rep.add(Finding("MC001", "warn", "tool description is very short", name,
                            desc, "Say what it does and when to use it."))
        elif est_tokens(desc) > DESC_TOKEN_WARN:
            rep.add(Finding("MC006", "warn", "tool description is very long", name,
                            f"~{est_tokens(desc)} tokens",
                            "This cost is paid on every request, not just when "
                            "the tool is used."))

        if not isinstance(props, dict) or (props and required is None):
            rep.add(Finding("MC007", "warn", "schema has no 'required' list", name,
                            fix="Without it every parameter is optional and the "
                                "model will omit ones you depend on."))
        if props and schema.get("additionalProperties") is not False:
            rep.add(Finding("MC008", "info",
                            "schema allows additionalProperties", name,
                            fix="Set additionalProperties: false so malformed "
                                "calls fail loudly instead of silently."))

        for pname, pschema in (props.items() if isinstance(props, dict) else []):
            pschema = pschema if isinstance(pschema, dict) else {}
            if not str(pschema.get("description") or "").strip():
                rep.add(Finding("MC002", "warn",
                                f"parameter '{pname}' has no description", name,
                                fix="Undescribed parameters get guessed at."))
            pdesc = str(pschema.get("description") or "")
            if (pschema.get("type") == "string"
                    and INJECTION_PRONE.match(pname)
                    and not any(k in pschema for k in ("enum", "pattern", "format"))
                    and not VALIDATION_DOCUMENTED.search(pdesc)):
                rep.add(Finding("MC004", "warn",
                                f"unconstrained free-form '{pname}'", name,
                                "type=string, no enum/pattern/format, and no "
                                "server-side validation described",
                                "This is the surface a malicious document steers. "
                                "Constrain it in the schema, or validate "
                                "server-side and state the mechanism in the "
                                "parameter description."))

        # Match the name and the opening sentence only. A destructive verb
        # later in the description is usually context ("use before publishing"),
        # not what the tool does — matching the whole description turns this
        # rule into noise, which is how good rules get switched off.
        if DESTRUCTIVE.search(_wordify(name)) or DESTRUCTIVE.search(_lede(desc)):
            has_gate = any(CONFIRM_PARAM.search(p) for p in props) if props else False
            if not has_gate and not CONFIRM_TEXT.search(desc):
                rep.add(Finding("MC003", "error",
                                "destructive tool with no confirmation gate", name,
                                "no confirm/dry_run parameter and no warning in "
                                "the description",
                                "Add a `confirm: boolean` (or `dry_run`) parameter, "
                                "or state the consequence in the description so the "
                                "model asks first. An agent will call this "
                                "unprompted otherwise."))

    for n, c in names.items():
        if c > 1:
            rep.add(Finding("MC009", "error", "duplicate tool name", n,
                            f"declared {c} times",
                            "Dispatch is by name; one of these is unreachable."))

    if len(tools) > TOOL_COUNT_WARN:
        rep.add(Finding("MC010", "warn", "large tool surface", source,
                        f"{len(tools)} tools",
                        "Past roughly 40 tools, selection accuracy drops. "
                        "Split the server or gate tools behind a mode."))
    if total_tokens > TOTAL_TOKEN_WARN:
        rep.add(Finding("MC011", "warn", "tool schema is expensive", source,
                        f"~{total_tokens:,} tokens per request",
                        "Paid on every single turn. Trim descriptions or reduce "
                        "the tool count."))
    return rep


def lint_schema_file(path: str) -> Report:
    try:
        with open(path) as f:
            payload = json.load(f)
    except Exception as e:
        r = Report()
        r.add(Finding("MC000", "error", f"cannot parse JSON: {e}", path))
        return r
    return lint_tools(payload, source=path)
