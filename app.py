"""
Streamlit entry point for the Deep Research Assistant.
Run with:
    streamlit run app.py
"""
from __future__ import annotations
import os
import json
import asyncio
from contextlib import suppress
from io import BytesIO
from typing import Any, Optional
import base64

import streamlit as st
import streamlit.components.v1 as components
import markdown
from xhtml2pdf import pisa

# ─────────── Page Configuration ───────────
# Must be first Streamlit call
st.set_page_config(page_title="Deep Research Prototype", layout="wide")
if 'history' not in st.session_state:
    st.session_state.history = []  # Always initialize

# ─────────── Weave Initialization ───────────
WANDB_ENABLE_WEAVE = os.getenv("WANDB_ENABLE_WEAVE", "false").lower() == "true"
if WANDB_ENABLE_WEAVE:
    import weave
    weave.init(os.getenv("WANDB_PROJECT"))

# ─────────── Deep Research Imports ───────────
from deep_research import (
    deep_research,
    write_final_report,
    write_final_answer,
    ResearchProgress,
    ResearchResult,
    generate_followup_sync, # sync version
)

# ─────────── Session State Initialization ───────────
if "selected_history" not in st.session_state:
    st.session_state.selected_history: Optional[int] = None
if "last_report" not in st.session_state:
    st.session_state.last_report: Optional[str] = None
if "last_output_type" not in st.session_state:
    st.session_state.last_output_type: Optional[str] = None
if "pending_query" not in st.session_state:
    st.session_state.pending_query = None
if "followup_questions" not in st.session_state:
    st.session_state.followup_questions = None
if "followup_answer" not in st.session_state:
    st.session_state.followup_answer = ""
if "show_readme" not in st.session_state:
    st.session_state["show_readme"] = False


# ─────────── Utility: Create PDF from Markdown using external CSS ───────────
def create_pdf_from_md(md_text: str) -> BytesIO:
    html_body = markdown.markdown(md_text, extensions=["extra"])
    css_path = os.path.join(os.path.dirname(__file__), "pdf_style.css")
    with open(css_path, encoding="utf-8") as f:
        css_content = f.read()
    style = f"<style>{css_content}</style>"
    html = f"<html><head><meta charset='utf-8'>{style}</head><body>{html_body}</body></html>"
    buffer = BytesIO()
    pisa.CreatePDF(src=html, dest=buffer)
    buffer.seek(0)
    return buffer

# ─────────── Sidebar: New Research & Settings ───────────
st.sidebar.header("Deep Researchメニュー")
if st.sidebar.button("🔍 新規調査開始", key="new_research"):
    st.session_state.selected_history = None
    st.session_state.last_report = None
    st.session_state.last_output_type = None
    st.session_state.show_readme = False

with st.sidebar.expander("⚙️ 設定オプション", expanded=True):
    breadth: int = st.slider("探索幅（検索のバリエーション）", 2, 5, 3)
    depth: int = st.slider("探索の深さ（調査結果をさらに深掘り）", 1, 3, 2)
    output_type: str = st.radio("Output", ["詳細レポート", "シンプル回答"], horizontal=True)

# ─────────── Sidebar: 履歴メニュー ───────────
with st.sidebar.expander("📂 履歴メニュー", expanded=True):
    history_json = json.dumps(st.session_state.history, ensure_ascii=False, indent=2)
    st.download_button(
        label="履歴をファイルに保存する",
        data=history_json,
        file_name="history.json",
        mime="application/json",
    )
    # 履歴を読み込む
    uploaded = st.file_uploader(
        label="履歴を読み込む",
        type=["json"],
        key="upload_history",
        accept_multiple_files=False,
    )
    if uploaded:
        try:
            loaded = json.load(uploaded)
            if isinstance(loaded, list):
                existing = set(json.dumps(e, ensure_ascii=False) for e in st.session_state.history)
                count_added = 0
                for entry in loaded:
                    entry_str = json.dumps(entry, ensure_ascii=False)
                    if entry_str not in existing:
                        st.session_state.history.append(entry)
                        count_added += 1
                st.success(f"{count_added} 件の履歴を読み込みました！")
            else:
                st.error("JSON形式が不正です。リストを指定してください。")
        except Exception as e:
            st.error(f"読み込みエラー: {e}")
    # クリア
    if st.button("履歴をクリア", key="clear_history"):
        st.session_state.history.clear()
        st.success("履歴をクリアしました！")
        st.rerun()

# ─────────── READMEトグル ───────────
with st.sidebar:
    if "show_readme" not in st.session_state:
        st.session_state["show_readme"] = False

    if st.button("📖 README（使い方）", key="show_readme_button"):
        st.session_state.show_readme = True


st.sidebar.markdown("---")
# ─────────── Sidebar: 調査履歴 ───────────
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


# ─────────── README表示 ───────────
if st.session_state.get("show_readme", False):
    st.title("📖 Deep Research 使い方ガイド")
    st.markdown("""
## 概要
このアプリは、OpenAIの最新モデル（o4-mini）を使って、構造的かつ深いWeb調査を行うツールです。

---

## 📝 基本的な流れ

1. **「何を調査したいですか？」に入力**
2. **💡 フォローアップ質問を生成** をクリック
3. **補足があれば記入**
4. **🚀 この内容で調査する** をクリック
5. **最終レポートが表示されます**

---

## ⚠️ 注意！履歴の保存について

- 履歴は、ブラウザを閉じたら消えてしまうので、必要に応じて「履歴をファイルに保存する」ボタンで保存してください。
- 履歴はJSON形式で保存されます。履歴を読み込む際は、同じ形式のJSONファイルを指定してください。
                
---
## 📊 幅と深さのイメージ（幅４、深さ３の場合）
* 幅は調査の切り口、深さは調査の深掘りを表します。
* 深さが進むごとに幅は半減（4→2→1）します。
    """)
    # ここで chart.svg を表示
    def render_svg(svg_text: str) -> None:
        """Renders the given svg string."""
        b64 = base64.b64encode(svg_text.encode('utf-8')).decode("utf-8")
        html = f'<img src="data:image/svg+xml;base64,{b64}"/>'
        st.write(html, unsafe_allow_html=True)

    chart_path = os.path.join(os.path.dirname(__file__), "chart.svg")
    if os.path.exists(chart_path):
        try:
            with open(chart_path, "r", encoding="utf-8") as f:
                svg_text = f.read()
            render_svg(svg_text)
        except Exception:
            st.warning("chart.svg の読み込みに失敗しました。")
    else:
        st.warning("chart.svg が見つかりませんでした。プロジェクトディレクトリに配置してください。")

    if st.button("🔙 戻る", key="readme_back"):
        st.session_state["show_readme"] = False
        st.rerun()

    st.stop()



# ─────────── Main Page ───────────
st.title("🔍 Deep Research prototype")
st.markdown("OpenAI o4-miniを使って、幅／深さをコントロールしながらウェブリサーチを行います。")
# Guard against invalid index
if st.session_state.selected_history is not None and 0 <= st.session_state.selected_history < len(st.session_state.history):
    entry = st.session_state.history[st.session_state.selected_history]
    st.subheader("📂 過去の調査結果")
    st.markdown(f"**調査依頼**: {entry['followups']}")
    with st.expander("📚 調査で得たLearnings", expanded=False):
        for l in entry['learnings']:
            st.markdown(f"- {l}")
    st.markdown("**最終レポート**:")
    st.markdown(entry['report'], unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        components.html(
            f"""
            <textarea id='history-report-text' style='opacity:0;position:absolute;left:-9999px;'>{entry['report']}</textarea>
            <button onclick="(function(){{ var el=document.getElementById('history-report-text'); el.style.display='block'; el.select(); document.execCommand('copy'); el.style.display='none'; alert('レポートをコピーしました'); }})();">レポートをコピー</button>
            """,
            height=80,
        )
    with col2:
        pdf_buffer = create_pdf_from_md(entry['report'])
        b64 = base64.b64encode(pdf_buffer.getvalue()).decode()
        st.markdown(
            f"""
            <a href="data:application/pdf;base64,{b64}" download="history_report.pdf">
                <button class="copy-button">PDFで保存</button>
            </a>
            """,
            unsafe_allow_html=True
        )
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
        pdf_buffer = create_pdf_from_md(entry['report'])
        b64 = base64.b64encode(pdf_buffer.getvalue()).decode()
        st.markdown(
            f"""
            <a href="data:application/pdf;base64,{b64}" download="history_report.pdf">
                <button class="copy-button">PDFで保存</button>
            </a>
            """,
            unsafe_allow_html=True
        )
else:
    if "trigger_research" not in st.session_state:
        st.session_state.trigger_research = False

    if "followup_questions" not in st.session_state:
        st.session_state.followup_questions = []

    if "pending_query" not in st.session_state:
        st.session_state.pending_query = None

    if "followup_answer" not in st.session_state:
        st.session_state.followup_answer = ""

    query: str = st.text_input("何を調査したいですか？", key="query_input")

    if st.button("💡 フォローアップ質問を生成") and query.strip():
        st.session_state.pending_query = query
        st.session_state.followup_answer = ""
        st.session_state.trigger_research = False
        with st.spinner("💭 フォローアップ質問を生成中です..."):
            st.session_state.followup_questions = generate_followup_sync(query)

    if st.session_state.followup_questions:
        st.subheader("🧩 フォローアップ質問")
        for i, q in enumerate(st.session_state.followup_questions, 1):
            st.markdown(f"**Q{i}.** {q}")
        st.session_state.followup_answer = st.text_area(
            "💬 上記を参考に自由に補足してください（任意）",
            value=st.session_state.followup_answer,
            key="followup_answer_input"
        )
        if st.button("🚀 この内容で調査する", key="start_with_followup"):
            st.session_state.trigger_research = True

    if st.session_state.trigger_research:
        st.session_state.trigger_research = False  # リセット

        # query + followup answer を組み合わせ
        base_query = st.session_state.pending_query
        sections = [f"【ユーザーの質問】\n{base_query}"]

        # フォローアップ質問の表示
        if st.session_state.followup_questions:
            followup_section = "\n".join(
                f"{i+1}. {q}" for i, q in enumerate(st.session_state.followup_questions)
            )
            sections.append(f"【フォローアップ質問】\n{followup_section}")

        # フォローアップ回答が空なら「なし」と明記
        followup_answer = st.session_state.followup_answer.strip()
        if followup_answer:
            sections.append(f"【フォローアップ回答】\n{followup_answer}")
        else:
            sections.append("【フォローアップ回答】\nなし")

        combined_query = "\n\n".join(sections)
        
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
                query=combined_query,
                breadth=breadth,
                depth=depth,
                on_progress=_on_progress,
            )
        
        research_result = _run_async(_driver())

        async def _summarise() -> str:
            if output_type == "詳細レポート":
                return await write_final_report(
                    combined_query,
                    research_result.learnings,
                    research_result.visited_urls,
                )
            return await write_final_answer(combined_query, research_result.learnings)
        
        with st.spinner("📝 回答生成中です..."):
            final_md = _run_async(_summarise())
        st.session_state.last_report = final_md
        st.session_state.last_output_type = output_type
        st.session_state.history.append({
            'query': query,
            'followups': combined_query,
            'learnings': research_result.learnings,
            'report': final_md,
        })

        # セッションをクリア
        st.session_state.pending_query = None
        st.session_state.followup_questions = []
        st.session_state.followup_answer = ""
        st.rerun()

