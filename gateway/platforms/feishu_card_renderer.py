"""Feishu Card JSON 2.0 renderer for final assistant replies.

Converts a platform-neutral ``MessageDocument`` into Feishu Card JSON 2.0 dicts
suitable for ``msg_type=interactive``. This module is purely functional — it
produces card dictionaries and has no transport dependency on ``FeishuAdapter``
or the lark SDK.
"""

from __future__ import annotations

import json
import re
from typing import Any

from gateway.rendering.document import (
    CodeBlock,
    DividerBlock,
    HeadingBlock,
    ImageBlock,
    MessageDocument,
    ParagraphBlock,
    TableBlock,
)

_PLAINTEXT_SUMMARY_MAX_LENGTH = 80
_FIXED_CARD_TITLE = "Hermes"
_UNDESIRED_SUMMARY_CHARS_RE = re.compile(r"[`*_~\[\]!#>"">]")
_DEFAULT_MAX_MARKDOWN_CHARS = 3000
_DEFAULT_MAX_ELEMENTS_PER_CARD = 120
_DEFAULT_MAX_CARD_CHARS = 6000


def render_document_to_feishu_card_v2(
    doc: MessageDocument,
    *,
    title: str = _FIXED_CARD_TITLE,
    table_policy: str = "table",
    table_cell_type: str = "markdown",
    max_tables: int = 5,
    max_columns: int = 8,
    max_rows: int = 20,
    image_key_by_source: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Convert a ``MessageDocument`` into a single Feishu Card JSON 2.0 dict."""
    elements = _document_to_elements(
        doc,
        table_policy=table_policy,
        table_cell_type=table_cell_type,
        max_tables=max_tables,
        max_columns=max_columns,
        max_rows=max_rows,
        image_key_by_source=image_key_by_source,
    )
    return _build_card_payload(doc.blocks, elements, title=title)


def render_document_to_feishu_card_v2_parts(
    doc: MessageDocument,
    *,
    title: str = _FIXED_CARD_TITLE,
    table_policy: str = "table",
    table_cell_type: str = "markdown",
    max_tables: int = 5,
    max_columns: int = 8,
    max_rows: int = 20,
    image_key_by_source: dict[str, str] | None = None,
    max_markdown_chars: int = _DEFAULT_MAX_MARKDOWN_CHARS,
    max_elements_per_card: int = _DEFAULT_MAX_ELEMENTS_PER_CARD,
    max_card_chars: int = _DEFAULT_MAX_CARD_CHARS,
) -> list[dict[str, Any]]:
    """Convert a document into one or more Feishu Card v2 payload dicts.

    Large single cards can be clipped by Feishu clients or rejected by OpenAPI.
    This renderer keeps the response complete by splitting oversized markdown
    elements, then partitioning elements into multiple cards.
    """
    elements = _document_to_elements(
        doc,
        table_policy=table_policy,
        table_cell_type=table_cell_type,
        max_tables=max_tables,
        max_columns=max_columns,
        max_rows=max_rows,
        image_key_by_source=image_key_by_source,
    )
    expanded: list[dict[str, Any]] = []
    for element in elements:
        expanded.extend(_split_oversized_markdown_element(element, max_markdown_chars))

    groups = _partition_elements(
        expanded,
        max_elements_per_card=max_elements_per_card,
        max_card_chars=max_card_chars,
    )
    if not groups:
        groups = [[]]
    if len(groups) == 1:
        return [_build_card_payload(doc.blocks, groups[0], title=title)]

    total = len(groups)
    return [
        _build_card_payload(doc.blocks, group, title=f"{title} {idx}/{total}")
        for idx, group in enumerate(groups, start=1)
    ]


def build_feishu_card_v2_payload(text: str, *, table_policy: str = "table") -> str:
    from gateway.rendering.markdown_parser import parse_markdown_document

    return json.dumps(
        render_document_to_feishu_card_v2(
            parse_markdown_document(text), table_policy=table_policy
        ),
        ensure_ascii=False,
    )


def build_feishu_card_v2_payloads(
    text: str,
    *,
    table_policy: str = "table",
    max_markdown_chars: int = _DEFAULT_MAX_MARKDOWN_CHARS,
    max_elements_per_card: int = _DEFAULT_MAX_ELEMENTS_PER_CARD,
    max_card_chars: int = _DEFAULT_MAX_CARD_CHARS,
) -> list[str]:
    from gateway.rendering.markdown_parser import parse_markdown_document

    return [
        json.dumps(card, ensure_ascii=False)
        for card in render_document_to_feishu_card_v2_parts(
            parse_markdown_document(text),
            table_policy=table_policy,
            max_markdown_chars=max_markdown_chars,
            max_elements_per_card=max_elements_per_card,
            max_card_chars=max_card_chars,
        )
    ]


def build_feishu_card_v2_payload_from_document(
    doc: MessageDocument,
    *,
    table_policy: str = "table",
    image_key_by_source: dict[str, str] | None = None,
) -> str:
    return json.dumps(
        render_document_to_feishu_card_v2(
            doc,
            table_policy=table_policy,
            image_key_by_source=image_key_by_source,
        ),
        ensure_ascii=False,
    )


def build_feishu_card_v2_payloads_from_document(
    doc: MessageDocument,
    *,
    table_policy: str = "table",
    image_key_by_source: dict[str, str] | None = None,
    max_markdown_chars: int = _DEFAULT_MAX_MARKDOWN_CHARS,
    max_elements_per_card: int = _DEFAULT_MAX_ELEMENTS_PER_CARD,
    max_card_chars: int = _DEFAULT_MAX_CARD_CHARS,
) -> list[str]:
    return [
        json.dumps(card, ensure_ascii=False)
        for card in render_document_to_feishu_card_v2_parts(
            doc,
            table_policy=table_policy,
            image_key_by_source=image_key_by_source,
            max_markdown_chars=max_markdown_chars,
            max_elements_per_card=max_elements_per_card,
            max_card_chars=max_card_chars,
        )
    ]


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _build_card_payload(
    blocks: list[Any],
    elements: list[Any],
    *,
    title: str,
) -> dict[str, Any]:
    summary = _build_summary(blocks)
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "width_mode": "fill",
            "summary": {"content": summary},
        },
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue",
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 8px 12px 8px",
            "elements": elements,
        },
    }


def _document_to_elements(
    doc: MessageDocument,
    *,
    table_policy: str,
    table_cell_type: str,
    max_tables: int,
    max_columns: int,
    max_rows: int,
    image_key_by_source: dict[str, str] | None,
) -> list[Any]:
    elements: list[Any] = []
    table_count = 0

    for block in doc.blocks:
        if isinstance(block, ParagraphBlock):
            elements.append(
                {"tag": "markdown", "content": block.text, "text_size": "normal"}
            )
        elif isinstance(block, HeadingBlock):
            elements.append(
                {"tag": "markdown", "content": block.text, "text_size": "heading"}
            )
        elif isinstance(block, CodeBlock):
            content = _render_code_block_content(block)
            elements.append(
                {"tag": "markdown", "content": content, "text_size": "normal"}
            )
        elif isinstance(block, DividerBlock):
            elements.append({"tag": "hr"})
        elif isinstance(block, ImageBlock):
            image_key = (image_key_by_source or {}).get(block.source, "")
            if image_key:
                elements.append(
                    {
                        "tag": "img",
                        "img_key": image_key,
                        "alt": {
                            "tag": "plain_text",
                            "content": block.alt or "image",
                        },
                    }
                )
            else:
                elements.append(
                    {
                        "tag": "markdown",
                        "content": f"[Image: {block.alt or block.source}]",
                        "text_size": "normal",
                    }
                )
        elif isinstance(block, TableBlock):
            use_table = (
                table_policy == "table"
                and table_count < max_tables
                and len(block.headers) <= max_columns
                and len(block.rows) <= max_rows
                and bool(block.headers)
                and bool(block.rows)
            )
            if use_table:
                elements.append(_build_table_element(block, table_cell_type, len(block.rows)))
                table_count += 1
            else:
                content = _render_code_block_content_from_raw(
                    language="markdown", code=block.raw_markdown or _table_to_markdown(block)
                )
                elements.append(
                    {"tag": "markdown", "content": content, "text_size": "normal"}
                )
        # Unknown block types are silently ignored.
    return elements


def _split_oversized_markdown_element(
    element: dict[str, Any],
    max_markdown_chars: int,
) -> list[dict[str, Any]]:
    """Split an oversized markdown element by UTF-8 byte budget.

    - Uses ``len(content.encode("utf-8"))`` for the size check.
    - Prefers splitting at line boundaries (``\\n``).
    - When no line boundary is available within the budget, splits at a
      UTF-8 character-safe position (never mid-multibyte).
    - Avoids splitting inside ``[...](...)`` links, `` `...` `` inline code,
      or backslash escape sequences.
    - When the content contains an open code fence (````​```​````), each
      split piece gets a closing fence and the next piece re-opens it.
    - Never produces empty chunks.
    """
    if element.get("tag") != "markdown":
        return [element]
    content = str(element.get("content") or "")
    if len(content.encode("utf-8")) <= max_markdown_chars:
        return [element]
    parts = _split_markdown_by_utf8_bytes(content, max_markdown_chars)
    return [{**element, "content": part} for part in parts if part]


def _utf8_safe_rsplit(text: str, max_bytes: int) -> tuple[str, str]:
    """Split *text* so that the left part is at most *max_bytes* in UTF-8.

    The split point is chosen at a character boundary (never mid-multibyte).
    Returns ``(left, right)``.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, ""
    # Find the largest char-boundary whose UTF-8 encoding fits in max_bytes.
    # We do this by walking back from max_bytes until we find a valid boundary.
    cut = max_bytes
    # A valid UTF-8 char boundary: the byte at *cut* must be a leading byte
    # (not a continuation byte 0x80-0xBF).
    while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
    # Now *cut* is a character boundary.  But we may have cut too aggressively
    # for the first round; the caller will handle remaining.
    left = encoded[:cut].decode("utf-8")
    right = encoded[cut:].decode("utf-8")
    return left, right


def _find_safe_split_point(text: str, max_bytes: int) -> int:
    """Find a character index to split *text* so that ``text[:idx]`` fits
    within *max_bytes* in UTF-8, preferring boundaries at ``\\n``, spaces,
    and sentence terminators.  Avoids cutting inside links, inline code,
    or escape sequences.

    Returns a character index (0-based), not byte offset.
    """
    # First, find the byte-safe maximum character length.
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return len(text)

    # Binary search for the maximum number of characters whose UTF-8
    # encoding fits within max_bytes.
    lo, hi = 0, len(text)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if len(text[:mid].encode("utf-8")) <= max_bytes:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    if best == 0:
        # Even one character fits (shouldn't happen with reasonable budgets),
        # but guard anyway.
        return 1

    # Now try to find a better boundary within text[:best].
    window = text[:best]
    # Prefer: double newline > newline > 。 > space > hard cut
    candidates = [
        window.rfind("\n\n"),
        window.rfind("\n"),
        window.rfind("。"),
        window.rfind(" "),
    ]
    split_at = max(candidates)
    if split_at < max(40, best // 4):
        # No good boundary found — use the hard character-boundary cut.
        split_at = best
    else:
        split_at += 1  # include the boundary character

    # Avoid cutting inside markdown links [text](url), inline code `...`,
    # or backslash escapes.
    split_at = _avoid_unsafe_markdown_cut(text, split_at, best)

    return split_at


def _avoid_unsafe_markdown_cut(text: str, split_at: int, hard_limit: int) -> int:
    """Adjust *split_at* to avoid breaking markdown link/inline-code/escape
    syntax.  If the split point falls inside an unsafe region, move it
    backwards to just before the unsafe region begins.
    """
    if split_at <= 0 or split_at >= len(text):
        return split_at

    # Check for backslash escape: if the character before split_at is a
    # backslash, move back.
    while split_at > 0 and text[split_at - 1] == "\\":
        split_at -= 1

    # Check for unclosed inline code: count backticks in text[:split_at].
    # If odd, the split is inside an inline code span — move back to
    # before the last opening backtick.
    prefix = text[:split_at]
    backtick_count = prefix.count("`")
    # But we need to be careful: ``` could be a code fence, not inline code.
    # Only treat single or double backticks as inline code.
    triple_count = prefix.count("```")
    single_backticks = backtick_count - triple_count * 3
    if single_backticks % 2 == 1:
        # Inside inline code — find the last lone backtick and move before it.
        idx = prefix.rfind("`")
        if idx >= 0:
            split_at = idx

    # Check for unclosed markdown link: look for `](` in the prefix
    # without a matching `)` after it.
    last_link_open = prefix.rfind("](")
    if last_link_open >= 0:
        after_open = prefix[last_link_open + 2:]
        if ")" not in after_open:
            # The link URL is being cut — move split before the `[`.
            # Find the matching `[` for this `]`.
            bracket_start = prefix.rfind("[", 0, last_link_open)
            if bracket_start >= 0:
                split_at = bracket_start

    # Final guard: don't let the adjustment push below 0 or above hard_limit.
    if split_at <= 0:
        return min(1, hard_limit)
    return min(split_at, hard_limit)


def _split_markdown_by_utf8_bytes(content: str, max_bytes: int) -> list[str]:
    """Split *content* into pieces, each at most *max_bytes* in UTF-8.

    Handles code fences: detects open ````​```lang```` fences and ensures
    each split piece has balanced fences.
    """
    parts: list[str] = []
    remaining = content

    while len(remaining.encode("utf-8")) > max_bytes:
        # Detect code fence state in the remaining text.
        fence_lang = _detect_open_code_fence(remaining)

        # Find a safe split point.
        split_at = _find_safe_split_point(remaining, max_bytes)

        if split_at <= 0:
            # Safety valve: force at least 1 character.
            split_at = 1

        part = remaining[:split_at]
        rest = remaining[split_at:]

        # Handle code fence balance.
        if fence_lang is not None:
            # The part starts inside a code block. Close it.
            part = part.rstrip("\n")
            if not part.endswith("```"):
                part += "\n```"
            parts.append(part)
            # Re-open the fence in the next part.
            rest = rest.lstrip("\n")
            rest = f"```{fence_lang}\n" + rest
        else:
            # Check if this part has an odd number of ``` — meaning
            # we opened a fence but didn't close it.
            fence_count = part.count("```")
            if fence_count % 2 == 1:
                # Extract the fence language.
                fence_match = re.search(r"```(\w*)", part)
                lang = fence_match.group(1) if fence_match else ""
                part = part.rstrip("\n")
                if not part.endswith("```"):
                    part += "\n```"
                parts.append(part)
                rest = rest.lstrip("\n")
                rest = f"```{lang}\n" + rest
            else:
                stripped = part.strip()
                if stripped:
                    parts.append(part)

        remaining = rest

    # Last chunk.
    if remaining.strip():
        parts.append(remaining)

    return parts


def _detect_open_code_fence(text: str) -> str | None:
    """If *text* starts inside a code fence (i.e., the text begins with
    the interior of a code block because a previous fence was opened but
    not closed), return the language tag.  Otherwise return None.

    This is used when we've already split off a piece that re-opened a fence.
    """
    # Count ``` occurrences: if odd, the text starts inside a code block.
    fence_count = text.count("```")
    if fence_count % 2 == 1:
        # The first ``` in the text closes the open block.
        # We want to know the language of the *opening* fence that was
        # placed by the previous split.  We handle this differently:
        # if text starts with "```lang\n", it's the re-opened fence.
        m = re.match(r"```(\w*)\n", text)
        if m:
            return m.group(1)
        return ""
    return None


def _partition_elements(
    elements: list[dict[str, Any]],
    *,
    max_elements_per_card: int,
    max_card_chars: int,
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0

    for element in elements:
        element_chars = _element_char_size(element)
        would_overflow_count = len(current) >= max_elements_per_card
        would_overflow_chars = bool(current) and current_chars + element_chars > max_card_chars
        if would_overflow_count or would_overflow_chars:
            groups.append(current)
            current = []
            current_chars = 0
        current.append(element)
        current_chars += element_chars
    if current:
        groups.append(current)
    return groups


def _element_char_size(element: dict[str, Any]) -> int:
    if element.get("tag") == "markdown":
        return len(str(element.get("content") or ""))
    return len(json.dumps(element, ensure_ascii=False))


def _build_summary(blocks: list[Any]) -> str:
    for block in blocks:
        if isinstance(block, ParagraphBlock):
            return _strip_inline_markdown(block.text)[:_PLAINTEXT_SUMMARY_MAX_LENGTH]
    for block in blocks:
        if isinstance(block, HeadingBlock):
            return _strip_inline_markdown(block.text)[:_PLAINTEXT_SUMMARY_MAX_LENGTH]
    return ""


def _strip_inline_markdown(text: str) -> str:
    """Best-effort inline Markdown removal for notification summaries."""
    return _UNDESIRED_SUMMARY_CHARS_RE.sub("", text).strip()


def _render_code_block_content(block: CodeBlock) -> str:
    code = block.code.replace("\r\n", "\n").rstrip("\n")
    return f"```{block.language}\n{code}\n```"


def _render_code_block_content_from_raw(*, language: str, code: str) -> str:
    normalized = code.replace("\r\n", "\n").rstrip("\n")
    return f"```{language}\n{normalized}\n```"


def _build_table_element(data: TableBlock, cell_type: str, row_count: int) -> dict[str, Any]:
    return {
        "tag": "table",
        "page_size": min(row_count, 10),
        "row_height": "auto",
        "row_max_height": "124px",
        "freeze_first_column": len(data.headers) > 2,
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "none",
            "text_color": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": [
            {"name": f"col_{i}", "display_name": header, "data_type": cell_type}
            for i, header in enumerate(data.headers)
        ],
        "rows": [
            {f"col_{i}": cell for i, cell in enumerate(padded_row)}
            for padded_row in (
                _fit_row(row, len(data.headers)) for row in data.rows
            )
        ],
    }


def _table_to_markdown(data: TableBlock) -> str:
    lines = ["| " + " | ".join(data.headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in data.headers) + " |")
    for row in data.rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _fit_row(row: list[str], width: int) -> list[str]:
    fitted = list(row)[:width]
    if len(fitted) < width:
        fitted.extend("" for _ in range(width - len(fitted)))
    return fitted
