"""SearXNG search — plugin form.

Subclasses :class:`agent.web_search_provider.WebSearchProvider`. Same JSON
API call (``/search?format=json``), same result normalization. The legacy
in-tree module ``tools.web_providers.searxng`` was removed in the same
commit that moved this code under ``plugins/``; this file is now the
canonical implementation.

Search-only — SearXNG aggregates results from upstream engines but does not
fetch/extract arbitrary URLs. ``supports_extract()`` returns False.

Config keys this provider responds to::

    web:
      search_backend: "searxng"     # explicit per-capability
      backend: "searxng"            # shared fallback

Env var::

    SEARXNG_URL=http://localhost:8080
"""

from __future__ import annotations
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Tuple

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)


_SPLIT_SEARCH_TERMS = (
    # finance / markets
    "a股", "港股", "美股", "股票", "基金", "etf", "财报", "业绩", "营收", "利润",
    "回购", "增持", "板块", "龙头", "汇率", "人民币", "美元", "美联储", "cpi",
    "利率", "降息", "通胀", "油价", "黄金", "航运", "出口", "进口",
    # policy / industry / event-style queries
    "政策", "发布", "量产", "进展", "价格", "产业", "新能源", "芯片", "ai",
    "机器人", "固态电池", "影响", "预期", "热点", "新闻", "最新", "今年",
    # global / geopolitics
    "红海", "中东", "伊朗", "美国", "欧洲", "地缘",
)


def _should_split_general_news(query: str) -> bool:
    """Return True for queries that benefit from both general and news search."""
    q = (query or "").lower()
    return any(term in q for term in _SPLIT_SEARCH_TERMS)


def _normalize_url_for_dedupe(url: str) -> str:
    """Normalize only enough to dedupe obviously identical SearXNG hits."""
    url = (url or "").strip()
    if not url:
        return ""
    url = re.sub(r"#.*$", "", url)
    return url.rstrip("/")


def _result_score(raw: Dict[str, Any], category: str) -> float:
    try:
        score = float(raw.get("score", 0) or 0)
    except (TypeError, ValueError):
        score = 0.0
    if category == "news":
        score += 0.15
    return score


def _merge_ranked_results(category_results: Iterable[Tuple[str, List[Dict[str, Any]]]]) -> List[Dict[str, Any]]:
    """Merge category result lists, dedupe by URL, and sort by adjusted score."""
    merged: Dict[str, Dict[str, Any]] = {}
    for category, results in category_results:
        for raw in results:
            key = _normalize_url_for_dedupe(str(raw.get("url", ""))) or str(raw.get("title", ""))
            candidate = dict(raw)
            candidate["_source_category"] = category
            candidate["_adjusted_score"] = _result_score(raw, category)
            existing = merged.get(key)
            if existing is None or candidate["_adjusted_score"] > existing.get("_adjusted_score", 0):
                merged[key] = candidate
    return sorted(
        merged.values(),
        key=lambda r: float(r.get("_adjusted_score", 0)),
        reverse=True,
    )


def _searxng_url() -> str:
    """Return SEARXNG_URL from Hermes config-aware env, falling back to process env."""
    try:
        from hermes_cli.config import get_env_value

        val = get_env_value("SEARXNG_URL")
    except Exception:
        val = None
    if val is None:
        val = os.getenv("SEARXNG_URL", "")
    return (val or "").strip()


class SearXNGWebSearchProvider(WebSearchProvider):
    """Search via a user-hosted SearXNG instance."""

    @property
    def name(self) -> str:
        return "searxng"

    @property
    def display_name(self) -> str:
        return "SearXNG"

    def is_available(self) -> bool:
        """Return True when ``SEARXNG_URL`` is set."""
        return bool(_searxng_url())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def _request_search(self, base_url: str, query: str, category: str | None = None) -> Tuple[List[Dict[str, Any]], int]:
        """Request one SearXNG category and return raw results + raw count."""
        import httpx

        params: Dict[str, Any] = {
            "q": query,
            "format": "json",
            "pageno": 1,
        }
        if category:
            params["categories"] = category

        resp = httpx.get(
            f"{base_url}/search",
            params=params,
            timeout=15,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raw_results = []
        return raw_results, len(raw_results)

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute a search against the configured SearXNG instance."""
        import httpx

        base_url = _searxng_url().rstrip("/")
        if not base_url:
            return {"success": False, "error": "SEARXNG_URL is not set"}

        categories = ["general", "news"] if _should_split_general_news(query) else [None]
        category_results: List[Tuple[str, List[Dict[str, Any]]]] = []
        raw_count = 0

        try:
            for category in categories:
                results, count = self._request_search(base_url, query, category)
                raw_count += count
                category_results.append((category or "default", results))
        except httpx.HTTPStatusError as exc:
            logger.warning("SearXNG HTTP error: %s", exc)
            return {
                "success": False,
                "error": f"SearXNG returned HTTP {exc.response.status_code}",
            }
        except httpx.RequestError as exc:
            logger.warning("SearXNG request error: %s", exc)
            return {
                "success": False,
                "error": f"Could not reach SearXNG at {base_url}: {exc}",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("SearXNG response parse error: %s", exc)
            return {
                "success": False,
                "error": "Could not parse SearXNG response as JSON",
            }

        sorted_results = _merge_ranked_results(category_results)[:limit]

        web_results = [
            {
                "title": str(r.get("title", "")),
                "url": str(r.get("url", "")),
                "description": str(r.get("content", "")),
                "position": i + 1,
            }
            for i, r in enumerate(sorted_results)
        ]

        logger.info(
            "SearXNG search '%s': %d results (from %d raw across %s, limit %d)",
            query,
            len(web_results),
            raw_count,
            ",".join(category or "default" for category in categories),
            limit,
        )

        return {"success": True, "data": {"web": web_results}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "SearXNG",
            "badge": "free · self-hosted",
            "tag": "Free, privacy-respecting metasearch. Point SEARXNG_URL at your instance.",
            "env_vars": [
                {
                    "key": "SEARXNG_URL",
                    "prompt": "SearXNG instance URL (e.g. http://localhost:8080)",
                    "url": "https://searx.space/",
                },
            ],
        }
