import os
import json
import asyncio
from typing import List, Optional, Callable, Dict
from pydantic import BaseModel, Field
from dataclasses import dataclass
import logging

from openai import OpenAI
from firecrawl import FirecrawlApp  # ← 公式 Python SDK
from datetime import datetime, timezone

# 「o4-mini」をデフォルトに、環境変数で上書き可
LLM_MODEL = os.getenv("LLM_MODEL", "o4-mini")

def system_prompt() -> str:
    """
    Returns a system prompt for the agent, including the current UTC timestamp.
    """
    now = datetime.now(timezone.utc).isoformat()
    return f"""You are an expert researcher. Today is {now}. Follow these instructions when responding:
- You may be asked to research subjects that are after your knowledge cutoff; assume the user is right when presented with news.
- The user is a highly experienced analyst, no need to simplify; be as detailed as possible and make sure your response is correct.
- Be highly organized.
- Suggest solutions that I didn't think about.
- Be proactive and anticipate my needs.
- Treat me as an expert in all subject matter.
- Mistakes erode my trust, so be accurate and thorough.
- Provide detailed explanations; I'm comfortable with lots of detail.
- Value good arguments over authorities; the source is irrelevant.
- Consider new technologies and contrarian ideas, not just the conventional wisdom.
- You may use high levels of speculation or prediction; just flag it for me."""


# 環境変数から API キーを取得
os.environ["OPENAI_API_KEY"] = ""
client = OpenAI()

firecrawl = FirecrawlApp(api_key=os.getenv("FIRECRAWL_KEY", ""))

@dataclass
class ResearchProgress:
    current_depth: int
    total_depth: int
    current_breadth: int
    total_breadth: int
    total_queries: int = 0
    completed_queries: int = 0
    current_query: Optional[str] = None

@dataclass
class ResearchResult:
    learnings: List[str]
    visited_urls: List[str]

# ① SERP クエリ用のスキーマ
class QueryEntry(BaseModel):
    query: str = Field(
        ...,
        description="The SERP query"
    )
    researchGoal: str = Field(
        ...,
        description=(
            "First talk about the goal of the research that this query is meant to "
            "accomplish, then go deeper into how to advance the research once the "
            "results are found, mention additional research directions. Be as specific "
            "as possible, especially for additional research directions."
        )
    )

class QueryList(BaseModel):
    queries: List[QueryEntry] = Field(
        ...,
        description="List of SERP queries, max of the requested number"
    )

# ② process_serp_result 用のスキーマ
class ProcResponse(BaseModel):
    learnings: List[str] = Field(
        ...,
        description="List of learnings, max of the requested number"
    )
    followUpQuestions: List[str] = Field(
        ...,
        description="List of follow-up questions to research the topic further, max of the requested number"
    )

# ③ 最終レポート用のスキーマ
class FinalReport(BaseModel):
    reportMarkdown: str = Field(..., description="Final report on the topic in Markdown")

class FinalAnswer(BaseModel):
    exactAnswer: str = Field(..., description="The final answer, make it short and concise, just the answer, no other text")

async def generate_serp_queries(
    query: str,
    num_queries: int = 3,
    learnings: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    
    prompt = (
        f"Given the following prompt from the user, generate a list of SERP queries to research the topic.\n"
        f"Return a maximum of {num_queries} queries, but feel free to return less if the original prompt is clear.\n"
        f"Make sure each query is unique and not similar to each other: <prompt>{query}</prompt>\n\n"
    )
    if learnings:
        prompt += (
            "Here are some learnings from previous research, use them to generate more specific queries:\n"
            + "\n".join(learnings)
        )

    resp = client.responses.parse(
        model=LLM_MODEL,
        reasoning={"effort": "medium"},
        input=[
            {"role": "system", "content": system_prompt()},
            {"role": "user",   "content": prompt},
        ],
        text_format=QueryList,
    )
    parsed = resp.output_parsed
    # print("----- RAW generate_serp_queries START -----")
    # print(parsed)
    # print("----- RAW generate_serp_queries  END  -----")

    return [entry.model_dump() for entry in parsed.queries][:num_queries]

async def process_serp_result(
    query: str,
    search_result,
    num_learnings: int = 3,
    num_follow_up: int = 3,
) -> Dict[str, List[str]]:
    # item は dict なので ['description'] で取得
    contents = [item["description"] for item in search_result.data]
    # 各コンテンツを <content> タグでラップ
    wrapped = "\n".join(f"<content>\n{c}\n</content>" for c in contents)

    # Build the prompt exactly as in the original, using an f-string
    prompt = (
        f"Given the following contents from a SERP search for the query <query>{query}</query>, "
        f"generate a list of learnings from the contents. Return a maximum of {num_learnings} "
        "learnings, but feel free to return less if the contents are clear. Make sure each learning "
        "is unique and not similar to each other. The learnings should be concise and to the point, "
        "as detailed and information dense as possible. Make sure to include any entities like people, "
        "places, companies, products, things, etc in the learnings, as well as any exact metrics, "
        "numbers, or dates. The learnings will be used to research the topic further.\n\n"
        f"<contents>\n{wrapped}\n</contents>"
    )

    # structured output を Pydantic モデルで指定
    resp = client.responses.parse(
        model=LLM_MODEL,
        reasoning={"effort": "medium"},
        input=[
            {"role": "system", "content": system_prompt()},
            {"role": "user",   "content": prompt},
        ],
        text_format=ProcResponse,
    )

    # 解析済み結果を取得
    parsed = resp.output_parsed
    # print("----- RAW process_serp_result START -----")
    # print(parsed)
    # print("----- RAW process_serp_result  END  -----")

    return {
        "learnings": parsed.learnings,
        "followUpQuestions": parsed.followUpQuestions,
    }

# 最終レポート作成
async def write_final_report(
    prompt: str,
    learnings: List[str],
    visited_urls: List[str],
) -> str:
    learnings_str = "\n".join(f"<learning>\n{l}\n</learning>" for l in learnings)
    user_message = (
        f"Given the following prompt from the user, write a final report on the topic using the learnings from research. Make it as detailed as possible, aim for 3 or more pages, include ALL the learnings from research:\n\n"
        f"<prompt>{prompt}</prompt>\n\n"
        f"<learnings>\n{learnings_str}\n</learnings>"
    )
    resp = client.responses.parse(
        model=LLM_MODEL,
        reasoning={"effort": "medium"},
        input=[
            {"role": "system", "content": system_prompt()},
            {"role": "user",   "content": user_message},
        ],
        text_format=FinalReport,
    )
    parsed = resp.output_parsed
    urls_section = "\n\n## Sources\n\n" + "\n".join(f"- {u}" for u in visited_urls)
    return parsed.reportMarkdown + urls_section

# 最終回答作成
async def write_final_answer(
    prompt: str,
    learnings: List[str],
) -> str:
    learnings_str = "\n".join(f"<learning>\n{l}\n</learning>" for l in learnings)
    user_message = (
        f"Given the following prompt from the user, write a final answer on the topic using the learnings from research. Follow the format specified in the prompt. Do not yap or babble or include any other text than the answer besides the format specified in the prompt. Keep the answer as concise as possible - usually it should be just a few words or maximum a sentence. Try to follow the format specified in the prompt.\n\n"
        f"<prompt>{prompt}</prompt>\n\n"
        f"<learnings>\n{learnings_str}\n</learnings>"
    )
    resp = client.responses.parse(
        model=LLM_MODEL,
        reasoning={"effort": "medium"},
        input=[
            {"role": "system", "content": system_prompt()},
            {"role": "user",   "content": user_message},
        ],
        text_format=FinalAnswer,
    )
    parsed = resp.output_parsed
    return parsed.exactAnswer



async def deep_research(
    query: str,
    breadth: int,
    depth: int,
    learnings: Optional[List[str]] = None,
    visited_urls: Optional[List[str]] = None,
    on_progress: Optional[Callable[[ResearchProgress], None]] = None,
) -> ResearchResult:
    learnings = learnings or []
    visited_urls = visited_urls or []
    progress = ResearchProgress(depth, depth, breadth, breadth)

    # SERP クエリ生成
    serp_queries = await generate_serp_queries(query, breadth, learnings)
    progress.total_queries = len(serp_queries)
    progress.current_query = serp_queries[0]["query"] if serp_queries else None
    if on_progress: on_progress(progress)

    async def handle_one(serp: Dict[str, str]) -> ResearchResult:
        # *同期* search をそのまま呼ぶ
        search_result = firecrawl.search(serp["query"], limit=breadth)

        new_urls = [item["url"] for item in search_result.data]
        proc = await process_serp_result(
            serp["query"], search_result, num_learnings=breadth, num_follow_up=breadth // 2
        )

        all_learnings = learnings + proc["learnings"]
        all_urls = visited_urls + new_urls

        if depth - 1 > 0:
            next_query = (
                f"Previous research goal: {serp.get('researchGoal')}\n"
                + "\n".join(f"- {q}" for q in proc["followUpQuestions"])
            )
            progress.completed_queries += 1
            progress.current_depth = depth - 1
            progress.current_breadth = breadth // 2
            if on_progress: on_progress(progress)

            return await deep_research(
                next_query,
                breadth=breadth // 2,
                depth=depth - 1,
                learnings=all_learnings,
                visited_urls=all_urls,
                on_progress=on_progress,
            )
        else:
            progress.completed_queries += 1
            progress.current_depth = 0
            if on_progress: on_progress(progress)
            return ResearchResult(all_learnings, all_urls)

    # 並列に走らせたい場合は asyncio.to_thread を噛ませても OK
    results = await asyncio.gather(*(handle_one(q) for q in serp_queries))

    # 重複排除してマージ
    final_learnings = list({l for r in results for l in r.learnings})
    final_urls      = list({u for r in results for u in r.visited_urls})
    return ResearchResult(final_learnings, final_urls)


# CLIエントリーポイント
if __name__ == "__main__":
    async def main():
        initial_query = input("What would you like to research? ")
        breadth = int(input("Enter research breadth (recommended 2-10, default 4): ") or 4)
        depth = int(input("Enter research depth (recommended 1-5, default 2): ") or 2)
        report_flag = input("Generate long report or specific answer? (report/answer, default report): ").lower()
        is_report = report_flag != "answer"

        result = await deep_research(
            initial_query, breadth=breadth, depth=depth,
            on_progress=lambda p: print(f"{p.completed_queries}/{p.total_queries} done, depth {p.current_depth}")
        )

        if is_report:
            report = await write_final_report(initial_query, result.learnings, result.visited_urls)
            print("\n=== FINAL REPORT ===\n", report)
        else:
            answer = await write_final_answer(initial_query, result.learnings)
            print("\n=== FINAL ANSWER ===\n", answer)

    asyncio.run(main())