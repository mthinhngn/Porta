from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def _alembic_config() -> Config:
    root = Path(__file__).resolve().parents[1]
    output = StringIO()
    config = Config(root / "alembic.ini", output_buffer=output)
    config.set_main_option("script_location", str(root / "alembic"))
    return config


def test_cached_pricing_revision_is_the_single_head() -> None:
    scripts = ScriptDirectory.from_config(_alembic_config())

    assert scripts.get_heads() == ["20260612_0003"]
    assert scripts.get_base() == "20260611_0001"
    assert [revision.revision for revision in scripts.walk_revisions()] == [
        "20260612_0003",
        "20260611_0002",
        "20260611_0001",
    ]
    assert scripts.get_revision("20260612_0003").down_revision == "20260611_0002"


def test_upgrade_head_emits_cached_pricing_and_usage_integrity_ddl() -> None:
    config = _alembic_config()
    assert config.get_main_option("sqlalchemy.url").startswith("postgresql+asyncpg://")

    command.upgrade(config, "head", sql=True)
    output = config.output_buffer
    assert isinstance(output, StringIO)
    ddl = output.getvalue()

    assert ddl.strip().startswith("BEGIN;")
    assert ddl.strip().endswith("COMMIT;")
    assert "-- Running upgrade  -> 20260611_0001" in ddl
    assert "-- Running upgrade 20260611_0001 -> 20260611_0002" in ddl
    assert "-- Running upgrade 20260611_0002 -> 20260612_0003" in ddl

    for table_name in (
        "providers",
        "gateway_requests",
        "models",
        "audit_metadata",
        "pricing_snapshots",
        "provider_attempts",
        "usage_records",
    ):
        assert f"CREATE TABLE {table_name}" in ddl

    assert "pricing_snapshot_id" in ddl
    assert "cached_input_cost_per_million" in ddl
    assert "SET cached_input_cost_per_million = input_cost_per_million" in ddl
    assert "cached_input_tokens" in ddl
    assert "cached_input_tokens <= prompt_tokens" in ddl
    assert "uq_usage_records_provider_attempt_id" in ddl
    assert "fk_pricing_snapshots_model_provider" in ddl
    assert "fk_usage_records_pricing_snapshot_id_pricing_snapshots" in ddl
    assert "INSERT INTO alembic_version (version_num) VALUES ('20260611_0001')" in ddl
    assert "SET version_num='20260611_0002'" in ddl
    assert "SET version_num='20260612_0003'" in ddl
