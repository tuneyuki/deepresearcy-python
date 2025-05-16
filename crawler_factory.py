import os
from typing import Protocol, Dict, Any
from types import SimpleNamespace

# ─────────── Firecrawl SDK ───────────
try:
    from firecrawl import FirecrawlApp as _FirecrawlSDK
except ModuleNotFoundError:
    _FirecrawlSDK = None  # Firecrawl 未インストール環境でも OK

# ─────────── Tavily SDK ───────────
try:
    from tavily import TavilyClient as _TavilySDK
except ModuleNotFoundError:
    _TavilySDK = None     # 同様に未インストールでも OK


class TavilyApp:
    """Tavily SDK を Firecrawl と同じインターフェースで扱う薄いラッパー。"""

    def __init__(self, api_key: str):
        if _TavilySDK is None:
            raise ImportError("Tavily SDK (tavily-python) がインストールされていません")
        if not api_key:
            raise ValueError("TAVILY_API_KEY is not set")
        self._client = _TavilySDK(api_key)       # ← _TavilySDK を使用

    def search(self, query: str, limit: int = 10, **kwargs):
        # SDK 呼び出しはそのまま
        response: Dict[str, Any] = self._client.search(
            query=query,
            max_results=limit,
            topic=kwargs.get("topic", "general"),
            search_depth=kwargs.get("search_depth", "basic"),
            days=kwargs.get("days", 30),
            include_answer=False,
            include_raw_content=False,
            include_images=False,
        )

        def _map(item: Dict[str, Any]) -> Dict[str, str]:
            return {
                "title": item.get("title", ""),
                "description": item.get("content", ""),
                "url": item.get("url", ""),
            }

        return SimpleNamespace(data=[_map(r) for r in response.get("results", [])[:limit]])


# ─────────── インターフェース (Protocol) ───────────
class Crawler(Protocol):
    def search(self, query: str, limit: int = 10, **kwargs): ...


# ─────────── ファクトリ関数 ───────────
def get_crawler() -> Crawler:
    provider = os.getenv("SEARCH_PROVIDER", "firecrawl").lower()

    if provider == "firecrawl":
        if _FirecrawlSDK is None:
            raise ImportError("Firecrawl SDK がインストールされていません")
        key = os.getenv("FIRECRAWL_KEY")
        if not key:
            raise ValueError("FIRECRAWL_KEY が設定されていません")
        return _FirecrawlSDK(api_key=key)

    if provider == "tavily":
        if _TavilySDK is None:
            raise ImportError("Tavily SDK がインストールされていません")
        key = os.getenv("TAVILY_API_KEY")
        return TavilyApp(api_key=key)

    raise ValueError(f"Unsupported SEARCH_PROVIDER: {provider}")
