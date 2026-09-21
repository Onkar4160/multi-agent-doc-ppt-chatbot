"""Streamlit User Interface – talks to the FastAPI backend over HTTP only.

No business logic here: all AI / DB work happens in the backend.
Configure API_BASE_URL in the environment (default http://127.0.0.1:8000).
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
import streamlit as st

# ── Configuration ─────────────────────────────────────────────────────────────

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
POLL_INTERVAL = 2          # seconds between run-status polls
MAX_POLL_ROUNDS = 120      # 4 minutes max
ALLOWED_UPLOAD_TYPES = ["docx", "pptx", "pdf", "png", "jpg", "jpeg"]

QUICK_ACTIONS = [
    "Add an executive summary",
    "Make the presentation more concise",
    "Add a competitive analysis section",
    "Update the report using the latest web information",
    "Convert DOCX to PPTX",
]

st.set_page_config(
    page_title="NexaWorks AI",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Minimal CSS ───────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .stApp { background: #0f1117; }
    .block-container { padding-top: 1.2rem; }
    .run-step-ok   { color: #4caf50; font-size: 0.85rem; }
    .run-step-err  { color: #f44336; font-size: 0.85rem; }
    .run-step-run  { color: #ffa726; font-size: 0.85rem; }
    .badge-docx    { background:#1565c0; color:#fff; padding:1px 7px; border-radius:4px; font-size:0.75rem; }
    .badge-pptx    { background:#6a1b9a; color:#fff; padding:1px 7px; border-radius:4px; font-size:0.75rem; }
    .badge-pdf     { background:#b71c1c; color:#fff; padding:1px 7px; border-radius:4px; font-size:0.75rem; }
    .badge-img     { background:#2e7d32; color:#fff; padding:1px 7px; border-radius:4px; font-size:0.75rem; }
    .error-box     { background:#3e0000; border:1px solid #f44336; border-radius:6px;
                     padding:0.6rem 1rem; color:#ff8a80; margin-bottom:0.5rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Session-state defaults ────────────────────────────────────────────────────

def _init_state() -> None:
    defaults = {
        "jwt": None,
        "username": None,
        "session_id": None,
        "chat_history": [],   # list[dict role/content/run_id]
        "selected_run_id": None,
        "use_web": True,
        "use_kb": True,
        "uploaded_files": [],  # list of file dicts from backend
        "selected_file_ids": [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if st.session_state.jwt:
        h["Authorization"] = f"Bearer {st.session_state.jwt}"
    return h


def _get(path: str, *, params: dict | None = None, timeout: float = 15) -> dict | list | None:
    """GET request; returns parsed JSON or None on error (errors shown via st.error)."""
    try:
        r = httpx.get(f"{API_BASE}{path}", headers=_headers(), params=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        _show_api_error("GET", path, r)
        return None
    except httpx.RequestError as exc:
        st.error(f"❌ Connection error → {exc}")
        return None


def _post(path: str, json: dict | None = None, files=None, data=None, timeout: float = 30) -> dict | None:
    """POST request; returns parsed JSON or None on error."""
    try:
        headers = {"Authorization": f"Bearer {st.session_state.jwt}"} if st.session_state.jwt else {}
        if json is not None:
            headers["Content-Type"] = "application/json"
        r = httpx.post(
            f"{API_BASE}{path}",
            headers=headers,
            json=json,
            files=files,
            data=data,
            timeout=timeout,
        )
        if r.status_code in (200, 201, 202):
            return r.json()
        _show_api_error("POST", path, r)
        return None
    except httpx.RequestError as exc:
        st.error(f"❌ Connection error → {exc}")
        return None


def _show_api_error(method: str, path: str, r: httpx.Response) -> None:
    try:
        detail = r.json().get("detail", r.text[:200])
    except Exception:
        detail = r.text[:200]
    st.markdown(
        f'<div class="error-box">❌ <b>{method} {path}</b> → HTTP {r.status_code}: {detail}</div>',
        unsafe_allow_html=True,
    )


def _type_badge(ft: str) -> str:
    css = {"docx": "badge-docx", "pptx": "badge-pptx", "pdf": "badge-pdf"}.get(ft, "badge-img")
    return f'<span class="{css}">{ft.upper()}</span>'


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _do_login(username: str, password: str) -> bool:
    try:
        r = httpx.post(
            f"{API_BASE}/auth/login",
            json={"username": username, "password": password},
            timeout=10,
        )
        if r.status_code == 200:
            st.session_state.jwt = r.json()["access_token"]
            st.session_state.username = username
            return True
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        st.error(f"Login failed: {detail}")
        return False
    except httpx.RequestError as exc:
        st.error(f"❌ Cannot reach backend at {API_BASE}: {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# LOGIN SCREEN
# ══════════════════════════════════════════════════════════════════════════════

if not st.session_state.jwt:
    st.title("🤖 NexaWorks AI Assistant")
    st.caption("Multi-agent document & presentation chatbot")
    col1, col2, col3 = st.columns([1, 1.6, 1])
    with col2:
        with st.form("login_form"):
            st.subheader("Sign in")
            username = st.text_input("Username", value="demo")
            password = st.text_input("Password", type="password", value="demo123")
            submitted = st.form_submit_button("Login", use_container_width=True, type="primary")
        if submitted:
            with st.spinner("Authenticating…"):
                ok = _do_login(username, password)
            if ok:
                st.success("✅ Logged in!")
                st.rerun()
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown(f"### 👤 {st.session_state.username}")
    if st.button("🔄 New session", use_container_width=True):
        st.session_state.session_id = None
        st.session_state.chat_history = []
        st.session_state.selected_run_id = None
        st.rerun()

    st.divider()

    # ── (a) File upload ───────────────────────────────────────────────────────
    st.subheader("📁 Templates & Reference Files")
    uploaded = st.file_uploader(
        "Upload (docx, pptx, pdf, png, jpg)",
        type=ALLOWED_UPLOAD_TYPES,
        label_visibility="collapsed",
    )
    if uploaded:
        with st.spinner("Uploading…"):
            res = _post(
                "/files/upload",
                files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type)},
            )
        if res:
            st.success(f"Uploaded: {res['filename']}")
            # Auto-analyze
            with st.spinner("Analyzing…"):
                _post(f"/files/{res['id']}/analyze")
            st.toast("✅ Analyzed", icon="✅")

    # List uploaded files
    files_data = _get("/files") or []
    st.session_state.uploaded_files = files_data

    if files_data:
        st.caption("Select files to include as context:")
        for f in files_data:
            ft = f.get("file_type", "")
            badge = _type_badge(ft)
            fid = f["id"]
            checked = fid in st.session_state.selected_file_ids
            col_chk, col_lbl = st.columns([0.12, 0.88])
            with col_chk:
                new_val = st.checkbox("", value=checked, key=f"fchk_{fid}", label_visibility="collapsed")
            with col_lbl:
                st.markdown(
                    f'{badge} {f["filename"]}',
                    unsafe_allow_html=True,
                )
            if new_val and fid not in st.session_state.selected_file_ids:
                st.session_state.selected_file_ids.append(fid)
            elif not new_val and fid in st.session_state.selected_file_ids:
                st.session_state.selected_file_ids.remove(fid)

    st.divider()

    # ── (b) Knowledge base ────────────────────────────────────────────────────
    st.subheader("🧠 Knowledge Base")
    kb_file = st.file_uploader(
        "Ingest a document into KB",
        type=["docx", "pdf", "pptx", "txt"],
        key="kb_upload",
        label_visibility="collapsed",
    )
    if kb_file:
        with st.spinner("Ingesting…"):
            res_kb = _post(
                "/kb/ingest",
                files={"file": (kb_file.name, kb_file.getvalue(), kb_file.type)},
            )
        if res_kb:
            st.success(f"Ingested: {res_kb.get('result', {}).get('chunks_count', '?')} chunks")

    if st.button("📚 Ingest sample KB", use_container_width=True):
        with st.spinner("Ingesting sample KB…"):
            res_s = _post("/kb/ingest-samples")
        if res_s:
            st.success(f"Ingested {res_s.get('ingested_files', 0)} files, {res_s.get('total_chunks', 0)} chunks")

    kb_stats = _get("/kb/stats")
    if kb_stats:
        total_vecs = kb_stats.get("total_vectors") or kb_stats.get("vector_count") or kb_stats.get("count", "–")
        st.caption(f"📊 Vector count: **{total_vecs}**")

    st.divider()

    # ── (c) Toggles ───────────────────────────────────────────────────────────
    st.subheader("⚙️ Settings")
    st.session_state.use_web = st.toggle("🌐 Use web search", value=st.session_state.use_web)
    st.session_state.use_kb = st.toggle("🧠 Use knowledge base", value=st.session_state.use_kb)

    st.divider()
    if st.button("🚪 Logout", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LAYOUT – two columns: chat | artifacts
# ══════════════════════════════════════════════════════════════════════════════

main_col, art_col = st.columns([3, 2], gap="large")


# ── helpers ───────────────────────────────────────────────────────────────────

def _poll_run(run_id: str) -> dict | None:
    """Poll GET /runs/{run_id} until done/failed or timeout."""
    placeholder = st.empty()
    for attempt in range(MAX_POLL_ROUNDS):
        run = _get(f"/runs/{run_id}", timeout=10)
        if not run:
            return None
        status = run.get("status", "running")
        steps = run.get("trace_steps", [])

        with placeholder.container():
            with st.status(f"⏳ Running… ({attempt * POLL_INTERVAL}s)", expanded=True) as s:
                for step in steps:
                    sname = step.get("agent_name", "?")
                    sst = step.get("status", "?")
                    dur = step.get("duration_ms")
                    dur_str = f"{dur}ms" if dur else "–"
                    css = "run-step-ok" if sst == "ok" else ("run-step-err" if sst == "error" else "run-step-run")
                    st.markdown(
                        f'<span class="{css}">• {sname} [{sst}] {dur_str}</span>',
                        unsafe_allow_html=True,
                    )
                if status == "done":
                    s.update(label="✅ Done", state="complete", expanded=False)
                elif status == "failed":
                    s.update(label="❌ Failed", state="error", expanded=True)

        if status in ("done", "failed"):
            placeholder.empty()
            return run

        time.sleep(POLL_INTERVAL)

    placeholder.empty()
    st.warning("⚠️ Run timed out – try refreshing.")
    return None


def _send_message(message: str) -> None:
    """Submit message to POST /chat, poll, render result."""
    st.session_state.chat_history.append({"role": "user", "content": message})

    payload: dict[str, Any] = {
        "message": message,
        "file_ids": st.session_state.selected_file_ids,
    }
    if st.session_state.session_id:
        payload["session_id"] = st.session_state.session_id

    with st.spinner("Submitting…"):
        resp = _post("/chat", json=payload)

    if not resp:
        st.session_state.chat_history.append({
            "role": "assistant",
            "content": "❌ Failed to submit message to backend.",
            "failed": True,
        })
        return

    run_id = resp["run_id"]
    st.session_state.session_id = resp.get("session_id", st.session_state.session_id)
    st.session_state.selected_run_id = run_id

    run = _poll_run(run_id)

    if not run:
        st.session_state.chat_history.append({
            "role": "assistant",
            "content": "❌ Run did not return a result.",
            "failed": True,
            "run_id": run_id,
        })
        return

    failed = run.get("status") == "failed"
    errors = run.get("errors", [])

    entry: dict[str, Any] = {
        "role": "assistant",
        "content": run.get("reply") or ("❌ Pipeline failed – no reply." if failed else ""),
        "run_id": run_id,
        "citations": run.get("citations", {}),
        "validation": run.get("validation_report"),
        "artifacts": run.get("artifacts", []),
        "errors": errors,
        "failed": failed or bool(errors),
    }
    st.session_state.chat_history.append(entry)


def _render_assistant_message(entry: dict) -> None:
    """Render an assistant chat bubble with citations, validation, errors."""
    is_failed = entry.get("failed", False)

    if is_failed:
        st.markdown(
            f'<div class="error-box">⚠️ <b>FAILED / PARTIAL</b></div>',
            unsafe_allow_html=True,
        )
        for err in entry.get("errors", []):
            st.markdown(
                f'<div class="error-box">• {err}</div>', unsafe_allow_html=True
            )

    content = entry.get("content", "")
    if content:
        st.markdown(content)

    # Citations expander
    citations = entry.get("citations") or {}
    if citations:
        with st.expander(f"📚 Citations ({len(citations)})", expanded=False):
            for cid, src in citations.items():
                title = src.get("title", f"Source {cid}")
                url = src.get("url", "")
                if url:
                    st.markdown(f"**[{cid}]** [{title}]({url})")
                else:
                    st.markdown(f"**[{cid}]** {title}")

    # Validation report
    val = entry.get("validation")
    if val:
        passed = val.get("passed", True)
        score = val.get("score", "–")
        issues = val.get("issues", [])
        icon = "✅" if passed else "⚠️"
        with st.expander(f"{icon} Validation — score {score}", expanded=not passed):
            if issues:
                for iss in issues:
                    sev = iss.get("severity", "info").upper()
                    where = iss.get("where", "")
                    msg = iss.get("message", "")
                    color = "#f44336" if sev in ("ERROR", "CRITICAL") else "#ffa726"
                    st.markdown(
                        f'<span style="color:{color}"><b>[{sev}]</b> {where}: {msg}</span>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No issues found.")


# ══════════════════════════════════════════════════════════════════════════════
# CHAT COLUMN
# ══════════════════════════════════════════════════════════════════════════════

with main_col:
    st.title("💬 NexaWorks AI Assistant")

    # Render history
    for entry in st.session_state.chat_history:
        role = entry.get("role", "user")
        with st.chat_message(role):
            if role == "user":
                st.markdown(entry["content"])
            else:
                _render_assistant_message(entry)

    # ── (4) Quick-action buttons ──────────────────────────────────────────────
    st.markdown("**Quick actions:**")
    btn_cols = st.columns(len(QUICK_ACTIONS))
    for col, action in zip(btn_cols, QUICK_ACTIONS):
        with col:
            if st.button(action, key=f"qa_{action[:20]}", use_container_width=True):
                _send_message(action)
                st.rerun()

    st.divider()

    # ── (3) Chat input ────────────────────────────────────────────────────────
    user_input = st.chat_input("Ask the AI assistant…")
    if user_input:
        _send_message(user_input)
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# RIGHT PANEL – Artifacts + Trace tabs
# ══════════════════════════════════════════════════════════════════════════════

with art_col:
    tab_arts, tab_trace = st.tabs(["📄 Artifacts", "🔍 Trace"])

    # ── (5) Artifacts tab ─────────────────────────────────────────────────────
    with tab_arts:
        st.subheader("Generated Artifacts")

        if st.button("🔄 Refresh", key="refresh_arts"):
            st.rerun()

        artifacts_list = _get("/artifacts") or []

        if not artifacts_list:
            st.info("No artifacts yet. Start a chat to generate documents.")
        else:
            for art in artifacts_list:
                art_id = art["id"]
                title = art.get("title", f"Artifact {art_id}")
                kind = art.get("artifact_type", "?")
                latest_v = art.get("latest_version", 1)

                with st.expander(
                    f"{_type_badge(kind)} **{title}** (v{latest_v})" , expanded=False
                ):
                    # Download buttons for latest version
                    dl_url = f"{API_BASE}/artifacts/{art_id}/download?version={latest_v}"
                    dcol1, dcol2 = st.columns(2)
                    with dcol1:
                        st.markdown(f"[⬇️ Download {kind.upper()}]({dl_url})", unsafe_allow_html=False)

                    # Outline preview
                    ver_detail = _get(f"/artifacts/{art_id}/versions/{latest_v}")
                    if ver_detail:
                        model_json = ver_detail.get("model_json", {})
                        with st.expander("📋 Outline", expanded=False):
                            if kind == "docx":
                                sections = model_json.get("sections", [])
                                for sec in sections:
                                    indent = "  " * (sec.get("level", 1) - 1)
                                    st.markdown(f"{indent}• {sec.get('heading', '')}")
                            else:
                                slides = model_json.get("slides", [])
                                for idx, sl in enumerate(slides, 1):
                                    st.markdown(f"**{idx}.** {sl.get('title', '')}")

                    # Version history table
                    versions = _get(f"/artifacts/{art_id}/versions") or []
                    if versions:
                        with st.expander("📜 Version history", expanded=False):
                            for ver in versions:
                                vno = ver.get("version_no")
                                summary = ver.get("change_summary", "–")
                                created = (ver.get("created_at") or "")[:16]
                                diff = ver.get("diff") or {}
                                vcol1, vcol2, vcol3 = st.columns([1, 2, 2])
                                with vcol1:
                                    st.markdown(f"**v{vno}**")
                                    st.caption(created)
                                with vcol2:
                                    st.caption(summary)
                                    if diff:
                                        st.json(diff, expanded=False)
                                with vcol3:
                                    dl_v_url = f"{API_BASE}/artifacts/{art_id}/download?version={vno}"
                                    st.markdown(f"[⬇️ v{vno}]({dl_v_url})")
                                    if st.button(
                                        f"↩️ Revert to v{vno}",
                                        key=f"revert_{art_id}_{vno}",
                                        use_container_width=True,
                                    ):
                                        with st.spinner(f"Reverting to v{vno}…"):
                                            rv = _post(
                                                f"/artifacts/{art_id}/revert",
                                                json={"version": vno},
                                            )
                                        if rv:
                                            st.success(f"Reverted → new v{rv.get('new_version_no')}")
                                            st.rerun()
                                st.divider()

    # ── (6) Trace tab ─────────────────────────────────────────────────────────
    with tab_trace:
        st.subheader("Agent Trace")
        run_id_display = st.session_state.selected_run_id
        if not run_id_display:
            st.info("Send a message to populate the trace.")
        else:
            st.caption(f"Run: `{run_id_display}`")
            trace_rows = _get(f"/runs/{run_id_display}/trace") or []

            if not trace_rows:
                st.info("No trace rows yet for this run.")
            else:
                for row in trace_rows:
                    agent = row.get("agent_name", "?")
                    st_val = row.get("status", "?")
                    dur = row.get("duration_ms", "–")
                    in_sum = row.get("input_summary", "")
                    out_sum = row.get("output_summary", "")
                    created = (row.get("created_at") or "")[:19]

                    icon = "✅" if st_val == "ok" else ("❌" if st_val == "error" else "⏳")
                    with st.container():
                        tc1, tc2, tc3 = st.columns([2, 1, 1])
                        with tc1:
                            st.markdown(f"{icon} **{agent}**")
                        with tc2:
                            st.caption(f"{dur}ms" if dur != "–" else "–")
                        with tc3:
                            st.caption(created)
                        if in_sum:
                            st.caption(f"↳ {in_sum[:120]}")
                        if out_sum:
                            st.caption(f"← {out_sum[:120]}")
                        st.divider()
