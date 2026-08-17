"""MCP server — exposes the linter as tools over stdio JSON-RPC.

Written against the wire protocol directly rather than an SDK. That is a
deliberate choice: it keeps the package dependency-free, it starts in
milliseconds, and the protocol surface an agent server actually needs is small
enough that a shim would obscure more than it saves.

Handles: initialize · notifications/initialized · tools/list · tools/call · ping.

  skill-lint-mcp            # stdio, for an MCP client to spawn
  echo '<json-rpc>' | skill-lint-mcp
"""
from __future__ import annotations

import json
import sys
from typing import Any

from . import __version__
from .cli import render_text
from .core import Report
from .mcp_rules import lint_tools
from .skill_rules import lint_skill_roots

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "lint_skills",
        "description": (
            "Lint agent SKILL.md files in one or more directories. Reports "
            "missing or untriggerable descriptions, name/directory mismatches, "
            "oversized bodies, broken file references, duplicate skill names, "
            "and pairs of skills too similar for a model to choose between. "
            "Use when auditing a skill library, before publishing a skill, or "
            "when a skill exists but never seems to get invoked."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Directories to search recursively for SKILL.md files.",
                },
                "ignore": {
                    "type": "array",
                    "items": {"type": "string", "pattern": "^SL[0-9]{3}$"},
                    "description": "Rule codes to suppress, e.g. SL014.",
                },
            },
            "required": ["paths"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lint_mcp_tools",
        "description": (
            "Lint an MCP tool schema — pass the JSON from a tools/list response "
            "or a bare array of tool objects. Reports missing descriptions, "
            "destructive tools with no confirmation gate, unconstrained "
            "free-form parameters that widen prompt-injection surface, "
            "duplicate tool names, and the per-request token cost of the whole "
            "tool list. Use when designing or reviewing an MCP server."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "schema": {
                    "type": "string",
                    "description": "The tools/list JSON payload, as a string.",
                },
            },
            "required": ["schema"],
            "additionalProperties": False,
        },
    },
]


def _result(rep: Report) -> dict:
    return {
        "content": [{"type": "text", "text": render_text(rep)}],
        "structuredContent": {
            "scanned": rep.scanned,
            "summary": {"error": len(rep.errors), "warn": len(rep.warns)},
            "findings": [f.to_dict() for f in rep.sorted()],
        },
        "isError": False,
    }


def call_tool(name: str, args: dict) -> dict:
    if name == "lint_skills":
        paths = args.get("paths") or []
        if not isinstance(paths, list) or not paths:
            raise ValueError("paths must be a non-empty array of directories")
        rep = lint_skill_roots([str(p) for p in paths])
        for code in args.get("ignore") or []:
            rep.findings = [f for f in rep.findings if f.code != code]
        return _result(rep)

    if name == "lint_mcp_tools":
        raw = args.get("schema")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("schema must be a non-empty JSON string")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"schema is not valid JSON: {e}")
        return _result(lint_tools(payload))

    raise ValueError(f"unknown tool: {name}")


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "skill-lint", "version": __version__},
        }}
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None                       # notifications get no reply
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            res = call_tool(params.get("name", ""), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": mid, "result": res}
        except Exception as e:
            # Tool failures are results, not protocol errors — the model should
            # see the message and be able to correct its own call.
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": f"error: {e}"}],
                "isError": True}}

    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {
                "code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        reply = handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
