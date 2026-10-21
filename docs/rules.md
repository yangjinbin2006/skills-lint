# Rules

Why each rule exists, and what to change. Rules are numbered `SL001`–`SL017`;
`skills_lint.py --list-rules` prints the same index without the prose.

---

## How selection actually works

Worth stating plainly, because every rule follows from it.

When a request arrives, the runtime decides whether a skill is relevant using its
**name and description** — not its body. The body is only read after the skill has
already been chosen. So:

- The description is not documentation. It is the query-key.
- A skill whose description does not contain the words people use when asking for
  the task is invisible, no matter how good the instructions are.
- A skill with a great description and a bad body gets selected and then performs
  badly. Both halves matter, but only one of them determines whether you get a
  chance at all.

Everything below is a mechanical proxy for those two facts.

---

## SL001 — SKILL.md missing or misnamed

**Severity: fail**

The file must be at the root of the skill directory and named exactly `SKILL.md`.
The casing trap is real: on Windows and on default macOS filesystems a file called
`skill.md` works fine locally and vanishes the moment the skill is used on Linux.

**Fix:** `git mv skill.md SKILL.md` (and check `git config core.ignorecase`).

---

## SL002 — No YAML frontmatter

**Severity: fail**

Without frontmatter there is no name and no description, so the skill cannot be
matched at all. It is a text file that happens to sit in a directory.

**Fix:** add the block at the very top, starting on line 1:

```yaml
---
name: my-skill
description: Do the thing. Use when the user asks to ...
---
```

---

## SL003 — No `name`

**Severity: fail**

`name` is a matching surface as well as an identifier. Some runtimes fall back to the
directory name; relying on that is fragile.

---

## SL004 — `name` does not match the directory

**Severity: warn**

When these disagree, every later problem is harder to diagnose: the error mentions one
name, the file lives under another, and neither greps cleanly.

---

## SL005 — `name` is not lowercase-hyphenated

**Severity: warn**

`^[a-z0-9]+(-[a-z0-9]+)*$`. Capitals, spaces, dots and underscores are accepted by some
runtimes and rejected or mangled by others. Hyphenated lowercase is the intersection
that works everywhere.

`Data_Helper` fails; `data-helper` passes.

---

## SL006 — `name` is too generic

**Severity: warn**

`helper`, `utils`, `tools`, `misc`, `test`, `skill` are not capabilities. If the name is
the only signal a matcher has in some edge case, it needs to carry meaning.

---

## SL007 — No `description`

**Severity: fail**

The single most damaging omission. It is the primary matching surface; empty means
never selected.

---

## SL008 — `description` under 40 characters

**Severity: warn**

Short descriptions rarely contain enough vocabulary overlap with real requests. "Helps
with data" matches nothing specific. Forty characters is not a target — it is a floor
below which the odds are poor.

---

## SL009 — `description` over 1024 characters

**Severity: warn**

Every skill's name and description is loaded into context up front, in some runtimes for
every turn. A 3000-character description for one skill crowds out the others and costs
tokens continuously for a benefit that is speculative.

Long descriptions usually mean the author is trying to cover every possible phrasing.
Usually the fix is fewer, more concrete situations.

---

## SL010 — `description` never says when to use the skill

**Severity: fail** — the rule that matters most.

This is the difference between a catalogue entry and a trigger.

A description that reads `PDF manipulation toolkit for extracting text and tables,
creating PDFs, merging and splitting documents` is a perfectly accurate summary. It is
also a list of capabilities, written in the author's words. A user asking "can you split
this pdf into pages" has no `split`-adjacent phrasing guarantee to match against.

Add the situation, in the second person or with an explicit clause:

```yaml
description: Manipulate PDF files. Use when the user asks to extract text or
  tables from a PDF, merge or split documents, fill in a form, or generate a
  PDF programmatically.
```

Recognised patterns (bilingual, case-insensitive):

| English | Chinese |
|---|---|
| `use when`, `use for` | `…时使用`, `…时调用`, `…时触发` |
| `when the user`, `when you`, `when claude`, `when the agent` | `当用户…`, `当需要…` |
| `whenever`, `triggered by`, `is requested` | `适用于`, `用于`, `需要时`, `适合用于` |

If your runtime matches on vocabulary alone and a skill works without an explicit clause,
this finding is a false positive — ignore it, and note why in the skill.

---

## SL011 — `description` only repeats the name

**Severity: fail**

`name: pdf` + `description: pdf` adds zero matching signal and zero information. Cheap
to write, easy to catch, common in a first draft.

---

## SL012 — Body has almost no content

**Severity: warn**

Under five non-empty lines means the model gets selected and then has nothing to follow.
It will improvise, which is the behaviour the skill existed to prevent.

---

## SL013 — Body long enough to split

**Severity: info**

Over 500 lines. Move detail into `references/` and link it. The main file is read in full
when the skill activates; a reference file is read only if the model decides it needs it.
That is a real saving, not tidiness for its own sake.

---

## SL014 — Referenced path does not exist

**Severity: fail**

Both markdown links and bare `scripts/…`, `references/…`, `assets/…` paths are checked
against the filesystem. A broken reference fails at the worst moment: mid-task, after the
model has already committed to an approach.

Field names and placeholders are skipped — a bare token needs a path separator or a known
file extension to count as a reference.

---

## SL015 — Risky shell command

**Severity: warn**

Matched patterns: `rm -rf` on an absolute or home path, `curl`/`wget` piped into a shell,
`chmod 777`, fork bombs, `git push --force`.

A skill executes with the user's full privileges, often without a human reading the
command it is about to run. Destructive steps should be conditional or explicitly
confirmed by the instructions — not presented as an unconditional step.

This is a prompt for review, not proof of a problem. `--force` on a personal branch is
fine; the rule cannot tell.

---

## SL016 — Possible hardcoded secret

**Severity: fail**

Patterns for OpenAI-style keys, GitHub tokens, AWS access keys, Slack tokens and PEM
private keys, scanned across the whole file including frontmatter.

Skills get copied between machines, shared in repos, and pasted into issues. A secret in
one is a secret published.

**Fix:** read from the environment, and rotate the key — removing the line does not
un-expose it.

---

## SL017 — Scripts exist but are undocumented

**Severity: info**

Files under `scripts/` that `SKILL.md` never mentions. The model cannot know they exist,
so it reimplements the logic inline — worse, slower, and inconsistent with the script
nobody is calling.

---

## Adding a rule

Rules live in `lint_skill()`. A rule is a call to `_add(report, level, rule_id, message,
hint=..., line=...)`. Add the id to `RULE_INDEX` so `--list-rules` picks it up, and give
it a weight in `SEVERITY_WEIGHT` if the default for its severity does not fit.
