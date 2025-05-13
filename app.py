# app.py
"""
Streamlit entry point for the Deep Research Assistant.
Run with:
    streamlit run app.py
"""
from __future__ import annotations

import asyncio
from contextlib import suppress

import streamlit as st

from deep_research import (
    deep_research,
    write_final_report,
    write_final_answer,
    ResearchProgress,
    ResearchResult,
)

# -----------------------------------------------------------------------------
# Helper utilities
# -----------------------------------------------------------------------------

def _update_progress_widgets(
    p: ResearchProgress,
    prog_bar: "st.delta_generator.DeltaGenerator",
    status_box: "st.delta_generator.DeltaGenerator",
) -> None:
    """Update the progress bar + status lines."""
    prog_bar.progress(p.completed_queries / max(p.total_queries, 1))

    status_lines = [
        f"**Queries**: {p.completed_queries}/{p.total_queries}",
        f"**Depth**  : {p.total_depth - p.current_depth}/{p.total_depth}",
        f"**Breadth**: {p.current_breadth}/{p.total_breadth}",
    ]
    if p.current_query:
        status_lines.append(f"**Current query**: {p.current_query}")
    status_box.markdown("\n".join(status_lines))


def _run_async(coro):
    """Run *coro* in an isolated loop so Streamlit hot‑reload stays happy."""
    with suppress(RuntimeError):
        loop = asyncio.get_event_loop()
        loop.close()
    new_loop = asyncio.new_event_loop()
    try:
        return new_loop.run_until_complete(coro)
    finally:
        new_loop.close()

# -----------------------------------------------------------------------------
# Streamlit Layout
# -----------------------------------------------------------------------------

st.set_page_config(page_title="Deep Research Prototype", layout="wide")
st.title("🔍 Deep Research prototype")
st.markdown(
    "OpenAI o4-miniを使って、幅／深さをコントロールしながらウェブリサーチを行います。"
)

# Form ------------------------------------------------------------------------
with st.form("research_form"):
    query: str = st.text_input("何を調査したいですか？", key="query")

    col1, col2 = st.columns(2)
    with col1:
        breadth: int = st.slider("探索幅（検索のバリエーション）", 2, 5, 3)
    with col2:
        depth: int = st.slider("探索の深さ（調査結果をさらに深掘り）", 1, 3, 2)

    output_type = st.radio("Output", ["詳細レポート", "シンプル回答"], horizontal=True)
    submitted = st.form_submit_button("🚀 Start research")

# -----------------------------------------------------------------------------
# Run research when form submitted
# -----------------------------------------------------------------------------
if submitted and query.strip():
    st.info("調査実施中...")

    # Placeholders ------------------------------------------
    prog_bar_ph   = st.progress(0.0)
    status_box_ph = st.empty()
    learn_expander = st.expander("📚 調査データ", expanded=False)

    # Callback ------------------------------------------------
    def _on_progress(p: ResearchProgress):
        _update_progress_widgets(p, prog_bar_ph, status_box_ph)

        if not p.new_learnings:
            return
        

        # ★ ここをシンプルに：受け取ったリストをそのまま Markdown に
        md = "\n".join(f"- {l}" for l in p.new_learnings)

        # ★ Expander を “上書き” 表示
        learn_expander.markdown(md)


    # Driver --------------------------------------------------
    async def _driver() -> ResearchResult:
        return await deep_research(
            query=query,
            breadth=breadth,
            depth=depth,
            on_progress=_on_progress,
        )

    research_result = _run_async(_driver())

    # Summarise ----------------------------------------------
    async def _summarise():
        if output_type == "詳細レポート":
            return await write_final_report(query, research_result.learnings, research_result.visited_urls)
        return await write_final_answer(query, research_result.learnings)

    # Spinner で「回答生成中」を可視化
    with st.spinner("📝 回答生成中です..."):
        final_output = _run_async(_summarise())

    # Display final output -----------------------------------
    if output_type == "詳細レポート":
        st.markdown("## 📄 Final Report")
        st.markdown(final_output, unsafe_allow_html=True)
    else:
        st.markdown("## ✅ Final Answer")
        st.success(final_output)
