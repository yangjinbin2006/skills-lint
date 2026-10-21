# skills-lint

A linter for agent skills, focused on one question: will this skill ever be picked?

Skills fail without saying anything. You write one, drop it in a directory, and either the
model selects it at the right moment or it never does. Nothing logs, nothing warns, there
is no error to search for. Weeks later you notice the agent has been doing that task by
hand the whole time.

The usual cause is the description field. The runtime matches a user request against it,
so a description that only lists what the skill contains gives the matcher nothing to
work with. This tool reads a skill the way the runtime does and points at the missing
signal.

Standard library only. Python 3.8+. Works on any directory of `SKILL.md` files, including
Claude Code, Codex, and anything else following the same convention.

```bash
python skills_lint.py ~/.claude/skills
```

```
skills-lint 1.0.0
==================================================================
pdf-tools                 99  [####################]
   [INFO] SL017 8 file(s) under scripts/ are never mentioned in SKILL.md
music-search              88  [##################..]
   [FAIL] SL010 description never says when to use the skill
          Add an explicit trigger clause, for example 'Use when the
          user asks to ...'. This is the single highest-impact fix
          for a skill that never fires.
news-digest              100  [####################]
   [ OK ] no findings
------------------------------------------------------------------
8 skill(s), average score 94, 3 failure(s), 2 warning(s)

most impactful fixes first:
  music-search         SL010: description never says when to use the skill
```

That run was against eight working skills. Three of them never declare when they apply,
which means the descriptions were written with care and still had no trigger clause.

## The case being caught

Same skill, two descriptions:

```yaml
# Never selected. This is a catalogue entry.
description: PDF manipulation toolkit for extracting text and tables,
  creating PDFs, merging and splitting documents.
```

```yaml
# Selected. Names the situations, in the words people type.
description: Manipulate PDF files. Use when the user asks to extract text
  or tables from a PDF, merge or split documents, fill in a form, or
  generate a PDF programmatically.
```

Both are accurate. Only the second one gives a matcher something to match.

## Rules

`--list-rules` prints this index.

| Rule | Severity | Catches |
|---|---|---|
| `SL001` | fail | `SKILL.md` missing, or named `skill.md` / `Skill.md` |
| `SL002` | fail | No YAML frontmatter |
| `SL003` | fail | No `name` |
| `SL004` | warn | `name` does not match the directory name |
| `SL005` | warn | `name` is not lowercase-hyphenated |
| `SL006` | warn | `name` is generic (`helper`, `utils`, `tools`…) |
| `SL007` | fail | No `description` |
| `SL008` | warn | `description` under 40 characters |
| `SL009` | warn | `description` over 1024 characters |
| `SL010` | **fail** | **`description` never says when to use the skill** |
| `SL011` | fail | `description` only repeats the name |
| `SL012` | warn | Body has almost no content |
| `SL013` | info | Body long enough to split into `references/` |
| `SL014` | fail | A referenced path does not exist |
| `SL015` | warn | Risky shell command (`rm -rf /`, `curl \| sh`, force push…) |
| `SL016` | fail | Possible hardcoded secret |
| `SL017` | info | Files under `scripts/` that `SKILL.md` never mentions |

Trigger detection handles English and Chinese: `use when`, `when the user`, `when claude`,
`当用户…时`, `…时使用`, `适用于`, `用于`, and similar forms. Adding a new phrasing is one
line in the pattern table.

## Scoring

The score starts at 100 and subtracts 12 per failure, 5 per warning, 1 per info. Treat it
as a triage number rather than a grade. A score of 100 means nothing mechanical will stop
the skill from being selected. It does not mean the instructions are good, and this tool
makes no attempt to judge that.

## CI

```bash
python skills_lint.py ./skills --min-score 70
python skills_lint.py ./skills --fail-level warn
python skills_lint.py ./skills --json > skills-report.json
```

Exit codes: `0` clean, `1` findings at or above `--fail-level`, `2` usage error.

`--fail-level fail` is the default and blocks only on things that stop selection.
`--fail-level warn` is for repositories that want a clean house.

`--json` gives per-skill findings with rule ids, line numbers and hints, which is enough
for a bot to comment on a pull request:

```bash
python skills_lint.py ./skills --json | jq -r '
  .skills[] | select(.score < 90) |
  "\(.name): \(.score) - " + ([.findings[] | .rule] | join(", "))'
```

## What it does not do

- Judge whether the instructions are any good. No static tool can.
- Run the skill. This is a lint pass.
- Rewrite anything. Every hint is meant for a human edit, and no file is modified.

## False positives

The two rules most likely to misfire:

- **SL010** on a description that triggers through vocabulary alone. Some runtimes match
  on nouns. If yours does and the skill works, ignore the finding.
- **SL014** on placeholders like `[title](some.field)` inside documentation examples. Bare
  words with no path separator and no known extension are skipped, but an unusual
  placeholder can still get through.

## License

MIT
