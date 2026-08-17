---
name: good-skill
description: Converts CSV exports to Parquet and validates them against a declared
  schema, reporting type mismatches per column. Use when the user asks to convert,
  compact, or validate tabular data files before loading them into a warehouse.
---

# Good skill

Passes every rule. Note what the description does: it says what the skill
produces *and* the conditions under which to reach for it. That second half is
what the router actually matches on, and it is the half most skills omit.

## Steps

1. Read the source file and infer column types.
2. Compare against the declared schema; report mismatches rather than coercing.
3. Write Parquet with the validated schema.

## Notes

Keep bodies focused. Detail that is only occasionally needed belongs in a
separate file this skill points to, so it is read on demand rather than loaded
into context on every turn.
