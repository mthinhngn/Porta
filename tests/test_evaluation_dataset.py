from __future__ import annotations

import json
from dataclasses import replace

import pytest

from llm_gateway.evaluation.dataset import (
    PHASE4_DATASET_VERSION,
    REQUIRED_PHASE4_CATEGORIES,
    DatasetValidationError,
    EvaluationDataset,
    default_phase4_dataset_path,
    load_phase4_v1_dataset,
    validate_phase4_v1_dataset,
)


def _raw_fixture() -> dict[str, object]:
    dataset_path = default_phase4_dataset_path()
    return json.loads(dataset_path.read_text(encoding="utf-8"))


def test_load_phase4_v1_dataset_returns_versioned_cases() -> None:
    dataset = load_phase4_v1_dataset()

    assert isinstance(dataset, EvaluationDataset)
    assert dataset.schema_version == PHASE4_DATASET_VERSION
    assert dataset.name == "Phase 4 synthetic gateway evaluation v1"
    assert len(dataset.cases) == 12
    assert tuple(case.category for case in dataset.cases[::2]) == REQUIRED_PHASE4_CATEGORIES


def test_phase4_fixture_covers_all_required_categories() -> None:
    dataset = load_phase4_v1_dataset()

    assert dataset.categories == frozenset(REQUIRED_PHASE4_CATEGORIES)


def test_phase4_fixture_contains_no_sensitive_content() -> None:
    raw = _raw_fixture()
    fixture_text = json.dumps(raw, sort_keys=True)

    assert "sk-" not in fixture_text
    assert "@" not in fixture_text
    assert "BEGIN PRIVATE KEY" not in fixture_text
    validate_phase4_v1_dataset(raw)


def test_validate_rejects_unsupported_schema_version() -> None:
    raw = _raw_fixture()
    raw["schema_version"] = "phase4-v2"

    with pytest.raises(DatasetValidationError, match="unsupported dataset schema_version"):
        validate_phase4_v1_dataset(raw)


def test_validate_rejects_missing_category_coverage() -> None:
    raw = _raw_fixture()
    raw["cases"] = [
        case
        for case in raw["cases"]
        if isinstance(case, dict) and case.get("category") != "guardrail_safe"
    ]

    with pytest.raises(DatasetValidationError, match="missing required categories"):
        validate_phase4_v1_dataset(raw)


def test_validate_rejects_sensitive_looking_fixture_text() -> None:
    dataset = load_phase4_v1_dataset()
    unsafe_case = replace(dataset.cases[0], prompt="Use api_key = abcdefgh12345678.")
    raw = {
        "schema_version": dataset.schema_version,
        "name": dataset.name,
        "cases": [
            {
                "id": case.id,
                "category": case.category,
                "prompt": case.prompt,
                "expected_behavior": case.expected_behavior,
                "tags": list(case.tags),
            }
            for case in (unsafe_case, *dataset.cases[1:])
        ],
    }

    with pytest.raises(DatasetValidationError, match="sensitive-looking content"):
        validate_phase4_v1_dataset(raw)
