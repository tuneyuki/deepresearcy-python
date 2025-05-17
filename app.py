# app.py
"""
Streamlit entry point for the Deep Research Assistant.
Run with:
    streamlit run app.py
"""
from __future__ import annotations
import os
import asyncio
from contextlib import suppress
from io import BytesIO
from typing import Any, Optional

import weave
import streamlit as st
import streamlit.components.v1 as components
import markdown
from xhtml2pdf import pisa

# ─────────── Page Configuration ───────────
# Must be first Streamlit call
st.set_page_config(page_title="Deep Research Prototype", layout="wide")

# ─────────── Weave Initialization ───────────
WANDB_ENABLE_WEAVE = os.getenv("WANDB_ENABLE_WEAVE", "false").lower() == "true"
if WANDB_ENABLE_WEAVE:
    weave.init(os.getenv("WANDB_PROJECT"))

# ─────────── Deep Research Imports ───────────
from deep_research import (
    deep_research,
    write_final_report,
    write_final_answer,
    ResearchProgress,
    ResearchResult,
)

# ─────────── Japanese Font Registration ───────────
# Ensure 'NotoSansJP-Regular.ttf' is placed in project root
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
pdfmetrics.registerFont(TTFont('NotoSansJP', 'NotoSansJP-Regular.ttf'))

# ─────────── Session State Initialization ───────────
if "history" not in st.session_state:
    st.session_state.history: list[dict[str, Any]] = []
if "selected_history" not in st.session_state:
    st.session_state.selected_history: Optional[int] = None
if "last_report" not in st.session_state:
    st.session_state.last_report: Optional[str] = None
if "last_output_type" not in st.session_state:
    st.session_state.last_output_type: Optional[str] = None

# ─────────── Utility: Create PDF from Markdown ───────────
def create_pdf_from_md(md_text: str) -> BytesIO:
    html_body = markdown.markdown(md_text)
    css = '''
    <style>
    @page { size: A4; margin: 1cm; }
    @font-face { font-family: 'NotoSansJP'; src: url('NotoSansJP-Regular.ttf'); }
    body { font-family: 'NotoSansJP', sans-serif; line-height: 1.5; }
    </style>
    '''
    html = f"<html><head><meta charset='utf-8'>{css}</head><body>{html_body}</body></html>"
    buffer = BytesIO()
    pisa.CreatePDF(src=html, dest=buffer)
    buffer.seek(0)
    return buffer

# ─────────── Sidebar: New Research, Settings & History ───────────
# New research resets view
if st.sidebar.button("🔍 新規調査開始", key="new_research"):
    st.session_state.selected_history = None
    st.session_state.last_report = None
    st.session_state.last_output_type = None

# Settings in expander
with st.sidebar.expander("設定オプション", expanded=True):
    breadth: int = st.slider("探索幅（検索のバリエーション）", 2, 5, 3)
    depth: int = st.slider("探索の深さ（調査結果をさらに深掘り）", 1, 3, 2)
    output_type: str = st.radio("Output", ["詳細レポート", "シンプル回答"], horizontal=True)

# History links
st.sidebar.header("調査履歴")
if st.session_state.history:
    for idx, entry in enumerate(st.session_state.history):
        title = entry['query'][:20] + ('...' if len(entry['query']) > 20 else '')
        if st.sidebar.button(title, key=f"hist_{idx}"):
            st.session_state.selected_history = idx
            st.session_state.last_report = None
            st.session_state.last_output_type = None
else:
    st.sidebar.write("(履歴なし)")

# ─────────── Main Page ───────────
st.title("🔍 Deep Research prototype")
st.markdown(
    "OpenAI o4-miniを使って、幅／深さをコントロールしながらウェブリサーチを行います。"
)

# Display past history if selected
if st.session_state.selected_history is not None:
    entry = st.session_state.history[st.session_state.selected_history]
    st.subheader("📂 過去の調査結果")
    st.markdown(f"**調査依頼**: {entry['query']}")
    st.markdown("**調査で得たLearnings**:")
    for l in entry['learnings']:
        st.markdown(f"- {l}")
    st.markdown("**最終レポート**:")
    st.markdown(entry['report'], unsafe_allow_html=True)
    if st.button("🔙 新規調査に戻る"):
        st.session_state.selected_history = None
        st.session_state.last_report = None
        st.session_state.last_output_type = None

# Display ongoing or last report
elif st.session_state.last_report is not None:
    st.subheader("📄 Final Report")
    st.markdown(st.session_state.last_report, unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        components.html(
            f"""
            <textarea id='report-text' style='opacity:0;position:absolute;left:-9999px;'>{st.session_state.last_report}</textarea>
            <button onclick="(function(){{ var el=document.getElementById('report-text'); el.style.display='block'; el.select(); document.execCommand('copy'); el.style.display='none'; alert('レポートをコピーしました'); }})();">レポートをコピー</button>
            """,
            height=80,
        )
    with col2:
        pdf_buffer = create_pdf_from_md(st.session_state.last_report)
        st.download_button(
            label="PDFファイルとして保存",
            data=pdf_buffer,
            file_name="final_report.pdf",
            mime="application/pdf",
        )

# Otherwise show new research input
else:
    query: str = st.text_input("何を調査したいですか？", key="query_input")
    if st.button("🚀 Start research", key="start_btn") and query.strip():
        st.info("調査実施中...")

        prog_bar_ph = st.progress(0.0)
        status_box_ph = st.empty()
        learn_expander = st.expander("📚 調査データ", expanded=False)

        def _on_progress(p: ResearchProgress) -> None:
            prog_bar_ph.progress(p.completed_queries / max(p.total_queries, 1))
            lines = [
                f"**Queries**: {p.completed_queries}/{p.total_queries}",
                f"**Depth**  : {p.total_depth - p.current_depth}/{p.total_depth}",
                f"**Breadth**: {p.current_breadth}/{p.total_breadth}",
            ]
            if p.current_query:
                lines.append(f"**Current query**: {p.current_query}")
            status_box_ph.markdown("\n".join(lines))
            if p.new_learnings:
                learn_md = "\n".join(f"- {l}" for l in p.new_learnings)
                learn_expander.markdown(learn_md)

        def _run_async(coro):
            with suppress(RuntimeError):
                loop = asyncio.get_event_loop()
                loop.close()
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()

        async def _driver() -> ResearchResult:
            return await deep_research(
                query=query,
                breadth=breadth,
                depth=depth,
                on_progress=_on_progress,
            )

        research_result = _run_async(_driver())

        async def _summarise() -> str:
            if output_type == "詳細レポート":
                return await write_final_report(
                    query,
                    research_result.learnings,
                    research_result.visited_urls,
                )
            return await write_final_answer(query, research_result.learnings)

        with st.spinner("📝 回答生成中です..."):
            final_md = _run_async(_summarise())

        # Save report in session
        st.session_state.last_report = final_md
        st.session_state.last_output_type = output_type

        # Append to history
        st.session_state.history.append({
            'query': query,
            'learnings': research_result.learnings,
            'report': final_md,
        })

        # Display report after run
        st.rerun()
