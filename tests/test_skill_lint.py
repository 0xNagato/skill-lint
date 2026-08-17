"""Tests. Run: PYTHONPATH=src python3 -m unittest discover -s tests -v"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from skill_lint.core import parse_frontmatter                     # noqa: E402
from skill_lint.mcp_rules import lint_tools                       # noqa: E402
from skill_lint.server import handle, serve                       # noqa: E402
from skill_lint.skill_rules import lint_skill_file, lint_skill_roots  # noqa: E402


def codes(findings):
    return {f.code for f in findings}


def write_skill(root: str, name: str, text: str) -> str:
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "SKILL.md")
    with open(p, "w") as fh:
        fh.write(text)
    return p


class TestFrontmatter(unittest.TestCase):
    def test_basic(self):
        fm, body, err = parse_frontmatter("---\nname: a\ndescription: b\n---\nBody\n")
        self.assertIsNone(err)
        self.assertEqual(fm["name"], "a")
        self.assertEqual(body.strip(), "Body")

    def test_multiline_plain_scalar(self):
        """The failure that made 7 of 8 real-world 'parse errors' bogus."""
        fm, _b, err = parse_frontmatter(
            "---\nname: a\ndescription: Explains things.\n  Use when the user asks.\n---\nx\n")
        self.assertIsNone(err)
        self.assertIn("Use when the user asks", fm["description"])

    def test_prose_colon_is_not_a_key(self):
        fm, _b, err = parse_frontmatter(
            "---\nname: a\ndescription: Does X\n  Note: this is prose, not a key\n---\nx\n")
        self.assertIsNone(err)
        self.assertNotIn("Note", fm)
        self.assertIn("prose", fm["description"])

    def test_block_list(self):
        fm, _b, err = parse_frontmatter("---\nname: a\ntags:\n  - x\n  - y\n---\nb\n")
        self.assertIsNone(err)
        self.assertEqual(fm["tags"], ["x", "y"])

    def test_missing_frontmatter_is_reported(self):
        _fm, _b, err = parse_frontmatter("# just markdown\n")
        self.assertIsNotNone(err)


class TestSkillRules(unittest.TestCase):
    def test_clean_skill_has_no_findings(self):
        with tempfile.TemporaryDirectory() as t:
            p = write_skill(t, "good-skill",
                            "---\nname: good-skill\ndescription: Converts CSV files "
                            "to Parquet with a schema check. Use when the user asks "
                            "to convert or validate tabular data files.\n---\n"
                            "# Good skill\n\nSteps go here.\n")
            self.assertEqual(codes(lint_skill_file(p)), set())

    def test_missing_name_and_description(self):
        with tempfile.TemporaryDirectory() as t:
            p = write_skill(t, "x", "---\nversion: 1\n---\nbody\n")
            self.assertLessEqual({"SL001", "SL002"}, codes(lint_skill_file(p)))

    def test_no_trigger_clause_is_flagged(self):
        with tempfile.TemporaryDirectory() as t:
            p = write_skill(t, "vague",
                            "---\nname: vague\ndescription: A helpful utility that "
                            "processes many kinds of document really well.\n---\nb\n")
            self.assertIn("SL007", codes(lint_skill_file(p)))

    def test_non_english_trigger_downgraded_to_info(self):
        with tempfile.TemporaryDirectory() as t:
            p = write_skill(t, "espanol",
                            "---\nname: espanol\ndescription: Arquitecto de soluciones "
                            "que diagnostica problemas complejos del proyecto.\n---\nb\n")
            f = [x for x in lint_skill_file(p) if x.code == "SL007"]
            self.assertEqual(f[0].severity, "info")

    def test_name_directory_mismatch(self):
        with tempfile.TemporaryDirectory() as t:
            p = write_skill(t, "dirname",
                            "---\nname: other-name\ndescription: Does a thing. "
                            "Use when the user asks for that thing.\n---\nb\n")
            self.assertIn("SL003", codes(lint_skill_file(p)))

    def test_missing_referenced_file(self):
        with tempfile.TemporaryDirectory() as t:
            p = write_skill(t, "refs",
                            "---\nname: refs\ndescription: Runs a helper. Use when "
                            "the user asks to run the helper.\n---\n"
                            "See [guide](docs/guide.md).\n")
            self.assertIn("SL011", codes(lint_skill_file(p)))

    def test_duplicate_names_and_similar_descriptions(self):
        with tempfile.TemporaryDirectory() as t:
            a = os.path.join(t, "a")
            b = os.path.join(t, "b")
            write_skill(a, "dup", "---\nname: dup\ndescription: Converts CSV to "
                                  "Parquet. Use when converting tabular data.\n---\nx\n")
            write_skill(b, "dup", "---\nname: dup\ndescription: Converts CSV to "
                                  "Parquet. Use when converting tabular data.\n---\nx\n")
            self.assertIn("SL012", codes(lint_skill_roots([t]).findings))

    def test_similar_descriptions_flagged(self):
        with tempfile.TemporaryDirectory() as t:
            d = ("---\nname: {}\ndescription: Generates quarterly revenue reports "
                 "from ledger exports. Use when the user asks for a revenue "
                 "report.\n---\nbody\n")
            write_skill(t, "rev-one", d.format("rev-one"))
            write_skill(t, "rev-two", d.format("rev-two"))
            self.assertIn("SL016", codes(lint_skill_roots([t]).findings))


class TestMcpRules(unittest.TestCase):
    def test_destructive_without_gate(self):
        rep = lint_tools({"tools": [{
            "name": "delete_record",
            "description": "Deletes a record from the database permanently by id.",
            "inputSchema": {"type": "object",
                            "properties": {"id": {"type": "string",
                                                  "description": "record id"}},
                            "required": ["id"], "additionalProperties": False}}]})
        self.assertIn("MC003", codes(rep.findings))

    def test_destructive_with_confirm_is_ok(self):
        rep = lint_tools({"tools": [{
            "name": "delete_record",
            "description": "Deletes a record by id. Irreversible.",
            "inputSchema": {"type": "object", "properties": {
                "id": {"type": "string", "description": "record id"},
                "confirm": {"type": "boolean", "description": "must be true"}},
                "required": ["id", "confirm"], "additionalProperties": False}}]})
        self.assertNotIn("MC003", codes(rep.findings))

    def test_injection_prone_param(self):
        rep = lint_tools([{
            "name": "run_query",
            "description": "Runs a read-only analytics query and returns rows.",
            "inputSchema": {"type": "object", "properties": {
                "query": {"type": "string", "description": "the SQL"}},
                "required": ["query"], "additionalProperties": False}}])
        self.assertIn("MC004", codes(rep.findings))

    def test_documented_server_side_validation_clears_mc004(self):
        """MC004's own fix text offers server-side validation as a remedy, so a
        param that documents a concrete mechanism must not still be flagged."""
        rep = lint_tools([{
            "name": "search",
            "description": "Search files under the server root and return matches.",
            "inputSchema": {"type": "object", "properties": {
                "path": {"type": "string", "description":
                         "Subdirectory relative to the server root. Validated "
                         "server-side: resolved, canonicalised, and refused if "
                         "it escapes the root."}},
                "required": ["path"], "additionalProperties": False}}])
        self.assertNotIn("MC004", codes(rep.findings))

    def test_vague_reassurance_does_not_clear_mc004(self):
        rep = lint_tools([{
            "name": "search",
            "description": "Search files under the server root and return matches.",
            "inputSchema": {"type": "object", "properties": {
                "path": {"type": "string",
                         "description": "A safe, trusted path. Don't worry."}},
                "required": ["path"], "additionalProperties": False}}])
        self.assertIn("MC004", codes(rep.findings))

    def test_missing_descriptions_and_duplicates(self):
        rep = lint_tools([
            {"name": "a", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "a", "description": "x" * 40,
             "inputSchema": {"type": "object", "properties": {}}}])
        self.assertLessEqual({"MC001", "MC009"}, codes(rep.findings))

    def test_empty_schema_reports_cleanly(self):
        self.assertIn("MC000", codes(lint_tools({"tools": []}).findings))


class TestMcpServer(unittest.TestCase):
    def test_initialize(self):
        r = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["result"]["serverInfo"]["name"], "skill-lint")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_notification_gets_no_reply(self):
        self.assertIsNone(handle({"jsonrpc": "2.0",
                                  "method": "notifications/initialized"}))

    def test_tools_list_shape(self):
        r = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in r["result"]["tools"]]
        self.assertEqual(sorted(names), ["lint_mcp_tools", "lint_skills"])
        for t in r["result"]["tools"]:
            self.assertTrue(t["description"])
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_tools_call_roundtrip(self):
        payload = json.dumps({"tools": [{"name": "drop_table",
                                         "description": "Drops a table.",
                                         "inputSchema": {"type": "object",
                                                         "properties": {}}}]})
        r = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "lint_mcp_tools",
                               "arguments": {"schema": payload}}})
        self.assertFalse(r["result"]["isError"])
        self.assertIn("MC003",
                      {f["code"] for f in r["result"]["structuredContent"]["findings"]})

    def test_unknown_tool_is_an_error_result_not_a_crash(self):
        r = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "nope", "arguments": {}}})
        self.assertTrue(r["result"]["isError"])

    def test_unknown_method(self):
        r = handle({"jsonrpc": "2.0", "id": 5, "method": "bogus/method"})
        self.assertEqual(r["error"]["code"], -32601)

    def test_serve_handles_malformed_line(self):
        out = io.StringIO()
        serve(io.StringIO("not json\n"), out)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], -32700)

    def test_own_schema_is_clean(self):
        """Dogfood: the server's own tool list must pass its own linter."""
        from skill_lint.server import TOOLS
        rep = lint_tools(TOOLS, source="skill-lint's own tools")
        self.assertEqual([f.code for f in rep.findings if f.severity == "error"], [])


if __name__ == "__main__":
    unittest.main()
