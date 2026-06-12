from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import Connection, Table, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from llm_gateway.domain import GenerateTokenUsage
from llm_gateway.persistence import (
    Base,
    GatewayRequest,
    GatewayRoute,
    Model,
    PricingSnapshot,
    Provider,
    ProviderAttempt,
    RouteBootstrap,
    SqlAlchemyGatewayLedger,
    UsageRecord,
    calculate_estimated_cost,
)

EXPECTED_TABLES = {
    "audit_metadata",
    "gateway_requests",
    "models",
    "pricing_snapshots",
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
    pricing_constraints = {constraint.name for constraint in _table(PricingSnapshot).constraints}
    usage_constraints = {constraint.name for constraint in _table(UsageRecord).constraints}

    assert "fk_provider_attempts_model_provider" in attempt_constraints
    assert "fk_pricing_snapshots_model_provider" in pricing_constraints
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


def _insert_model(
    connection: Connection,
    *,
    provider_id: object,
    gateway_name: str = "gateway-model",
    upstream_name: str = "upstream-model",
) -> object:
    model_id = uuid4()
    connection.execute(
        _table(Model).insert(),
        {
            "id": model_id,
            "provider_id": provider_id,
            "gateway_name": gateway_name,
            "upstream_name": upstream_name,
        },
    )
    return model_id


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


def test_sqlite_rejects_pricing_snapshot_with_provider_model_mismatch(
    sqlite_connection: Connection,
) -> None:
    model_provider_id = _insert_provider(sqlite_connection, "model-provider")
    other_provider_id = _insert_provider(sqlite_connection, "other-provider")
    model_id = _insert_model(sqlite_connection, provider_id=model_provider_id)

    with pytest.raises(IntegrityError):
        sqlite_connection.execute(
            _table(PricingSnapshot).insert(),
            {
                "id": uuid4(),
                "provider_id": other_provider_id,
                "model_id": model_id,
                "currency": "USD",
                "input_cost_per_million": Decimal("0.1500000000"),
                "output_cost_per_million": Decimal("0.6000000000"),
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


def test_calculate_estimated_cost_uses_decimal_accounting() -> None:
    cost = calculate_estimated_cost(
        input_tokens=2_000,
        output_tokens=500,
        input_cost_per_million=Decimal("0.1500000000"),
        output_cost_per_million=Decimal("0.6000000000"),
    )

    assert cost == Decimal("0.0006000000")


def test_sqlalchemy_ledger_bootstraps_route_and_persists_usage(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = SqlAlchemyGatewayLedger(sessions)

    ledger.ensure_r1_route(
        RouteBootstrap(
            provider_name="openai",
            provider_adapter="openai_responses",
            gateway_model="gateway-default",
            upstream_model="gpt-4.1-mini",
            currency="USD",
            input_cost_per_million=Decimal("0.1500000000"),
            output_cost_per_million=Decimal("0.6000000000"),
        )
    )

    route = ledger.resolve_route("gateway-default")
    assert route == GatewayRoute(
        provider_id=route.provider_id,
        provider_name="openai",
        model_id=route.model_id,
        gateway_model="gateway-default",
        upstream_model="gpt-4.1-mini",
        routing_reason="configured_single_path",
    )

    request_id = ledger.create_gateway_request(
        correlation_id="correlation-1",
        requested_model="gateway-default",
    )
    attempt_id = ledger.create_provider_attempt(gateway_request_id=request_id, route=route)
    started_at = datetime.now(UTC)
    completed_at = started_at + timedelta(seconds=1)
    ledger.start_generation(request_id=request_id, attempt_id=attempt_id, started_at=started_at)

    usage_cost = ledger.complete_generation(
        gateway_request_id=request_id,
        attempt_id=attempt_id,
        route=route,
        provider_request_id="resp_123",
        usage=GenerateTokenUsage(input_tokens=2, output_tokens=3, total_tokens=5),
        latency_ms=17,
        completed_at=completed_at,
    )

    assert usage_cost.estimated_cost == Decimal("0.0000021000")

    with sessions() as session:
        assert session.query(PricingSnapshot).count() == 1
        request = session.get(GatewayRequest, request_id)
        attempt = session.get(ProviderAttempt, attempt_id)
        usage = session.query(UsageRecord).one()

    assert request is not None and request.status == "succeeded"
    assert attempt is not None and attempt.status == "succeeded"
    assert usage.estimated_cost == Decimal("0.0000021000")
    assert usage.pricing_snapshot_id is not None


def test_sqlalchemy_ledger_replaces_obsolete_route_mapping(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger-route-replace.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = SqlAlchemyGatewayLedger(sessions)

    ledger.ensure_r1_route(
        RouteBootstrap(
            provider_name="openai",
            provider_adapter="openai_responses",
            gateway_model="gateway-default",
            upstream_model="gpt-4.1-mini",
            currency="USD",
            input_cost_per_million=Decimal("0.1500000000"),
            output_cost_per_million=Decimal("0.6000000000"),
        )
    )
    ledger.ensure_r1_route(
        RouteBootstrap(
            provider_name="openai",
            provider_adapter="openai_responses",
            gateway_model="gateway-default",
            upstream_model="gpt-4.1-nano",
            currency="USD",
            input_cost_per_million=Decimal("0.1500000000"),
            output_cost_per_million=Decimal("0.6000000000"),
        )
    )

    route = ledger.resolve_route("gateway-default")
    assert route is not None
    assert route.upstream_model == "gpt-4.1-nano"

    with sessions() as session:
        enabled_models = session.query(Model).filter(
            Model.gateway_name == "gateway-default",
            Model.enabled.is_(True),
        ).all()
        disabled_models = session.query(Model).filter(
            Model.gateway_name == "gateway-default",
            Model.enabled.is_(False),
        ).all()

    assert len(enabled_models) == 1
    assert enabled_models[0].upstream_name == "gpt-4.1-nano"
    assert len(disabled_models) == 1
    assert disabled_models[0].upstream_name == "gpt-4.1-mini"


def test_sqlalchemy_ledger_ignores_future_pricing_until_effective(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger-pricing.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = SqlAlchemyGatewayLedger(sessions)

    ledger.ensure_r1_route(
        RouteBootstrap(
            provider_name="openai",
            provider_adapter="openai_responses",
            gateway_model="gateway-default",
            upstream_model="gpt-4.1-mini",
            currency="USD",
            input_cost_per_million=Decimal("0.1500000000"),
            output_cost_per_million=Decimal("0.6000000000"),
        )
    )
    route = ledger.resolve_route("gateway-default")
    assert route is not None

    with sessions.begin() as session:
        current_snapshot = session.query(PricingSnapshot).one()
        future_snapshot = PricingSnapshot(
            provider_id=route.provider_id,
            model_id=route.model_id,
            currency="USD",
            input_cost_per_million=Decimal("9.0000000000"),
            output_cost_per_million=Decimal("9.0000000000"),
            effective_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add(future_snapshot)
        session.flush()
        future_snapshot_id = future_snapshot.id
        current_snapshot_id = current_snapshot.id

    request_id = ledger.create_gateway_request(
        correlation_id="correlation-1",
        requested_model="gateway-default",
    )
    attempt_id = ledger.create_provider_attempt(gateway_request_id=request_id, route=route)
    started_at = datetime.now(UTC)
    completed_at = started_at + timedelta(seconds=1)
    ledger.start_generation(request_id=request_id, attempt_id=attempt_id, started_at=started_at)
    ledger.complete_generation(
        gateway_request_id=request_id,
        attempt_id=attempt_id,
        route=route,
        provider_request_id="resp_123",
        usage=GenerateTokenUsage(input_tokens=2, output_tokens=3, total_tokens=5),
        latency_ms=10,
        completed_at=completed_at,
    )

    with sessions() as session:
        usage = session.query(UsageRecord).one()

    assert usage.pricing_snapshot_id == current_snapshot_id
    assert usage.pricing_snapshot_id != future_snapshot_id
