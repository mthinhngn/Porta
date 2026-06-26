"""Versioned evaluation datasets for deterministic gateway checks."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

PHASE4_DATASET_VERSION = "phase4-v1"

EvaluationCategory = Literal[
    "coding",
    "general",
    "summarization",
    "factual_qa",
    "constrained_format",
    "guardrail_safe",
]

REQUIRED_PHASE4_CATEGORIES: tuple[EvaluationCategory, ...] = (
    "coding",
    "general",
    "summarization",
    "factual_qa",
    "constrained_format",
    "guardrail_safe",
)

_REQUIRED_CATEGORY_SET = set(REQUIRED_PHASE4_CATEGORIES)
_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)\b(?:api[_ -]?key|secret|password|token)\s*[:=]\s*['\"]?[\w.-]{8,}"),
    re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
)


class DatasetValidationError(ValueError):
    """Raised when an evaluation dataset does not match the supported schema."""


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    id: str
    category: EvaluationCategory
    prompt: str
    expected_behavior: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    schema_version: str
    name: str
    cases: tuple[EvaluationCase, ...]

    @property
    def categories(self) -> frozenset[EvaluationCategory]:
        return frozenset(case.category for case in self.cases)


def default_phase4_dataset_path() -> Path:
    return Path(__file__).with_name("fixtures") / "phase4_v1.json"


def load_phase4_v1_dataset(path: str | Path | None = None) -> EvaluationDataset:
    dataset_path = Path(path) if path is not None else default_phase4_dataset_path()
    try:
        raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetValidationError(f"invalid JSON in {dataset_path}") from exc

    if not isinstance(raw, Mapping):
        raise DatasetValidationError("dataset root must be an object")
    return validate_phase4_v1_dataset(cast(Mapping[str, object], raw))


def validate_phase4_v1_dataset(raw: Mapping[str, object]) -> EvaluationDataset:
    schema_version = _required_str(raw, "schema_version", "dataset")
    if schema_version != PHASE4_DATASET_VERSION:
        raise DatasetValidationError(
            f"unsupported dataset schema_version {schema_version!r}; "
            f"expected {PHASE4_DATASET_VERSION!r}"
        )

    name = _required_str(raw, "name", "dataset")
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise DatasetValidationError("dataset.cases must be a non-empty array")

    seen_ids: set[str] = set()
    cases: list[EvaluationCase] = []
    for index, raw_case in enumerate(raw_cases):
        location = f"cases[{index}]"
        if not isinstance(raw_case, Mapping):
            raise DatasetValidationError(f"{location} must be an object")
        case = _parse_case(cast(Mapping[str, object], raw_case), location)
        if case.id in seen_ids:
            raise DatasetValidationError(f"{location}.id duplicates {case.id!r}")
        seen_ids.add(case.id)
        _assert_no_sensitive_text(case.prompt, f"{location}.prompt")
        _assert_no_sensitive_text(case.expected_behavior, f"{location}.expected_behavior")
        cases.append(case)

    dataset = EvaluationDataset(
        schema_version=schema_version,
        name=name,
        cases=tuple(cases),
    )
    missing_categories = _REQUIRED_CATEGORY_SET.difference(dataset.categories)
    if missing_categories:
        missing = ", ".join(sorted(missing_categories))
        raise DatasetValidationError(f"dataset is missing required categories: {missing}")

    return dataset


def _parse_case(raw: Mapping[str, object], location: str) -> EvaluationCase:
    case_id = _required_str(raw, "id", location)
    if not case_id.startswith(f"{PHASE4_DATASET_VERSION}-"):
        raise DatasetValidationError(
            f"{location}.id must start with {PHASE4_DATASET_VERSION!r}"
        )

    raw_category = _required_str(raw, "category", location)
    if raw_category not in _REQUIRED_CATEGORY_SET:
        categories = ", ".join(REQUIRED_PHASE4_CATEGORIES)
        raise DatasetValidationError(f"{location}.category must be one of: {categories}")

    raw_tags = raw.get("tags", [])
    if not isinstance(raw_tags, list):
        raise DatasetValidationError(f"{location}.tags must be an array when present")
    tags = tuple(_parse_tag(tag, f"{location}.tags[{index}]") for index, tag in enumerate(raw_tags))

    return EvaluationCase(
        id=case_id,
        category=raw_category,
        prompt=_required_str(raw, "prompt", location),
        expected_behavior=_required_str(raw, "expected_behavior", location),
        tags=tags,
    )


def _parse_tag(raw: object, location: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise DatasetValidationError(f"{location} must be a non-empty string")
    return raw


def _required_str(raw: Mapping[str, object], field: str, location: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DatasetValidationError(f"{location}.{field} must be a non-empty string")
    return value


def _assert_no_sensitive_text(text: str, location: str) -> None:
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.search(text):
            raise DatasetValidationError(f"{location} contains sensitive-looking content")
