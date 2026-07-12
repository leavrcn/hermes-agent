"""Tests for removing the orphaned Feishu ``card_schema`` setting."""

from __future__ import annotations

import errno
import importlib
import os
import stat

import pytest
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


BYTE_STABLE_CONFIG = """\
# formatting below is intentionally unlike ruamel's default dump
display:
  platforms:
    feishu:
      card_schema: "2.0"  # delete target display
      long_plain: this is a deliberately long plain scalar that must remain on exactly the same physical line without wrapping or reformatting
      compact_sequence:
      - alpha
      - beta
gateway:
    platforms:
      feishu:
        extra: {markdown_tables: table, final_response_format: card}
        card_schema: '2.0'  # delete target gateway
unrelated:
  card_schema: keep-me
"""


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


@pytest.mark.parametrize(
    ("line_ending", "terminal_newline"),
    [(b"\n", True), (b"\r\n", True), (b"\n", False)],
)
def test_apply_deletes_only_target_lines_byte_for_byte(
    tmp_path, line_ending, terminal_newline
):
    config_path = tmp_path / "config.yaml"
    before = BYTE_STABLE_CONFIG.encode("utf-8").replace(b"\n", line_ending)
    if not terminal_newline:
        before = before.removesuffix(line_ending)
    config_path.write_bytes(before)

    target_line_indexes = {
        index
        for index, line in enumerate(before.splitlines(keepends=True))
        if b"# delete target" in line
    }
    expected = b"".join(
        line
        for index, line in enumerate(before.splitlines(keepends=True))
        if index not in target_line_indexes
    )

    assert _migration_module().main(["--config", str(config_path), "--apply"]) == 0

    assert config_path.read_bytes() == expected
    assert b"unrelated:" + line_ending + b"  card_schema: keep-me" in expected


@pytest.mark.parametrize(
    "target_yaml",
    [
        "      card_schema: |\n        2.0\n",
        "      card_schema:\n        major: 2\n",
        "      card_schema: [2.0, 1.0]\n",
        '      card_schema: "2.0\n        continued"\n',
    ],
)
def test_apply_rejects_complex_target_without_changing_file(tmp_path, target_yaml):
    config_path = tmp_path / "config.yaml"
    before = (
        "display:\n"
        "  platforms:\n"
        "    feishu:\n"
        f"{target_yaml}"
        "      final_response_format: auto\n"
    ).encode()
    config_path.write_bytes(before)

    with pytest.raises(ValueError, match="single-line scalar"):
        _migration_module().main(["--config", str(config_path), "--apply"])

    assert config_path.read_bytes() == before


def test_apply_temp_write_failure_keeps_original_and_cleans_temp_file(
    tmp_path, monkeypatch
):
    config_path = _write_config(tmp_path)
    before = config_path.read_bytes()
    before_entries = set(tmp_path.iterdir())
    real_write = os.write
    injected = False

    def partial_write_then_enospc(fd, data):
        nonlocal injected
        if not injected:
            injected = True
            real_write(fd, data[: max(1, len(data) // 2)])
            raise OSError(errno.ENOSPC, "no space left on device")
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", partial_write_then_enospc)

    with pytest.raises(OSError, match="no space left on device"):
        _migration_module().main(["--config", str(config_path), "--apply"])

    assert config_path.read_bytes() == before
    assert set(tmp_path.iterdir()) == before_entries


def test_apply_replace_failure_keeps_original_and_cleans_temp_file(
    tmp_path, monkeypatch
):
    config_path = _write_config(tmp_path)
    before = config_path.read_bytes()
    before_entries = set(tmp_path.iterdir())

    def reject_replace(_source, _destination):
        raise OSError(errno.EIO, "replace failed")

    monkeypatch.setattr(os, "replace", reject_replace)

    with pytest.raises(OSError, match="replace failed"):
        _migration_module().main(["--config", str(config_path), "--apply"])

    assert config_path.read_bytes() == before
    assert set(tmp_path.iterdir()) == before_entries


def test_apply_atomic_replace_preserves_original_permissions(tmp_path):
    config_path = _write_config(tmp_path)
    config_path.chmod(0o640)
    before_mode = stat.S_IMODE(config_path.stat().st_mode)

    _migration_module().main(["--config", str(config_path), "--apply"])

    assert stat.S_IMODE(config_path.stat().st_mode) == before_mode


@pytest.mark.skipif(
    not hasattr(os, "fchown"), reason="requires POSIX fchown fault injection"
)
def test_apply_chown_failure_keeps_original_and_cleans_temp_file(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    before = config_path.read_bytes()
    before_entries = set(tmp_path.iterdir())

    def reject_chown(_fd, _uid, _gid):
        raise OSError(errno.EPERM, "chown rejected")

    monkeypatch.setattr(os, "fchown", reject_chown)

    with pytest.raises(OSError, match="chown rejected"):
        _migration_module().main(["--config", str(config_path), "--apply"])

    assert config_path.read_bytes() == before
    assert set(tmp_path.iterdir()) == before_entries


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() != 0,
    reason="requires root to create a foreign-owned fixture",
)
def test_apply_atomic_replace_preserves_original_owner_group(tmp_path):
    config_path = _write_config(tmp_path)
    original_uid = 65534
    original_gid = 65534
    os.chown(config_path, original_uid, original_gid)
    before_stat = config_path.stat()

    _migration_module().main(["--config", str(config_path), "--apply"])

    after_stat = config_path.stat()
    assert (after_stat.st_uid, after_stat.st_gid) == (
        before_stat.st_uid,
        before_stat.st_gid,
    )
    assert stat.S_IMODE(after_stat.st_mode) == stat.S_IMODE(before_stat.st_mode)


def test_apply_preserves_symlink_and_migrates_real_target(tmp_path):
    managed_dir = tmp_path / "managed"
    managed_dir.mkdir()
    real_config = _write_config(managed_dir)
    link_dir = tmp_path / "profile"
    link_dir.mkdir()
    config_link = link_dir / "config.yaml"
    config_link.symlink_to(real_config)

    _migration_module().main(["--config", str(config_link), "--apply"])

    assert config_link.is_symlink()
    assert config_link.resolve() == real_config
    config = _load_yaml(real_config)
    assert "card_schema" not in config["display"]["platforms"]["feishu"]
    assert "card_schema" not in config["gateway"]["platforms"]["feishu"]
    assert "card_schema" not in config["gateway"]["platforms"]["feishu"]["extra"]
    assert "card_schema" not in config["platforms"]["feishu"]["extra"]
    assert config["platforms"]["telegram"]["extra"]["card_schema"] == "keep-me"
    assert config_link.read_bytes() == real_config.read_bytes()
    assert set(link_dir.iterdir()) == {config_link}


def test_apply_succeeds_without_posix_metadata_apis(tmp_path, monkeypatch):
    migration = _migration_module()
    config_path = _write_config(tmp_path)
    fake_os_members = {
        name: getattr(os, name)
        for name in dir(os)
        if name not in {"fchown", "fchmod", "geteuid"}
    }
    fake_os_members["name"] = "nt"
    fake_os = type("FakeWindowsOs", (), fake_os_members)

    monkeypatch.setattr(migration, "os", fake_os)
    migration.main(["--config", str(config_path), "--apply"])

    config = _load_yaml(config_path)
    assert "card_schema" not in config["display"]["platforms"]["feishu"]
    assert "card_schema" not in config["gateway"]["platforms"]["feishu"]
    assert "card_schema" not in config["gateway"]["platforms"]["feishu"]["extra"]
    assert "card_schema" not in config["platforms"]["feishu"]["extra"]
    assert config["platforms"]["telegram"]["extra"]["card_schema"] == "keep-me"


def test_apply_retries_successful_short_writes(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    real_write = os.write
    write_calls = 0

    def short_write(fd, data):
        nonlocal write_calls
        write_calls += 1
        return real_write(fd, data[:7])

    monkeypatch.setattr(os, "write", short_write)
    _migration_module().main(["--config", str(config_path), "--apply"])

    assert write_calls > 1
    config = _load_yaml(config_path)
    assert "card_schema" not in config["display"]["platforms"]["feishu"]


def test_apply_zero_progress_write_keeps_original_and_cleans_temp(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    before = config_path.read_bytes()
    before_entries = set(tmp_path.iterdir())
    monkeypatch.setattr(os, "write", lambda _fd, _data: 0)

    with pytest.raises(OSError, match="made no progress"):
        _migration_module().main(["--config", str(config_path), "--apply"])

    assert config_path.read_bytes() == before
    assert set(tmp_path.iterdir()) == before_entries


@pytest.mark.skipif(not hasattr(os, "fchmod"), reason="requires POSIX fchmod fault injection")
def test_apply_fchmod_failure_keeps_original_and_cleans_temp(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    before = config_path.read_bytes()
    before_entries = set(tmp_path.iterdir())

    def reject_fchmod(_fd, _mode):
        raise OSError(errno.EPERM, "fchmod rejected")

    monkeypatch.setattr(os, "fchmod", reject_fchmod)

    with pytest.raises(OSError, match="fchmod rejected"):
        _migration_module().main(["--config", str(config_path), "--apply"])

    assert config_path.read_bytes() == before
    assert set(tmp_path.iterdir()) == before_entries


def test_apply_replace_then_error_restores_original_bytes_and_metadata(
    tmp_path, monkeypatch
):
    config_path = _write_config(tmp_path)
    config_path.chmod(0o640)
    before = config_path.read_bytes()
    before_stat = config_path.stat()
    real_replace = os.replace
    replace_calls = 0

    def replace_then_fail_once(source, destination):
        nonlocal replace_calls
        replace_calls += 1
        real_replace(source, destination)
        if replace_calls == 1:
            raise OSError(errno.EIO, "post-replace failure")

    monkeypatch.setattr(os, "replace", replace_then_fail_once)

    with pytest.raises(OSError, match="post-replace failure"):
        _migration_module().main(["--config", str(config_path), "--apply"])

    after_stat = config_path.stat()
    assert replace_calls == 2
    assert config_path.read_bytes() == before
    assert (after_stat.st_uid, after_stat.st_gid, stat.S_IMODE(after_stat.st_mode)) == (
        before_stat.st_uid,
        before_stat.st_gid,
        stat.S_IMODE(before_stat.st_mode),
    )
    assert not list(tmp_path.glob(".config.yaml.*.tmp"))


def test_apply_post_write_semantic_failure_restores_original(tmp_path, monkeypatch):
    migration = _migration_module()
    config_path = _write_config(tmp_path)
    before = config_path.read_bytes()
    before_stat = config_path.stat()
    real_verify = migration._verify_migrated_text
    verify_calls = 0

    def fail_second_verify(text, expected_unrelated):
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 2:
            raise ValueError("post-write semantic rejection")
        return real_verify(text, expected_unrelated)

    monkeypatch.setattr(migration, "_verify_migrated_text", fail_second_verify)

    with pytest.raises(ValueError, match="post-write semantic rejection"):
        migration.main(["--config", str(config_path), "--apply"])

    after_stat = config_path.stat()
    assert config_path.read_bytes() == before
    assert (after_stat.st_uid, after_stat.st_gid, stat.S_IMODE(after_stat.st_mode)) == (
        before_stat.st_uid,
        before_stat.st_gid,
        stat.S_IMODE(before_stat.st_mode),
    )


@pytest.mark.skipif(not hasattr(os, "fchown"), reason="requires POSIX owner metadata")
def test_apply_passes_original_owner_to_atomic_replace(tmp_path, monkeypatch):
    migration = _migration_module()
    config_path = _write_config(tmp_path)
    before_stat = config_path.stat()
    real_atomic_replace = migration._atomic_replace_bytes
    owner_arguments = []

    def capture_owner(path, data, mode, owner_uid, owner_gid):
        owner_arguments.append((owner_uid, owner_gid))
        return real_atomic_replace(path, data, mode, owner_uid, owner_gid)

    monkeypatch.setattr(migration, "_atomic_replace_bytes", capture_owner)
    migration.main(["--config", str(config_path), "--apply"])

    assert owner_arguments == [(before_stat.st_uid, before_stat.st_gid)]
