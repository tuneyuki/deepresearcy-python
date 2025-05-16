# generate_feedback.py
from __future__ import annotations

import os
import asyncio
from typing import List

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

import weave

# 「o4-mini」をデフォルトに、環境変数で上書き可
LLM_MODEL = os.getenv("LLM_MODEL", "o4-mini")
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
)


# ---------------------------------------------------------------------------
# systemPrompt() 相当
# ---------------------------------------------------------------------------
def system_prompt() -> str:
    return (
        "You are an expert research assistant. "
        "Given a user's initial query you suggest concise follow-up questions "
        "that help clarify the research direction."
    )


# ---------------------------------------------------------------------------
# Pydantic スキーマ
# ---------------------------------------------------------------------------
class _FeedbackSchema(BaseModel):
    questions: List[str] = Field(
        ...,
        description="Follow-up questions to clarify the research direction",
    )


# ---------------------------------------------------------------------------
# コア関数（非同期版）
# ---------------------------------------------------------------------------
@weave.op
async def generate_feedback(
    query: str,
    num_questions: int = 3,
) -> List[str]:
    """
    初回クエリから最大 `num_questions` 件のフォローアップ質問を生成して返す。
    """

    prompt = (
        f"Given the following query from the user, ask some follow up questions "
        f"to clarify the research direction. Return a maximum of {num_questions} "
        f"questions, but feel free to return less if the original query is clear: "
        f"<query>{query}</query>"
    )

    # structured output を _FeedbackSchema で指定
    resp = client.responses.parse(
        model=LLM_MODEL,
        reasoning={"effort": "medium"},
        input=[
            {"role": "system", "content": system_prompt()},
            {"role": "user",   "content": prompt},
        ],
        text_format=_FeedbackSchema,
    )

    parsed = resp.output_parsed        # ← Pydantic で検証済み
    return parsed.questions[:num_questions]


# ---------------------------------------------------------------------------
# 同期ラッパー（Streamlit など同期環境向け）
# ---------------------------------------------------------------------------
def generate_feedback_sync(query: str, num_questions: int = 3) -> List[str]:
    return asyncio.run(generate_feedback(query, num_questions))


# ---------------------------------------------------------------------------
# スタンドアロン確認用
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    q = input("Query: ")
    print(asyncio.run(generate_feedback(q)))
