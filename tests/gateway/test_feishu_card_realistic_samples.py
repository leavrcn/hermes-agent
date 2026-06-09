"""Offline quality checks for realistic Feishu final reply cards."""

import json

from gateway.platforms.feishu_card_renderer import build_feishu_card_v2_payload


MIXED_FINAL_REPLY = """# 执行结果

结论：**已完成**，但保留 `legacy` 配置，未重启 gateway。

## 验收

| 项目 | 结果 | 说明 |
| --- | --- | --- |
| parser | pass | 支持标题、段落、表格 |
| renderer | pass | 输出 card v2 JSON |
| runtime | pass | final marker 才触发 |

```bash
python -m pytest tests/gateway/test_feishu_final_card_runtime.py -q -o 'addopts='
```

---

下一步：切到 `auto` 后做真实 smoke。
"""


def test_realistic_mixed_final_reply_card_contains_ordered_blocks():
    payload = build_feishu_card_v2_payload(MIXED_FINAL_REPLY, table_policy="table")
    card = json.loads(payload)

    assert card["schema"] == "2.0"
    assert card["config"]["width_mode"] == "fill"
    assert card["config"]["summary"]["content"].startswith("结论：已完成")

    elements = card["body"]["elements"]
    tags = [element["tag"] for element in elements]
    assert tags == ["markdown", "markdown", "markdown", "table", "markdown", "hr", "markdown"]
    assert elements[0]["text_size"] == "heading"
    assert elements[0]["content"] == "执行结果"
    assert elements[3]["columns"] == [
        {"name": "col_0", "display_name": "项目", "data_type": "markdown"},
        {"name": "col_1", "display_name": "结果", "data_type": "markdown"},
        {"name": "col_2", "display_name": "说明", "data_type": "markdown"},
    ]
    assert elements[4]["content"].startswith("```bash\npython -m pytest")


def test_realistic_mixed_final_reply_can_degrade_table_to_markdown_codeblock():
    payload = build_feishu_card_v2_payload(MIXED_FINAL_REPLY, table_policy="markdown")
    card = json.loads(payload)

    elements = card["body"]["elements"]
    assert not any(element.get("tag") == "table" for element in elements)
    assert any(
        element.get("tag") == "markdown"
        and element.get("content", "").startswith("```markdown\n| 项目 | 结果 | 说明 |")
        for element in elements
    )
