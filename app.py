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

import sys
import logging


# ──── Azure AppService 向けログ設定 ────
# ログをすべて stdout に流す  
root_logger = logging.getLogger()  
root_logger.setLevel(logging.INFO)

# 既存ハンドラをクリア（重複防止のため）  
for h in list(root_logger.handlers):
    root_logger.removeHandler(h)

# stdout 用ハンドラ  
sh = logging.StreamHandler(sys.stdout)
sh.setLevel(logging.INFO)
sh.setFormatter(
    logging.Formatter("%(message)s")
)
root_logger.addHandler(sh)

logger = logging.getLogger(__name__)

# ─────────── Page Configuration ───────────
# Must be first Streamlit call
st.set_page_config(page_title="Deep Research Prototype", layout="wide")
if 'history' not in st.session_state:
    st.session_state.history = []  # Always initialize


# ─────────── ユーザー情報を取得 ───────────
# Eメールアドレスがこのヘッダに格納される。
# logger.info(
#     "DEBUG | "
#     f"username={st.context.headers.get('X-Ms-Client-Principal-Name')}"
# )


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
    # judge_followup_required,
    # followup_research,
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
    breadth: int = st.slider("探索幅（検索のバリエーション）", 3, 5, 4)
    depth: int = st.slider("探索の深さ（調査結果をさらに深掘り）", 2, 4, 3)
    # enable_followup: bool = st.checkbox(
    #     "自動追加調査を許容する",
    #     value=False,
    #     help="設定した調査数が不十分な場合、自動で追加の深掘り調査を行います。",
    # )
    output_type: str = st.radio("Output", ["詳細レポート", "シンプル回答"], horizontal=True)

# ─────────── Sidebar: 履歴メニュー ───────────
with st.sidebar.expander("📂 履歴メニュー", expanded=False):
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
このアプリは、OpenAIの最新モデル（o3）を使って、構造的かつ深いWeb調査を行うツールです。

---

## ⚠️ 注意！既知の問題

- 履歴は、ブラウザを閉じたら消えてしまうので、必要に応じて「履歴をファイルに保存する」ボタンで保存してください。
- 調査結果をPDFで出力する際、長い文章の場合、ページ内に収まりきらず途切れてしまいます。（英語は途中で改行されるのでこの問題はありません）
- READMEを表示中に、調査結果のボタンを押しても画面遷移しないので、READMEを閉じてからボタンを押してください。
                
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
st.markdown("OpenAI o3を使って、幅／深さをコントロールしながらウェブリサーチを行います。")
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
        pdf_buffer = create_pdf_from_md(st.session_state.last_report)
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

    # query: str = st.text_input("何を調査したいですか？", key="query_input")
    with st.form("followup_form"):
        query: str = st.text_input("何を調査したいですか？", key="query_input")
        submitted = st.form_submit_button("💡 フォローアップ質問を生成")

    if submitted and query.strip():
        st.session_state.pending_query = query
        st.session_state.followup_answer = ""
        st.session_state.trigger_research = False
        with st.spinner("💭 フォローアップ質問を生成中です..."):
            st.session_state.followup_questions = generate_followup_sync(query)

    if st.session_state.followup_questions:
        st.subheader("🧩 フォローアップ質問")
        for i, q in enumerate(st.session_state.followup_questions, 1):
            st.markdown(f"**Q{i}.** {q}")
        with st.form("answer_form"):
            answer = st.text_area(
                "💬 上記を参考に自由に補足してください（任意）",
                value=st.session_state.get("followup_answer", ""),
                key="followup_answer_input",
                height=150,
            )
            submitted_research = st.form_submit_button("🚀 この内容で調査する")

        st.session_state.followup_answer = answer.strip()

        if submitted_research:
            st.session_state.trigger_research = True

    if st.session_state.trigger_research:
        st.session_state.trigger_research = False  # リセット

        # Azure のトレースログ（stdout）に出力
        # Azure AppService では、標準出力に出力されたログは自動的に収集される
        # AppServiceでEntraID認証を使用している場合、X-Ms-Client-Principal-Name ヘッダにユーザー名が格納される
        log_obj = {
            "event": "Research start",
            "user_id": st.context.headers.get('X-Ms-Client-Principal-Name'),
            "query": st.session_state.pending_query,
            "followups": st.session_state.followup_questions,
            "answer": st.session_state.followup_answer,
            "breadth": breadth,
            "depth": depth,
            "output": output_type
        }
        logger.info(json.dumps(log_obj, ensure_ascii=False))

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
            # ① まず通常のリサーチ
            base_result = await deep_research(
                query=combined_query,
                breadth=breadth,
                depth=depth,
                on_progress=_on_progress,
            )

            # ② 自動追加調査が ON なら breadth=2, depth=2 で再実行
            # if enable_followup:
            #     return await followup_research(
            #         query=combined_query,            # 元のクエリそのまま
            #         learnings=base_result.learnings, # 既存 learnings を継承
            #         visited_urls=base_result.visited_urls,
            #         on_progress=_on_progress,        # 進捗バー再利用
            #     )

            # 追加調査しない場合はそのまま返す
            return base_result

        
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

