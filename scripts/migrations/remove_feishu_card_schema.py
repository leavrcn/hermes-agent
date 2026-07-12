#!/usr/bin/env python3
"""Remove orphaned Feishu ``card_schema`` settings from config.yaml.

The migration intentionally leaves ``final_response_format`` and
``markdown_tables`` untouched.  Feishu cards continue to use the renderer's
fixed schema version rather than a user-configurable setting.
"""

from __future__ import annotations

import argparse
import errno
import os
import tempfile
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


FEISHU_CONFIG_ROOTS: tuple[tuple[str, ...], ...] = (
    ("display", "platforms", "feishu"),
    ("gateway", "platforms", "feishu"),
    ("platforms", "feishu"),
)


def _mapping_at(root: Any, path: Sequence[str]) -> MutableMapping[str, Any] | None:
    current = root
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current if isinstance(current, MutableMapping) else None


def _sequence_item_path(path: tuple[str, ...], index: int) -> tuple[str, ...]:
    return (*path[:-1], f"{path[-1]}[{index}]")


def _find_card_schema(
    node: Any,
    path: tuple[str, ...],
    found: list[tuple[tuple[str, ...], CommentedMap, Any, int]],
) -> None:
    """Locate card_schema keys and their exact source lines in round-trip YAML."""
    if isinstance(node, CommentedMap):
        for key in node:
            key_text = str(key)
            child_path = (*path, key_text)
            if key_text == "card_schema":
                try:
                    key_line, _ = node.lc.key(key)
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Cannot locate source line for {'.'.join(child_path)}"
                    ) from exc
                found.append((child_path, node, key, key_line))
            else:
                _find_card_schema(node[key], child_path, found)
    elif isinstance(node, MutableSequence):
        for index, child in enumerate(node):
            _find_card_schema(child, _sequence_item_path(path, index), found)


def _all_card_schema_values(
    node: Any,
    path: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], Any]]:
    """Return every card_schema path/value pair, including non-Feishu settings."""
    found: list[tuple[tuple[str, ...], Any]] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            child_path = (*path, str(key))
            if str(key) == "card_schema":
                found.append((child_path, value))
            found.extend(_all_card_schema_values(value, child_path))
    elif isinstance(node, MutableSequence):
        for index, child in enumerate(node):
            found.extend(_all_card_schema_values(child, _sequence_item_path(path, index)))
    return found


def _is_feishu_path(path: tuple[str, ...]) -> bool:
    return any(path[: len(root)] == root for root in FEISHU_CONFIG_ROOTS)


def _unrelated_card_schema_values(config: Any) -> list[tuple[tuple[str, ...], Any]]:
    return [entry for entry in _all_card_schema_values(config) if not _is_feishu_path(entry[0])]


def _round_trip_load(text: str) -> Any:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    return yaml.load(text)


def _validate_single_line_scalar(
    target: tuple[tuple[str, ...], CommentedMap, Any, int],
    source_lines: list[str],
) -> None:
    """Reject targets that cannot safely be removed as one complete source line."""
    path, parent, key, key_line = target
    path_text = ".".join(path)
    value = parent[key]
    if isinstance(value, (Mapping, MutableSequence)) or getattr(value, "style", None) in {
        "|",
        ">",
    }:
        raise ValueError(f"{path_text} must be a single-line scalar")

    try:
        value_line, _ = parent.lc.value(key)
        source_line = source_lines[key_line]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path_text} must be a single-line scalar") from exc
    if value_line != key_line:
        raise ValueError(f"{path_text} must be a single-line scalar")

    isolated_line = source_line.lstrip(" \t").rstrip("\r\n")
    try:
        isolated = _round_trip_load(isolated_line)
    except Exception as exc:
        raise ValueError(f"{path_text} must be a single-line scalar") from exc
    if (
        not isinstance(isolated, Mapping)
        or len(isolated) != 1
        or str(next(iter(isolated))) != "card_schema"
        or isolated[next(iter(isolated))] != value
    ):
        raise ValueError(f"{path_text} must be a single-line scalar")


def _find_feishu_targets(config: Any) -> list[tuple[tuple[str, ...], CommentedMap, Any, int]]:
    targets: list[tuple[tuple[str, ...], CommentedMap, Any, int]] = []
    for root_path in FEISHU_CONFIG_ROOTS:
        feishu_config = _mapping_at(config, root_path)
        if feishu_config is not None:
            _find_card_schema(feishu_config, root_path, targets)
    return targets


def _verify_migrated_text(
    text: str,
    expected_unrelated: list[tuple[tuple[str, ...], Any]],
) -> None:
    """Parse with both loaders and verify exact migration scope."""
    YAML(typ="safe").load(text)
    config = _round_trip_load(text)
    remaining_targets = _find_feishu_targets(config)
    if remaining_targets:
        paths = ", ".join(".".join(target[0]) for target in remaining_targets)
        raise ValueError(f"card_schema target paths remain after migration: {paths}")
    if _unrelated_card_schema_values(config) != expected_unrelated:
        raise ValueError("non-Feishu card_schema settings changed during migration")


def _atomic_replace_bytes(config_path: Path, data: bytes, mode: int) -> None:
    """Durably replace ``config_path`` without exposing a truncated file."""
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.",
        suffix=".tmp",
        dir=config_path.parent,
    )
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        remaining = memoryview(data)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError(errno.EIO, "temporary configuration write made no progress")
            remaining = remaining[written:]
        # os.write is unbuffered, so there is no userspace buffer to flush.
        # fsync publishes every completed write before the atomic rename.
        os.fsync(fd)
        os.close(fd)
        fd = -1

        os.replace(temp_path, config_path)
        temp_path = None

        # Persist the directory entry update as well as the file contents.
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(config_path.parent, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def migrate_config(config_path: Path, *, apply: bool) -> list[str]:
    """Remove Feishu card_schema lines without changing any other file bytes."""
    original_bytes = config_path.read_bytes()
    source_text = original_bytes.decode("utf-8")
    config = _round_trip_load(source_text)
    targets = _find_feishu_targets(config)
    source_lines = source_text.splitlines(keepends=True)
    for target in targets:
        _validate_single_line_scalar(target, source_lines)

    removed_paths = [".".join(target[0]) for target in targets]
    if not apply or not targets:
        return removed_paths

    target_lines = {target[3] for target in targets}
    if len(target_lines) != len(targets):
        raise ValueError("each card_schema target must occupy its own single line")
    migrated_bytes = b"".join(
        line
        for index, line in enumerate(original_bytes.splitlines(keepends=True))
        if index not in target_lines
    )
    migrated_text = migrated_bytes.decode("utf-8")
    expected_unrelated = _unrelated_card_schema_values(config)

    # Validate before writing so unsupported input can never damage the source file.
    _verify_migrated_text(migrated_text, expected_unrelated)
    original_mode = config_path.stat().st_mode & 0o7777
    try:
        _atomic_replace_bytes(config_path, migrated_bytes, original_mode)
        written_bytes = config_path.read_bytes()
        if written_bytes != migrated_bytes:
            raise ValueError("written configuration differs from validated migration bytes")
        # Re-parse the actual on-disk result and re-check migration scope.
        _verify_migrated_text(written_bytes.decode("utf-8"), expected_unrelated)
    except Exception:
        # A temp-write or replace failure leaves the original path untouched.
        # If only post-replace verification failed, restore the original bytes
        # through the same atomic path rather than truncating in place.
        if config_path.read_bytes() != original_bytes:
            _atomic_replace_bytes(config_path, original_bytes, original_mode)
        raise

    return removed_paths


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Remove orphaned card_schema settings from Feishu configuration."
    )
    parser.add_argument("--config", required=True, type=Path, help="Path to config.yaml")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    mode.add_argument("--apply", action="store_true", help="Write the migrated YAML in place")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    removed_paths = migrate_config(args.config, apply=args.apply)
    prefix = "Removed" if args.apply else "Would remove"
    for path in removed_paths:
        print(f"{prefix}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
