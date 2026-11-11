import re
from dataclasses import dataclass
from pathlib import Path

CALC_MODULES = {"risk", "windows", "orbit", "replay"}
SKIPPED_MODULES = {"devtools"}

CALC_RULES: dict[str, re.Pattern[str]] = {
    "fillna": re.compile(r"\.fillna\("),
    "nan_to_num": re.compile(r"nan_to_num\("),
    "or_zero": re.compile(r"\bor 0(\.0)?\b"),
    "default_zero": re.compile(r"default\s*=\s*0(\.0)?\b"),
    "bypass_asof": re.compile(r"evarisk\.store\.models"),
}
ALL_RULES: dict[str, re.Pattern[str]] = {
    "naive_now": re.compile(r"datetime\.now\(\s*\)|\.utcnow\("),
    "comment_address": re.compile(
        r"#.*\b(you|your|we should|let'?s|note that|please|todo|fixme|xxx|"
        r"обрати|обратите|смотри|заметь|пожалуйста)\b",
        re.IGNORECASE,
    ),
}
SILENT_EXCEPT = re.compile(r"except(\s+\w+(\s+as\s+\w+)?)?\s*:\s*\n\s*pass\b")


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    rule: str
    text: str


def _module(path: Path, root: Path) -> str:
    rel = path.relative_to(root).parts
    return rel[0] if len(rel) > 1 else "core"


def find_violations(root: Path) -> list[Violation]:
    found: list[Violation] = []
    for path in sorted(root.rglob("*.py")):
        module = _module(path, root)
        if module in SKIPPED_MODULES:
            continue
        text = path.read_text(encoding="utf-8")
        rules = dict(ALL_RULES)
        if module in CALC_MODULES:
            rules.update(CALC_RULES)
            for m in SILENT_EXCEPT.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                found.append(Violation(path, line, "silent_except", m.group(0).strip()))
        for n, line_text in enumerate(text.splitlines(), start=1):
            for rule, pattern in rules.items():
                if pattern.search(line_text):
                    found.append(Violation(path, n, rule, line_text.strip()))
    return found
