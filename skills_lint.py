#!/usr/bin/env python3
"""skills-lint — check whether an agent skill will actually get used.

Agent skills are cheap to write and easy to get wrong. The failure mode is silent:
a skill whose description does not match how people phrase requests is never
selected, and nothing anywhere reports it. You just notice, months later, that the
agent keeps doing the task by hand.

This linter scores skills against the things that decide whether they get picked:
a valid name, a description that names the situations it applies to, references
that resolve, and no size or safety disasters.

Zero third-party dependencies. Python 3.8+.

Usage:

    python skills_lint.py ~/.claude/skills
    python skills_lint.py ./my-skill --verbose
    python skills_lint.py ~/.claude/skills --json --min-score 70
    python skills_lint.py --list-rules

Exit codes: 0 clean, 1 findings at or above --fail-level, 2 usage error.

License: MIT
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

VERSION = "1.0.0"

FAIL = "FAIL"
WARN = "WARN"
INFO = "INFO"

SEVERITY_WEIGHT = {FAIL: 12, WARN: 5, INFO: 1}


# --------------------------------------------------------------------------
# Frontmatter
# --------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"\A\ufeff?---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


def split_frontmatter(text: str) -> Tuple[Dict[str, str], str, int]:
    """Return (fields, body, body_start_line)."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text, 1

    raw = match.group(1)
    fields: Dict[str, str] = {}
    current_key: Optional[str] = None
    block_indent: Optional[int] = None

    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        # Continuation of a block scalar (key: |  / key: >).
        if block_indent is not None and indent > block_indent and current_key:
            fields[current_key] = (fields[current_key] + " " + line.strip()).strip()
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        current_key = key
        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            fields[key] = ""
            block_indent = indent
        else:
            fields[key] = _unquote(value)
            block_indent = None

    body_start = text[: match.end()].count("\n") + 1
    return fields, text[match.end():], body_start


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


@dataclass
class Finding:
    level: str
    rule: str
    skill: str
    message: str
    hint: str = ""
    line: int = 0


@dataclass
class SkillReport:
    path: Path
    name: str = ""
    score: int = 100
    findings: List[Finding] = field(default_factory=list)
    description_len: int = 0

    @property
    def failures(self) -> int:
        return sum(1 for f in self.findings if f.level == FAIL)

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.level == WARN)


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

# Telltale wording that a description names the situations it applies to. A
# description without any of this is a statement of what the skill contains,
# which is not what the runtime matches against.
TRIGGER_RE = re.compile(
    r"use\s+(?:this\s+|it\s+)?when"
    r"|use\s+for"
    r"|when(?:ever)?\s+(?:the\s+|a\s+|an\s+)?"
    r"(?:user|users|you|your|claude|codex|copilot|agent|asked|asks|needs?|wants?|working\s+with)"
    r"|is\s+(?:requested|needed|used)"
    r"|trigger(?:ed|s)?\b"
    r"|当用户|当需要|使用时|适用于|需要时|用于|在.{0,6}时"
    r"|时使用|时调用|时触发|适合(?:在|用于)",
    re.IGNORECASE,
)

# A bare word with one of these suffixes is treated as a file reference; anything
# else (a field name like `links.original`, a placeholder) is ignored.
PATH_EXTENSIONS = {
    ".md", ".py", ".sh", ".js", ".mjs", ".ts", ".json", ".yaml", ".yml", ".txt",
    ".csv", ".tsv", ".html", ".css", ".rb", ".go", ".rs", ".java", ".c", ".h",
    ".cpp", ".sql", ".toml", ".ini", ".cfg", ".xml", ".ps1", ".bat",
}

# Names that describe nothing an agent can match on.
VAGUE_NAMES = {"helper", "utils", "tools", "misc", "stuff", "test", "skill", "my-skill", "temp"}

# Patterns that deserve a second look in a skill that a model can invoke on its own.
RISKY_PATTERNS = [
    (re.compile(r"\brm\s+-rf?\s+[/~]"), "recursive delete of an absolute or home path"),
    (re.compile(r"curl\s+[^|]*\|\s*(?:ba)?sh"), "piping a remote script straight into a shell"),
    (re.compile(r"wget\s+[^|]*\|\s*(?:ba)?sh"), "piping a remote script straight into a shell"),
    (re.compile(r"\bchmod\s+777\b"), "world-writable permissions"),
    (re.compile(r":\s*\(\s*\)\s*\{\s*:\|:\s*&\s*\}"), "fork bomb"),
    (re.compile(r"\bgit\s+push\b[^\n]*--force"), "force push"),
]

SECRET_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "OpenAI-style API key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key block"),
]

REFERENCE_RE = re.compile(r"(?:^|[\s(\"'`])((?:scripts|references|assets|templates|examples)/[A-Za-z0-9_./-]+)")
MD_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")

MAX_DESCRIPTION = 1024
MIN_DESCRIPTION = 40
MAX_BODY_LINES = 500


def looks_like_file_ref(ref: str) -> bool:
    """True for things that are meant to be a path, false for field names and placeholders."""
    if "/" in ref or "\\" in ref:
        return True
    return Path(ref).suffix.lower() in PATH_EXTENSIONS


def lint_skill(skill_dir: Path) -> SkillReport:
    report = SkillReport(path=skill_dir)
    skill_file = skill_dir / "SKILL.md"

    if not skill_file.is_file():
        # A bare directory with a differently-cased entry is a common mistake.
        candidates = [p for p in skill_dir.glob("*") if p.name.lower() == "skill.md"]
        if candidates:
            _add(report, FAIL, "SL001", "file is named %r, must be exactly 'SKILL.md'" % candidates[0].name,
                 hint="Case matters on every platform except by accident on Windows.")
        else:
            _add(report, FAIL, "SL001", "no SKILL.md in %s" % skill_dir.name,
                 hint="A skill directory must contain SKILL.md at its root.")
        return report

    text = skill_file.read_text(encoding="utf-8", errors="replace")
    fields, body, body_start = split_frontmatter(text)

    # ---- frontmatter presence -------------------------------------------
    if not fields:
        _add(report, FAIL, "SL002", "no YAML frontmatter",
             hint="The runtime reads name and description from frontmatter; without it the skill is invisible.")

    # ---- name ------------------------------------------------------------
    name = fields.get("name", "").strip()
    report.name = name or skill_dir.name
    if not name:
        _add(report, FAIL, "SL003", "frontmatter has no 'name'")
    else:
        if name != skill_dir.name:
            _add(report, WARN, "SL004", "name %r does not match directory %r" % (name, skill_dir.name),
                 hint="Resolution failures are confusing to debug when these disagree.")
        if not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", name):
            _add(report, WARN, "SL005", "name %r is not lowercase-hyphenated" % name,
                 hint="Some runtimes reject or mangle names containing capitals, spaces or dots.")
        if name.lower() in VAGUE_NAMES:
            _add(report, WARN, "SL006", "name %r says nothing about the capability" % name,
                 hint="The name is a keyword the model may match on. 'helper' matches nothing.")

    # ---- description ------------------------------------------------------
    description = " ".join(
        fields.get(k, "") for k in ("description", "description_en") if fields.get(k)
    ).strip()
    report.description_len = len(description)

    if not description:
        _add(report, FAIL, "SL007", "no description",
             hint="Selection is driven almost entirely by the description. An empty one means never selected.")
    else:
        if len(description) < MIN_DESCRIPTION:
            _add(report, WARN, "SL008", "description is only %d characters" % len(description),
                 hint="Short descriptions rarely contain enough vocabulary to match real requests.")
        if len(description) > MAX_DESCRIPTION:
            _add(report, WARN, "SL009", "description is %d characters, over the %d guideline"
                 % (len(description), MAX_DESCRIPTION),
                 hint="Every skill's description is loaded into context. Long ones crowd out others.")

        lowered = description.lower()
        if not TRIGGER_RE.search(description):
            _add(report, FAIL, "SL010", "description never says when to use the skill",
                 hint="Add an explicit trigger clause: 'Use when the user asks to ...'. "
                      "This is the single highest-impact fix for a skill that never fires.")

        # A description that only restates the name carries no matching signal.
        if name and lowered.strip() in (name.lower(), name.lower() + "."):
            _add(report, FAIL, "SL011", "description just repeats the name")

    # ---- body -------------------------------------------------------------
    body_lines = [line for line in body.splitlines() if line.strip()]
    if len(body_lines) < 5:
        _add(report, WARN, "SL012", "body has only %d non-empty lines" % len(body_lines),
             hint="A skill with no instructions leaves the model guessing once it is selected.")
    if len(body.splitlines()) > MAX_BODY_LINES:
        _add(report, INFO, "SL013", "body is %d lines" % len(body.splitlines()),
             hint="Move detail into references/ so the main file stays scannable.")

    # ---- reference resolution --------------------------------------------
    referenced = set()
    for match in REFERENCE_RE.finditer(body):
        referenced.add(match.group(1).rstrip(".,;:)"))
    for match in MD_LINK_RE.finditer(body):
        target = match.group(1).split("#")[0]
        if not target or target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        if looks_like_file_ref(target):
            referenced.add(target)

    missing = sorted(
        ref for ref in referenced
        if not (skill_dir / ref).exists() and "{{" not in ref and "<" not in ref
    )
    if missing:
        _add(report, FAIL, "SL014", "%d referenced path(s) do not exist: %s"
             % (len(missing), ", ".join(missing[:5])),
             hint="The model will try to read these and fail mid-task.")

    # ---- safety -----------------------------------------------------------
    for pattern, label in RISKY_PATTERNS:
        hit = pattern.search(body)
        if hit:
            line_no = body_start + body[: hit.start()].count("\n")
            _add(report, WARN, "SL015", "risky command: %s" % label, line=line_no,
                 hint="A skill runs with the user's full privileges. Make destructive steps conditional "
                      "or require explicit confirmation.")

    for pattern, label in SECRET_PATTERNS:
        hit = pattern.search(text)
        if hit:
            line_no = text[: hit.start()].count("\n") + 1
            _add(report, FAIL, "SL016", "possible hardcoded secret: %s" % label, line=line_no,
                 hint="Skills get copied, shared and committed. Read secrets from the environment.")

    # ---- script inventory -------------------------------------------------
    scripts = skill_dir / "scripts"
    if scripts.is_dir():
        script_files = [p for p in scripts.rglob("*") if p.is_file()]
        if script_files and not re.search(r"scripts/", body):
            _add(report, INFO, "SL017", "%d file(s) under scripts/ are never mentioned in SKILL.md"
                 % len(script_files),
                 hint="If the model does not know a script exists, it will reimplement it inline.")

    # ---- score ------------------------------------------------------------
    penalty = sum(SEVERITY_WEIGHT.get(f.level, 0) for f in report.findings)
    report.score = max(0, 100 - penalty)
    return report


def _add(report: SkillReport, level: str, rule: str, message: str, hint: str = "", line: int = 0) -> None:
    report.findings.append(Finding(level, rule, report.name, message, hint, line))


RULE_INDEX = {
    "SL001": "SKILL.md missing or misnamed",
    "SL002": "YAML frontmatter missing",
    "SL003": "name field missing",
    "SL004": "name does not match directory",
    "SL005": "name is not lowercase-hyphenated",
    "SL006": "name is too generic to match anything",
    "SL007": "description missing",
    "SL008": "description too short",
    "SL009": "description too long",
    "SL010": "description has no trigger clause",
    "SL011": "description only repeats the name",
    "SL012": "body has almost no content",
    "SL013": "body is long enough to split",
    "SL014": "referenced path does not exist",
    "SL015": "risky shell command",
    "SL016": "possible hardcoded secret",
    "SL017": "scripts exist but are not documented",
}


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def discover(target: Path) -> List[Path]:
    """Accept a single skill directory, or a directory of skill directories."""
    if (target / "SKILL.md").is_file():
        return [target]
    if not target.is_dir():
        return []
    found = sorted(p for p in target.iterdir() if p.is_dir() and not p.name.startswith("."))
    # Only keep directories that look like skills; skip assets and fixtures.
    return [p for p in found if (p / "SKILL.md").is_file() or any(
        c.name.lower() == "skill.md" for c in p.iterdir() if c.is_file()
    )]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

MARKS = {FAIL: "[FAIL]", WARN: "[WARN]", INFO: "[INFO]"}


def bar(score: int, width: int = 20) -> str:
    filled = round(width * score / 100)
    return "#" * filled + "." * (width - filled)


def render(reports: List[SkillReport], verbose: bool) -> str:
    out: List[str] = ["skills-lint %s" % VERSION, "=" * 66]

    for report in sorted(reports, key=lambda r: (r.score, r.name)):
        out.append("%-24s %3d  [%s]" % (report.name[:24], report.score, bar(report.score)))
        for finding in report.findings:
            if not verbose and finding.level == INFO:
                continue
            location = (" line %d" % finding.line) if finding.line else ""
            out.append("   %s %s %s%s" % (MARKS[finding.level], finding.rule, finding.message, location))
            if finding.hint and (verbose or finding.level == FAIL):
                for wrapped in wrap(finding.hint, 58):
                    out.append("          " + wrapped)
        if not report.findings:
            out.append("   [ OK ] no findings")

    total_fail = sum(r.failures for r in reports)
    total_warn = sum(r.warnings for r in reports)
    average = round(sum(r.score for r in reports) / len(reports)) if reports else 0

    out.append("-" * 66)
    out.append("%d skill(s), average score %d, %d failure(s), %d warning(s)"
               % (len(reports), average, total_fail, total_warn))

    worst = [r for r in reports if r.failures or r.warnings]
    if worst:
        out.append("")
        out.append("most impactful fixes first:")
        ranked = sorted(worst, key=lambda r: r.score)[:3]
        for report in ranked:
            top = next((f for f in report.findings if f.level == FAIL), None) or report.findings[0]
            out.append("  %-20s %s: %s" % (report.name, top.rule, top.message))
    return "\n".join(out)


def wrap(text: str, width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skills-lint",
        description="Check whether agent skills will actually be selected and run correctly.",
    )
    parser.add_argument("paths", nargs="+", help="skill directory, or a directory containing skills")
    parser.add_argument("--json", action="store_true", help="machine readable output")
    parser.add_argument("--verbose", "-v", action="store_true", help="include informational findings")
    parser.add_argument("--min-score", type=int, default=None,
                        help="fail if any skill scores below this value")
    parser.add_argument("--fail-level", choices=["fail", "warn", "never"], default="fail",
                        help="which severity makes the run exit non-zero (default: fail)")
    parser.add_argument("--list-rules", action="store_true", help="print the rule index and exit")
    parser.add_argument("--version", action="version", version="skills-lint %s" % VERSION)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    args = build_parser().parse_args(argv)

    if args.list_rules:
        for rule in sorted(RULE_INDEX):
            print("%s  %s" % (rule, RULE_INDEX[rule]))
        return 0

    targets: List[Path] = []
    for raw in args.paths:
        path = Path(raw).expanduser()
        if not path.exists():
            print("No such path: %s" % path, file=sys.stderr)
            return 2
        targets.extend(discover(path))

    if not targets:
        print("No skill directories found. A skill directory contains SKILL.md.", file=sys.stderr)
        return 2

    reports = [lint_skill(path) for path in targets]

    if args.json:
        print(json.dumps(
            {
                "version": VERSION,
                "summary": {
                    "skills": len(reports),
                    "average": round(sum(r.score for r in reports) / len(reports)),
                    "failures": sum(r.failures for r in reports),
                    "warnings": sum(r.warnings for r in reports),
                },
                "skills": [
                    {
                        "name": r.name,
                        "path": str(r.path),
                        "score": r.score,
                        "description_length": r.description_len,
                        "findings": [
                            {
                                "level": f.level,
                                "rule": f.rule,
                                "message": f.message,
                                "hint": f.hint,
                                "line": f.line,
                            }
                            for f in r.findings
                        ],
                    }
                    for r in reports
                ],
            },
            indent=2,
            ensure_ascii=False,
        ))
    else:
        print(render(reports, args.verbose))

    if args.min_score is not None:
        low = [r for r in reports if r.score < args.min_score]
        if low:
            return 1

    if args.fail_level == "never":
        return 0
    if args.fail_level == "warn":
        return 1 if any(r.failures or r.warnings for r in reports) else 0
    return 1 if any(r.failures for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
