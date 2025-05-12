import getpass
import os
from typing import List, Dict, Annotated, TypedDict, Literal, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage, BaseMessage
from langchain_core.tools import tool

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


if not os.environ.get("OPENAI_API_KEY"):
  os.environ["OPENAI_API_KEY"] = getpass.getpass("Enter API key for OpenAI: ")

from prompts import (
    ROUTER_SYSTEM_PROMPT,
    GENERAL_SYSTEM_PROMPT,
    MORE_INFO_SYSTEM_PROMPT,
    RESEARCH_PLAN_SYSTEM_PROMPT,
    RESPONSE_SYSTEM_PROMPT,
    GENERATE_QUERIES_SYSTEM_PROMPT
)

# model = init_chat_model("gpt-4o-mini", model_provider="openai")

class RouterResult(BaseModel):
    logic: str
    type: Literal["more-info", "deep-research", "general"]

class AgentState(BaseModel):
    """エージェントの内部状態としての会話履歴"""
    messages: List[BaseMessage]
    router: Optional[RouterResult] = None


def analyze_and_route_query(
    state: AgentState,
) -> dict:
    """
    Returns:
        {'router': RouterResult} の形で返却
    """
    # 2) 設定を取得し、モデルをロード
    model = ChatOpenAI(model_name="gpt-4.1-mini", temperature=0)

    # 3) with_structured_output で Pydantic スキーマを渡す
    structured_model = model.with_structured_output(RouterResult)

    # 4) メッセージ列を組み立て
    messages: List[BaseMessage] = [
        SystemMessage(content=ROUTER_SYSTEM_PROMPT),
        *state.messages,  # HumanMessage／AIMessage のリスト
    ]

    # 5) 非同期に実行し、RouterResult インスタンスを受け取る
    router_result: RouterResult = structured_model.invoke(messages)

    # 6) TSと同じ形で返却
    return {"router": router_result}


def route_query(
    state: AgentState
) -> Literal["create_research_plan", "ask_for_more_info", "respond_to_general_query"]:
    router_type = state.router.type
    if router_type == "deep-research":
        return "create_research_plan"
    elif router_type == "more-info":
        return "ask_for_more_info"
    elif router_type == "general":
        return "respond_to_general_query"
    else:
        raise ValueError(f"Unknown router type: {router_type}")


# --- スタブノードの定義 ---
def create_research_plan(state: AgentState) -> dict:
    """
    ユーザーの要求からリサーチプランを作成するノード。
    TODO: 実装を追加
    """
    raise NotImplementedError("create_research_plan is not implemented yet")


def ask_for_more_info(state: AgentState) -> dict:
    """
    リサーチする上で追加情報が必要な場合にユーザーへ質問を返すノード。
    TODO: 実装を追加
    """
    raise NotImplementedError("ask_for_more_info is not implemented yet")


def respond_to_general_query(state: AgentState) -> dict:
    """
    一般的な問いにはここで応答するノード。
    TODO: 実装を追加
    """
    raise NotImplementedError("respond_to_general_query is not implemented yet")


# ----------
# グラフ構築
# ----------

workflow = StateGraph(AgentState)
workflow.add_node("analyze_and_route_query", analyze_and_route_query)
workflow.add_conditional_edges("analyze_and_route_query", route_query, ["create_research_plan", "ask_for_more_info", "respond_to_general_query"])
workflow.add_node("create_research_plan", create_research_plan)
workflow.add_node("ask_for_more_info", ask_for_more_info)
workflow.add_node("respond_to_general_query", respond_to_general_query)

# 各ノードから終了ノードへ繋ぐ
workflow.add_edge("create_research_plan", END)
workflow.add_edge("ask_for_more_info", END)
workflow.add_edge("respond_to_general_query", END)

workflow.add_edge(START, "analyze_and_route_query")

graph = workflow.compile()


state = AgentState(messages=[HumanMessage("日本のAIに関する政治の取り組み状況を知りたい。")])

# 2) invoke でグラフを実行
result = graph.invoke(state)

# 3) 結果を確認
print(result)


