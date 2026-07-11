#!/usr/bin/env python3
"""Remove orphaned Feishu ``card_schema`` settings from config.yaml.

The migration intentionally leaves ``final_response_format`` and
``markdown_tables`` untouched.  Feishu cards continue to use the renderer's
fixed schema version rather than a user-configurable setting.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


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


def _remove_card_schema(
    node: Any,
    path: tuple[str, ...],
    removed_paths: list[str],
) -> None:
    """Recursively remove ``card_schema`` keys below one Feishu config root."""
    if isinstance(node, MutableMapping):
        for key in list(node.keys()):
            key_text = str(key)
            child_path = (*path, key_text)
            if key_text == "card_schema":
                del node[key]
                removed_paths.append(".".join(child_path))
            else:
                _remove_card_schema(node[key], child_path, removed_paths)
    elif isinstance(node, MutableSequence):
        for index, child in enumerate(node):
            _remove_card_schema(child, (*path[:-1], f"{path[-1]}[{index}]"), removed_paths)


def migrate_config(config_path: Path, *, apply: bool) -> list[str]:
    """Remove Feishu ``card_schema`` keys and optionally persist the result."""
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True

    with config_path.open("r", encoding="utf-8") as stream:
        config = yaml.load(stream)

    removed_paths: list[str] = []
    for root_path in FEISHU_CONFIG_ROOTS:
        feishu_config = _mapping_at(config, root_path)
        if feishu_config is not None:
            _remove_card_schema(feishu_config, root_path, removed_paths)

    if apply and removed_paths:
        with config_path.open("w", encoding="utf-8") as stream:
            yaml.dump(config, stream)

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
