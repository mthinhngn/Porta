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


def test_initial_revision_is_the_single_head() -> None:
    scripts = ScriptDirectory.from_config(_alembic_config())

    assert scripts.get_heads() == ["20260611_0002"]


def test_upgrade_head_emits_phase_zero_schema_ddl() -> None:
    config = _alembic_config()
    command.upgrade(config, "head", sql=True)
    output = config.output_buffer
    assert isinstance(output, StringIO)
    ddl = output.getvalue()

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
    assert "fk_pricing_snapshots_model_provider" in ddl
    assert "fk_usage_records_pricing_snapshot_id_pricing_snapshots" in ddl
