from collections.abc import Callable, Iterator
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import Connection, Table, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase

from llm_gateway.persistence import (
    Base,
    GatewayRequest,
    Model,
    Provider,
    ProviderAttempt,
    UsageRecord,
)

EXPECTED_TABLES = {
    "audit_metadata",
    "gateway_requests",
    "models",
    "provider_attempts",
    "providers",
    "usage_records",
}


def _table(model: type[DeclarativeBase]) -> Table:
    return cast(Table, model.__table__)


def test_sqlalchemy_metadata_contains_phase_zero_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_sqlalchemy_metadata_uses_naming_convention() -> None:
    convention = Base.metadata.naming_convention

    assert convention is not None
    assert convention["pk"] == "pk_%(table_name)s"
    assert convention["fk"] == ("fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s")


def test_sqlalchemy_metadata_declares_composite_integrity_constraints() -> None:
    attempt_constraints = {constraint.name for constraint in _table(ProviderAttempt).constraints}
    usage_constraints = {constraint.name for constraint in _table(UsageRecord).constraints}

    assert "fk_provider_attempts_model_provider" in attempt_constraints
    assert "uq_provider_attempts_id_request_id" in attempt_constraints
    assert "fk_usage_records_attempt_request" in usage_constraints
    assert "ck_usage_records_total_tokens_sum" in usage_constraints


def test_privacy_sensitive_columns_have_classification_comments() -> None:
    expected_columns = [
        _table(Provider).c.secret_ref,
        _table(Provider).c.settings,
        _table(GatewayRequest).c.request_payload_redacted,
        _table(GatewayRequest).c.error_message,
        _table(ProviderAttempt).c.upstream_request_id,
        _table(ProviderAttempt).c.error_message,
    ]

    assert all(column.comment for column in expected_columns)


@pytest.fixture
def sqlite_connection() -> Iterator[Connection]:
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(connection)
        yield connection


def _insert_provider(connection: Connection, name: str) -> object:
    provider_id = uuid4()
    connection.execute(
        _table(Provider).insert(),
        {"id": provider_id, "name": name, "adapter": "test"},
    )
    return provider_id


def _insert_request(connection: Connection, correlation_id: str) -> object:
    request_id = uuid4()
    connection.execute(
        _table(GatewayRequest).insert(),
        {
            "id": request_id,
            "correlation_id": correlation_id,
            "requested_model": "gateway-model",
        },
    )
    return request_id


def test_sqlite_rejects_attempt_with_provider_model_mismatch(
    sqlite_connection: Connection,
) -> None:
    model_provider_id = _insert_provider(sqlite_connection, "model-provider")
    other_provider_id = _insert_provider(sqlite_connection, "other-provider")
    model_id = uuid4()
    request_id = _insert_request(sqlite_connection, "correlation-1")
    sqlite_connection.execute(
        _table(Model).insert(),
        {
            "id": model_id,
            "provider_id": model_provider_id,
            "gateway_name": "gateway-model",
            "upstream_name": "upstream-model",
        },
    )

    with pytest.raises(IntegrityError):
        sqlite_connection.execute(
            _table(ProviderAttempt).insert(),
            {
                "id": uuid4(),
                "gateway_request_id": request_id,
                "provider_id": other_provider_id,
                "model_id": model_id,
                "attempt_number": 1,
            },
        )


def test_sqlite_rejects_usage_linked_to_attempt_from_another_request(
    sqlite_connection: Connection,
) -> None:
    provider_id = _insert_provider(sqlite_connection, "provider")
    model_id = uuid4()
    first_request_id = _insert_request(sqlite_connection, "correlation-1")
    second_request_id = _insert_request(sqlite_connection, "correlation-2")
    attempt_id = uuid4()
    sqlite_connection.execute(
        _table(Model).insert(),
        {
            "id": model_id,
            "provider_id": provider_id,
            "gateway_name": "gateway-model",
            "upstream_name": "upstream-model",
        },
    )
    sqlite_connection.execute(
        _table(ProviderAttempt).insert(),
        {
            "id": attempt_id,
            "gateway_request_id": first_request_id,
            "provider_id": provider_id,
            "model_id": model_id,
            "attempt_number": 1,
        },
    )

    with pytest.raises(IntegrityError):
        sqlite_connection.execute(
            _table(UsageRecord).insert(),
            {
                "id": uuid4(),
                "gateway_request_id": second_request_id,
                "provider_attempt_id": attempt_id,
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            },
        )


def test_sqlite_rejects_inconsistent_usage_total(sqlite_connection: Connection) -> None:
    request_id = _insert_request(sqlite_connection, "correlation-1")

    with pytest.raises(IntegrityError):
        sqlite_connection.execute(
            _table(UsageRecord).insert(),
            {
                "id": uuid4(),
                "gateway_request_id": request_id,
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 4,
            },
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Provider(settings={"api_key": "private-key"}),
        lambda: Model(capabilities={"prompt": "private prompt"}),
        lambda: GatewayRequest(
            correlation_id="correlation-1",
            requested_model="gateway-model",
            request_payload_redacted={"messages": [{"content": "private prompt"}]},
        ),
        lambda: GatewayRequest(
            correlation_id="correlation-1",
            requested_model="gateway-model",
            error_message="authorization=Bearer private-secret",
        ),
        lambda: ProviderAttempt(
            gateway_request_id=uuid4(),
            provider_id=uuid4(),
            model_id=uuid4(),
            attempt_number=1,
            error_message="completion=private completion",
        ),
    ],
)
def test_orm_rejects_sensitive_persistence_values(factory: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        factory()


def test_provider_rejects_credential_material_in_secret_reference() -> None:
    with pytest.raises(ValueError, match="credential material"):
        Provider(secret_ref="sk-ant-private-secret-value")
