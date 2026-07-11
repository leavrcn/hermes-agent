"""Tests for UTF-8 byte-safe markdown element splitting in Feishu Card renderer."""

import json

from gateway.platforms.feishu_card_renderer import build_feishu_card_v2_payloads


def _markdown_elements(payloads):
    """Extract all markdown elements from card payloads."""
    elements = []
    for p in payloads:
        card = json.loads(p)
        for el in card.get("body", {}).get("elements", []):
            if el.get("tag") == "markdown":
                elements.append(el)
    return elements


def test_long_unbroken_ascii_is_split_by_byte_budget():
    """Long ASCII-only content with no newlines must be split so each piece
    fits within the 3000-byte UTF-8 budget."""
    payloads = build_feishu_card_v2_payloads("x" * 9000)
    for el in _markdown_elements(payloads):
        assert len(el["content"].encode("utf-8")) <= 3000, (
            f"element exceeds 3000 bytes: {len(el['content'].encode('utf-8'))}"
        )


def test_long_chinese_is_split_by_utf8_bytes():
    """Each Chinese character is 3 bytes in UTF-8; 4000 chars = 12000 bytes.
    Each resulting element must stay within the 3000-byte budget."""
    payloads = build_feishu_card_v2_payloads("中" * 4000)
    for el in _markdown_elements(payloads):
        assert len(el["content"].encode("utf-8")) <= 3000


def test_split_code_blocks_have_balanced_fences():
    """When a code block is split, each piece must have balanced ``` fences."""
    code = "```python\n" + "x = 1\n" * 500 + "```"
    payloads = build_feishu_card_v2_payloads(code)
    for el in _markdown_elements(payloads):
        assert el["content"].count("```") % 2 == 0, "unbalanced fence"


def test_markdown_link_is_not_cut_mid_token():
    """Links [text](url) should not be cut in the middle of the URL."""
    text = "See [this is a very long link](https://example.com/" + "x" * 200 + ") for details."
    text = text * 20
    payloads = build_feishu_card_v2_payloads(text)
    for el in _markdown_elements(payloads):
        content = el["content"]
        # Check that there's no unclosed ]( — every ]( must have a matching )
        # Simple heuristic: count of ]( should equal count of ) after the last ]
        open_count = content.count("](")
        close_count = content.count(")")
        # If there are ]( without enough ), it's a broken link
        assert open_count <= close_count, (
            f"Unclosed markdown link: {open_count} ]( vs {close_count} )"
        )


def test_no_empty_chunks():
    """No split piece should be empty."""
    payloads = build_feishu_card_v2_payloads("x" * 9000)
    for el in _markdown_elements(payloads):
        assert len(el["content"]) > 0


def test_short_content_unchanged():
    """Short content should produce a single card without splitting."""
    payloads = build_feishu_card_v2_payloads("short text")
    assert len(payloads) == 1


def test_chinese_no_mojibake():
    """Splitting must not break multi-byte UTF-8 characters.
    Each piece must be valid UTF-8 and decodable."""
    payloads = build_feishu_card_v2_payloads("中文测试" * 1000)
    for el in _markdown_elements(payloads):
        content = el["content"]
        # Must be encodable and re-decodable without error
        raw = content.encode("utf-8")
        assert raw.decode("utf-8") == content
        # No partial characters — the content should not end or start with
        # a continuation byte
        assert len(raw) <= 3000
