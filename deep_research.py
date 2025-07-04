import os
import json
import asyncio
from typing import List, Optional, Callable, Dict
from pydantic import BaseModel, Field
from dataclasses import dataclass, field

from openai import OpenAI
from crawler_factory import get_crawler
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

import logging

# Azure App ServiceではINFOレベル以上を推奨
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("app")  # 名前は任意
# ---------------------------------------------------------------------------
# 「o3s」をデフォルトに、環境変数で上書き可
LLM_MODEL = os.getenv("LLM_MODEL", "o3")
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
)
web_crawler = get_crawler()


def system_prompt() -> str:
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
- You may use high levels of speculation or prediction; just flag it for me.

IMPORTANT:
- When writing a final report, you must use **only** the provided <learnings> content.
- Do not add any information that is not explicitly present in the <learnings> list.
- Do not use prior knowledge, outside facts, assumptions, or speculation in final reports.
- All claims, arguments, and statements in the report must be directly traceable to the provided learnings.
"""



@dataclass
class ResearchProgress:
    current_depth: int
    total_depth: int
    current_breadth: int
    total_breadth: int
    total_queries: int = 0
    completed_queries: int = 0
    current_query: Optional[str] = None
    new_learnings: list[str] = field(default_factory=list)

@dataclass
class ResearchResult:
    learnings: List[str]
    visited_urls: List[str]

# ① SERP クエリ用のスキーマ
class QueryEntry(BaseModel):
    query: str = Field(
        ...,
        # description="The search query"
        description = (
            "State the search request as ONE complete sentence, starting with a subject and verb "
            "if appropriate, but omit personal pronouns (e.g., 'I') and phrases like 'search for' or 'look for'. "
            "The result should read naturally as a search query or document title. "
            "Write in the SAME language as the user's question."
        )

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
        description="List of search queries, max of the requested number"
    )

# ② process_serp_result 用のスキーマ
class ProcResponse(BaseModel):
    learnings: List[str] = Field(
        ...,
        description=(
            "Detailed and self-contained insights extracted from the search contents. "
            "Each learning should be as informative as possible, include context, names, dates, numbers, and ideally be understandable on its own."
        )
    )
    followUpQuestions: List[str] = Field(
        ...,
        description="List of follow-up questions to research the topic further, max of the requested number."
    )

# ③ 最終レポート用のスキーマ
class FinalReport(BaseModel):
    reportMarkdown: str = Field(..., description="Final report on the topic in Markdown")

class FinalAnswer(BaseModel):
    exactAnswer: str = Field(..., description="The final answer, make it short and concise, just the answer, no other text")


# ④ フォローアップ質問のスキーマ
class FollowUpQuestion(BaseModel):
    questions: List[str] = Field(
        ...,
        description="Follow-up questions to clarify the research direction",
    )


class LLMJudgement(BaseModel):
    followup_required: bool = Field(
        ...,
        description="Whether follow-up research is needed based on the query and learnings"
    )

# ---------------------------------------------------------------------------
# generate_followup
# ---------------------------------------------------------------------------
async def generate_followup(
    query: str,
    num_questions: int = 3,
) -> List[str]:
    """
    初回クエリから最大 `num_questions` 件のフォローアップ質問を生成して返す。
    """
    fowllowup_system_prompt = (
        "You are an expert research assistant. "
        "Given a user's initial query you suggest concise follow-up questions "
        "that help clarify the research direction."
    )

    prompt = (
        f"Given the following query from the user, ask some follow up questions "
        f"to clarify the research direction. Return a maximum of {num_questions} "
        f"questions, but feel free to return less if the original query is clear: "
        f"\n{query}"
    )

    # structured output を _FeedbackSchema で指定
    resp = client.responses.parse(
        model=LLM_MODEL,
        reasoning={"effort": "medium"},
        input=[
            {"role": "system", "content": fowllowup_system_prompt},
            {"role": "user",   "content": prompt},
        ],
        text_format=FollowUpQuestion,
    )

    parsed = resp.output_parsed        # ← Pydantic で検証済み
    return parsed.questions[:num_questions]

# ---------------------------------------------------------------------------
# generate_followup 同期ラッパー（Streamlit など同期環境向け）
# ---------------------------------------------------------------------------
def generate_followup_sync(query: str, num_questions: int = 3) -> List[str]:
    return asyncio.run(generate_followup(query, num_questions))


async def generate_serp_queries(
    query: str,
    num_queries: int = 3,
    learnings: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    
    prompt = (
        f"ユーザーからの以下のプロンプトに基づき、このトピックを調査するための検索クエリをリストアップして。\n"
        f"最大で{num_queries}個までクエリを返して。 ただし、元のプロンプトが明確な場合は、それより少なくても構わない。\n"
        f"それぞれのクエリがユニークで、互いに類似しないようにして: <prompt>{query}</prompt>\n\n"
    )
    if learnings:
        prompt += (
            "以前の調査から得られたlearningsを以下に示します。これらを参考に、learningsと重複しない検索クエリを作成して:\n"
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
) -> Dict[str, List[str]]:
    # item は dict なので ['description'] で取得
    contents = [item["description"] for item in search_result.data]
    # 各コンテンツを <content> タグでラップ
    wrapped = "\n".join(f"<content>\n{c}\n</content>" for c in contents)

    # Build the prompt exactly as in the original, using an f-string
    prompt = (
        f"Given the following contents from a SERP search for the query <query>{query}</query>, "
        f"generate a list of learnings from the contents. Return a maximum of {num_learnings} "
        "learnings, but feel free to return less if the contents are clear.\n\n"
        "Each learning should:\n"
        "- Be unique and non-overlapping\n"
        "- Be detailed and explanatory, not just short summaries\n"
        "- Include context such as who/what/when/why/how\n"
        "- Mention relevant entities (e.g., people, organizations, events)\n"
        "- Include metrics, dates, or quotes where relevant\n"
        "- Be self-contained so it makes sense without reading the source\n\n"
        "The output will help guide deeper research and should be as informative as possible.\n\n"
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
    # debug
    # print(json.dumps(resp.model_dump(), indent=2, ensure_ascii=False))\

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
        f"Based on the following user prompt, write a final report using ONLY the information provided in the <learnings> tags. "
        "Do NOT add any additional information, assumptions, or external knowledge. "
        "However, if the learnings are fragmented or incomplete, you may connect them logically to create a coherent structure. "
        "Organize the report with a clear and consistent structure, including appropriate sections such as Introduction, Background, Summary of Findings, Discussion, and Conclusion. "
        "Where relevant, merge similar points or clarify relationships such as chronology or causality. "
        "Do not generalize beyond what is explicitly stated.\n\n"
        f"<prompt>\n{prompt}\n</prompt>\n\n"
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
    # debug
    # print(json.dumps(resp.model_dump(), indent=2, ensure_ascii=False))

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
        f"Given the following prompt from the user, write a final answer on the topic using the learnings from research. "
        "Write a final report with prompt language. Follow the format specified in the prompt. "
        "Do not yap or babble or include any other text than the answer besides the format specified in the prompt. "
        "Keep the answer as concise as possible - usually it should be just a few words or maximum a sentence. "
        "Try to follow the format specified in the prompt.\n\n"
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
    # debug
    # print(json.dumps(resp.model_dump(), indent=2, ensure_ascii=False))

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
    """
    再帰的にウェブリサーチを実行し、「幅 (breadth) × 深さ (depth)」で検索範囲を
    コントロールする関数。

    パラメータ
    ----------
    query : str
        初期の検索クエリ（ユーザープロンプト）。
    breadth : int
        幅。各深さレイヤーで生成する SERP クエリの本数。
        深さが進むごとに `breadth // 2` に縮小し、爆発的な分岐を防ぐ。
    depth : int
        深さ。1 なら 1 レイヤーのみ、2 ならさらにその下のレイヤーへ再帰する。
    learnings : list[str] | None
        これまでに得た知見の蓄積。再帰で下層へ渡し、最後に重複除去して返す。
    visited_urls : list[str] | None
        既にクロールした URL の集合。最終結果用なので途中処理では使わない。
    on_progress : Callable[[ResearchProgress], None] | None
        進捗を通知するコールバック。呼び出しタイミングは下記 3 つ。  
        1. 最初の SERP クエリを生成した直後  
        2. 各クエリを処理し「新しい learnings」が得られた直後  
        3. 深さ・幅を更新した直後  
        `ResearchProgress.new_learnings` に **今回増えた分だけ** が入るので、
        フロントエンドでリアルタイムに追加表示できる。

    戻り値
    ------
    ResearchResult
        - `learnings` : 最終的な知見（重複除去済み）  
        - `visited_urls` : 参照した全 URL（重複除去済み）

    処理フロー
    ----------
    1. `generate_serp_queries()` で次に検索すべきキーワードを LLM で生成  
    2. 各キーワードについて:  
       2-a. `web_crawler.search()` でページをクロール  
       2-b. `process_serp_result()` で知見と次の調査質問を抽出  
       2-c. `on_progress()` に新しい知見を通知  
    3. `depth > 1` の場合は、質問リストをまとめた新しいクエリを作り再帰呼び出し  
       （breadth を半減、depth を 1 減らす）  
    4. 末端まで到達したら各レイヤーから `ResearchResult` を集約し、  
       URL と知見をそれぞれ集合演算で重複排除して返す。

    備考
    ----
    - 同一レイヤーの SERP クエリは `asyncio.gather()` で並列実行し高速化。  
    - 再帰は逐次進むため、深さ方向の制御はシンプル。  
    - `on_progress` を使えば Streamlit などで進捗バーと知見ログを
      同期的に更新できる。
    """

    learnings = learnings or []
    visited_urls = visited_urls or []

    # Progress オブジェクト生成
    progress = ResearchProgress(
        current_depth=depth,
        total_depth=depth,
        current_breadth=breadth,
        total_breadth=breadth,
    )

    # ① 最初の SERP クエリ生成
    serp_queries = await generate_serp_queries(query, breadth, learnings)
    progress.total_queries = len(serp_queries)
    progress.current_query = serp_queries[0]["query"] if serp_queries else None

    # ── コールバック：new_learnings はまだ空 ──
    if on_progress:
        on_progress(progress)

    # ② 各クエリを処理する補助コルーチン ----------------
    async def handle_one(serp: Dict[str, str]) -> ResearchResult:
        # web_crawler 検索（同期 API）
        process_id = os.getpid()
        query_str = serp["query"]
        logger.info(
            f"SEARCH QUERY: '{query_str}' | pid={process_id}"
        )
        search_result = web_crawler.search(serp["query"], limit=breadth)

        new_urls = [item["url"] for item in search_result.data]

        # SERP 結果を解析し、learnings と follow-up 質問を抽出
        proc = await process_serp_result(
            serp["query"],
            search_result,
            num_learnings=breadth,
        )

        # 最新 learnings を progress にセット → UI へ即通知
        progress.new_learnings = proc["learnings"]
        if on_progress:
            on_progress(progress)

        # 累積
        all_learnings = learnings + proc["learnings"]
        all_urls = visited_urls + new_urls

        # ---------- 深さが残っている場合は再帰 ----------
        if depth - 1 > 0:
            next_query = (
                f"Previous research goal: {serp.get('researchGoal')}\n"
                + "\n".join(f"- {q}" for q in proc["followUpQuestions"])
            )

            # 進捗更新
            progress.completed_queries += 1
            progress.current_depth = depth - 1
            progress.current_breadth = breadth // 2
            # 直後の再帰で new_learnings が再度上書きされるので、一旦空に
            progress.new_learnings = []

            if on_progress:
                on_progress(progress)

            return await deep_research(
                next_query,
                breadth=breadth // 2,
                depth=depth - 1,
                learnings=all_learnings,
                visited_urls=all_urls,
                on_progress=on_progress,
            )

        # ---------- 末端ノード ----------
        progress.completed_queries += 1
        progress.current_depth = 0
        progress.new_learnings = []        # 最後は空にしておく
        if on_progress:
            on_progress(progress)

        return ResearchResult(all_learnings, all_urls)

    # ③ すべての SERP クエリを並列実行
    results = await asyncio.gather(*(handle_one(q) for q in serp_queries))

    # ④ 重複排除して統合
    final_learnings = list({l for r in results for l in r.learnings})
    final_urls      = list({u for r in results for u in r.visited_urls})

    return ResearchResult(final_learnings, final_urls)




def judge_followup_required(
    query: str,
    learnings: Optional[List[str]] = None,
) -> bool:
    """
    フォローアップリサーチが必要かどうかを判断する。

    パラメータ
    ----------
    query : str
        ユーザープロンプト。
    learnings : list[str] | None
        これまでに得た知見の蓄積。

    戻り値
    ------
    bool
        フォローアップリサーチが必要なら True、不要なら False。
    """
    if not learnings:
        return True

    # OpenAIを使って、queryとlearningsをInputし。followup researchが必要かを判断する。
    prompt = (
        f"Below is the user's research question followed by the learnings collected so far.\n"
        f"**Question:** {query}\n\n"
        "### Current Learnings\n"
        + "\n".join(f"- {l}" for l in learnings) + "\n\n"
        "### Task\n"
        "1. Critically evaluate whether the learnings already provide a complete and well-supported answer to the question.\n"
        "2. Examine the coverage from multiple angles (e.g., factual accuracy, depth, timeliness, opposing viewpoints, remaining unknowns).\n"
        "3. Decide if additional research is required.\n\n"
        "### Response format\n"
        "Return **exactly one word** (case-insensitive):\n"
        "- `yes`  → Follow-up research is still needed.\n"
        "- `no`   → The learnings are sufficient; no further research is required."
    )


    # structured output を Pydantic モデルで指定
    resp = client.responses.parse(
        model=LLM_MODEL,
        reasoning={"effort": "medium"},
        input=[
            {"role": "system", "content": system_prompt()},
            {"role": "user",   "content": prompt},
        ],
        text_format=LLMJudgement
    )
    
    parsed = resp.output_parsed
    return parsed.followup_required


async def followup_research(
    query: str,
    learnings: Optional[List[str]] = None,
    visited_urls: Optional[List[str]] = None,
    on_progress: Optional[Callable[[ResearchProgress], None]] = None,
) -> ResearchResult:
    """
    フォローアップリサーチを実行し、追加の知見と訪問したURLを返す。

    パラメータ
    ----------
    learnings : list[str] | None
        これまでに得た知見の蓄積。再帰で下層へ渡し、最後に重複除去して返す。
    visited_urls : list[str] | None
        既にクロールした URL の集合。最終結果用なので途中処理では使わない。

    戻り値
    ------
    ResearchResult
        - `learnings` : 最終的な知見（重複除去済み）  
        - `visited_urls` : 参照した全 URL（重複除去済み）
    """
    learnings = learnings or []
    visited_urls = visited_urls or []

    # フォローアップリサーチを実行
    return await deep_research(
        query=query,
        breadth=2,
        depth=2,
        learnings=learnings,
        visited_urls=visited_urls,
        on_progress=on_progress,
    )


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