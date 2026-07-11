"""Tests for removing the orphaned Feishu ``card_schema`` setting."""

from __future__ import annotations

import importlib

from ruamel.yaml import YAML


CONFIG_WITH_CARD_SCHEMA = """\
# keep this top-level comment
display:
  platforms:
    feishu:
      card_schema: "2.0"  # remove only this setting
      final_response_format: auto
gateway:
  platforms:
    feishu:
      card_schema: '2.0'
      extra:
        card_schema: "2.0"
        markdown_tables: markdown
platforms:
  feishu:
    extra:
      card_schema: '2.0'
      final_response_format: card
      markdown_tables: table
  telegram:
    extra:
      card_schema: keep-me
"""

EXPECTED_REMOVED_PATHS = {
    "display.platforms.feishu.card_schema",
    "gateway.platforms.feishu.card_schema",
    "gateway.platforms.feishu.extra.card_schema",
    "platforms.feishu.extra.card_schema",
}


def _migration_module():
    return importlib.import_module("scripts.migrations.remove_feishu_card_schema")


def _write_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_WITH_CARD_SCHEMA, encoding="utf-8")
    return config_path


def _load_yaml(config_path):
    yaml = YAML(typ="rt")
    with config_path.open("r", encoding="utf-8") as stream:
        return yaml.load(stream)


def test_migration_removes_only_card_schema_from_all_feishu_locations(tmp_path):
    config_path = _write_config(tmp_path)

    assert _migration_module().main(["--config", str(config_path), "--apply"]) == 0

    config = _load_yaml(config_path)
    assert "card_schema" not in config["display"]["platforms"]["feishu"]
    assert "card_schema" not in config["gateway"]["platforms"]["feishu"]
    assert "card_schema" not in config["gateway"]["platforms"]["feishu"]["extra"]
    assert "card_schema" not in config["platforms"]["feishu"]["extra"]
    assert config["platforms"]["telegram"]["extra"]["card_schema"] == "keep-me"
    assert "# keep this top-level comment" in config_path.read_text(encoding="utf-8")


def test_migration_preserves_final_response_format_and_markdown_tables(tmp_path):
    config_path = _write_config(tmp_path)

    _migration_module().main(["--config", str(config_path), "--apply"])

    config = _load_yaml(config_path)
    assert config["display"]["platforms"]["feishu"]["final_response_format"] == "auto"
    assert config["gateway"]["platforms"]["feishu"]["extra"]["markdown_tables"] == "markdown"
    assert config["platforms"]["feishu"]["extra"]["final_response_format"] == "card"
    assert config["platforms"]["feishu"]["extra"]["markdown_tables"] == "table"


def test_migration_is_idempotent(tmp_path):
    config_path = _write_config(tmp_path)
    migration = _migration_module()

    migration.main(["--config", str(config_path), "--apply"])
    after_first_apply = config_path.read_bytes()
    migration.main(["--config", str(config_path), "--apply"])

    assert config_path.read_bytes() == after_first_apply


def test_migration_dry_run_does_not_write(tmp_path):
    config_path = _write_config(tmp_path)
    before = config_path.read_bytes()

    assert _migration_module().main(["--config", str(config_path), "--dry-run"]) == 0

    assert config_path.read_bytes() == before


def test_migration_prints_exact_removed_yaml_paths(tmp_path, capsys):
    config_path = _write_config(tmp_path)

    _migration_module().main(["--config", str(config_path), "--dry-run"])

    output_lines = set(capsys.readouterr().out.splitlines())
    assert output_lines == {f"Would remove: {path}" for path in EXPECTED_REMOVED_PATHS}
