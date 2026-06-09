#!/usr/bin/env python3
"""Generate and validate a realistic Feishu final-response card sample."""

from __future__ import annotations

import json
from pathlib import Path

from gateway.platforms.feishu_card_renderer import build_feishu_card_v2_payload

SAMPLE_REPLY = """# Hermes 飞书最终回复卡片 Smoke

结论：**离线渲染通过**。这条样例覆盖段落、表格、代码块和分割线。

## 关键检查

| 检查项 | 状态 | 备注 |
| --- | --- | --- |
| Markdown parser | pass | 输出中间 Document |
| Feishu card v2 renderer | pass | 输出 interactive payload |
| MEDIA 保护 | pass | auto 模式遇到附件保持 legacy |

```python
from gateway.platforms.feishu_card_renderer import build_feishu_card_v2_payload
payload = build_feishu_card_v2_payload(markdown)
```

---

下一步才是切换 `final_response_format: auto` 后做真实 Feishu 发送 smoke。
"""


def validate_card(card: dict) -> None:
    assert card.get("schema") == "2.0", "card schema must be 2.0"
    assert card.get("config", {}).get("width_mode") == "fill", "card width_mode must be fill"
    assert isinstance(card.get("body", {}).get("elements"), list), "card body.elements must be list"
    elements = card["body"]["elements"]
    assert elements, "card must contain body elements"
    assert any(e.get("tag") == "table" for e in elements), "sample must contain table element"
    assert any(
        e.get("tag") == "markdown" and str(e.get("content", "")).startswith("```python")
        for e in elements
    ), "sample must contain python code block"
    assert any(e.get("tag") == "hr" for e in elements), "sample must contain divider"


def main() -> None:
    out = Path("/tmp/hermes-feishu-final-card-sample.json")
    payload = build_feishu_card_v2_payload(SAMPLE_REPLY, table_policy="table")
    card = json.loads(payload)
    validate_card(card)
    out.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(out), "elements": len(card["body"]["elements"]), "summary": card["config"]["summary"]["content"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
