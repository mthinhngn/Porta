"""Deterministic task classification for local fallback ordering."""

from __future__ import annotations

import re
from typing import Literal

TaskKind = Literal["coding", "general"]

_CODE_FENCE = re.compile(r"```")
_STACK_TRACE = re.compile(r"(?im)^\s*(traceback|at\s+\S+\s+\(.+:\d+\)|file\s+\".+\",\s+line\s+\d+)")
_FILE_EXTENSION = re.compile(
    r"(?i)\b[\w.-]+\.(?:py|js|jsx|ts|tsx|java|go|rs|rb|php|cs|cpp|c|h|sql|html|css|json|ya?ml|toml)\b"
)
_SQL_STATEMENT = re.compile(
    r"(?i)\b(?:select\s+.+\s+from|insert\s+into|update\s+\w+\s+set|delete\s+from|"
    r"create\s+(?:table|index)|alter\s+table)\b"
)
_CODING_TERM = re.compile(
    r"(?i)\b(?:code|function|class|debug|implement|refactor|sql|regex|"
    r"exception|stack\s+trace|compiler|database|api|endpoint)\b"
)


def classify_task(text: str) -> TaskKind:
    """Classify without external calls or retaining the supplied text."""

    if (
        _CODE_FENCE.search(text)
        or _STACK_TRACE.search(text)
        or _FILE_EXTENSION.search(text)
        or _SQL_STATEMENT.search(text)
        or _CODING_TERM.search(text)
    ):
        return "coding"
    return "general"
