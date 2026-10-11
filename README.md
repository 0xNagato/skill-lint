# skill-lint

[![M8ven Score](https://m8ven.ai/badge/mcp/0xnagato-skill-lint-10hdro)](https://m8ven.ai/mcp/0xnagato-skill-lint-10hdro)

Lint agent `SKILL.md` files and MCP tool schemas.

Agent skills and MCP tools rarely fail loudly. They fail by never being chosen —
a description that never says *when* to use it, two tools the model can't tell
apart, a schema so verbose it costs you tokens on every single turn. `skill-lint`
finds those, plus the ones that bite harder: a destructive tool with no
confirmation gate, an unconstrained `command` parameter, a skill pointing at a
file that isn't there.

Zero dependencies. Runs as a CLI or as an MCP server.

```bash
uv tool install git+https://github.com/0xNagato/skill-lint   # or: pipx install git+https://github.com/0xNagato/skill-lint

skill-lint ~/.claude/skills          # lint a skill library
skill-lint --mcp tools.json          # lint an MCP tools/list payload
skill-lint . --format github         # CI annotations
```

## Example

```
$ skill-lint ~/.claude/skills

skills/report-writer/SKILL.md
  ▲ SL007 description never says WHEN to use the skill — Generates polished reports…
      → Add an explicit trigger clause: "Use when …", "Triggers on …". This is the
        single most common reason a working skill never gets invoked.
  ▲ SL011 references a missing file — templates/quarterly.md
      → The model will try to read this and fail.

197 checked · 1 error(s) · 129 warning(s) · 75 info
```

## As an MCP server

```jsonc
// claude_desktop_config.json / .mcp.json
{
  "mcpServers": {
    "skill-lint": { "command": "skill-lint-mcp" }
  }
}
```

Exposes `lint_skills` and `lint_mcp_tools`. The server speaks the MCP stdio
protocol directly — no SDK, so it starts in milliseconds and adds nothing to
your dependency tree.

## Rules

### Skills

| Code | Severity | What it catches |
|---|---|---|
| SL001 / SL002 | error | Missing `name` / `description` |
| SL003 | warn | `name` doesn't match its directory (breaks invocation) |
| SL004 | warn | Name isn't kebab-case, or is over 64 chars |
| SL005 / SL006 | warn | Description too short to route on / long enough to cost real tokens |
| **SL007** | warn | **Description never says when to use the skill** |
| SL008 | info | Description written in first person |
| SL009 | warn/error | Skill body large enough to bloat every load |
| SL010 | error | Frontmatter with no body |
| SL011 | warn | Links to or runs a file that doesn't exist |
| SL012 | error | Two skills share a name — one silently shadows the other |
| SL013 | error | Frontmatter doesn't parse |
| SL014 | info | Unrecognised frontmatter keys (usually typos) |
| SL016 | warn | Two descriptions so similar the model can't choose between them |

SL007 is the one that matters most. On a real 197-skill library it fired 116
times — most skills describe what they *are* and never say when to reach for
them, so the router never picks them and the author assumes the skill is fine.

Trigger detection is English-first. Descriptions that don't look English are
reported at `info` rather than `warn`, since the heuristic can't judge them.

### MCP tools

| Code | Severity | What it catches |
|---|---|---|
| MC001 | error/warn | Tool has no description, or one too short to route on |
| MC002 | warn | Parameter has no description |
| **MC003** | error | **Destructive tool with no confirmation gate** |
| MC004 | warn | Unconstrained free-form `command`/`path`/`query` — injection surface |
| MC005 | info | Tool name isn't snake_case |
| MC006 | warn | Description long enough to matter, paid every request |
| MC007 | warn | Schema has no `required` list |
| MC008 | info | `additionalProperties` not `false` |
| MC009 | error | Duplicate tool name — one is unreachable |
| MC010 | warn | More than ~40 tools; selection accuracy drops |
| MC011 | warn | Whole tool list is expensive per request |

MC003 matches the tool name and its opening sentence, not the whole
description — a destructive verb in later context ("use before publishing")
isn't what the tool does, and matching it turns the rule into noise.

## Options

```
--mcp                   treat paths as MCP tool-schema JSON
--format text|json|github
--fail-on error|warn|info|never     minimum severity that exits non-zero
--select SL007,MC003    only these codes
--ignore SL014          suppress these codes
```

## CI

```yaml
- run: pipx install git+https://github.com/0xNagato/skill-lint
- run: skill-lint .claude/skills --format github --fail-on warn
```

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The test suite includes a dogfood case: this server's own tool schema has to
pass its own linter.

## Licence

MIT
