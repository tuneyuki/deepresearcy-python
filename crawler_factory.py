import os
from typing import Protocol, Dict, Any
from types import SimpleNamespace
from openai import OpenAI

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


class OpenAISearchApp:
    """OpenAI の web_search_preview ツールを使うラッパー"""

    def __init__(self, api_key: str, model: str = "gpt-4.1"):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not set")
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def search(self, query: str, limit: int = 10, **kwargs):
        resp = self.client.responses.create(
            model=self.model,
            tools=[{"type": "web_search_preview"}],
            input=query,
        )
        # ① メッセージ要素を属性でフィルタ
        msg = next(o for o in resp.output if getattr(o, "type", None) == "message")
        # ② content[0] に .text, .annotations が詰まっている
        first = msg.content[0]
        text = first.text
        anns = getattr(first, "annotations", [])
        # ③ url_citation をマップ
        items = []
        for ann in anns:
            if getattr(ann, "type", None) == "url_citation":
                items.append({
                    "title": getattr(ann, "title", ""),
                    "description": text,
                    "url": getattr(ann, "url", ""),
                })
                if len(items) >= limit:
                    break
        return SimpleNamespace(data=items)


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

    if provider == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise ValueError("OPENAI_API_KEY が設定されていません")
        return OpenAISearchApp(api_key=key)


    raise ValueError(f"Unsupported SEARCH_PROVIDER: {provider}")
