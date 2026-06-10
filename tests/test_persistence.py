from llm_gateway.persistence import Base

EXPECTED_TABLES = {
    "audit_metadata",
    "gateway_requests",
    "models",
    "provider_attempts",
    "providers",
    "usage_records",
}


def test_sqlalchemy_metadata_contains_phase_zero_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_sqlalchemy_metadata_uses_naming_convention() -> None:
    convention = Base.metadata.naming_convention

    assert convention is not None
    assert convention["pk"] == "pk_%(table_name)s"
    assert convention["fk"] == ("fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s")
