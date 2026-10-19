"""Streamlit User Interface – talks to the FastAPI backend over HTTP only.

No business logic here: all AI / DB work happens in the backend.
Configure API_BASE_URL in the environment (default http://127.0.0.1:8000).
Look and flow follow the DocForge web client: chat history sidebar, attachments with a role, live progress
steps, download cards with slide thumbnails, and a plain-language quality report.
"""

from __future__ import annotations

import base64
import os
import re
import time
from typing import Any

import httpx
import streamlit as st

# ── Configuration ─────────────────────────────────────────────────────────────

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
POLL_INTERVAL = 1.5        # seconds between run-status polls
MAX_POLL_SECONDS = 600     # generation with research can take a few minutes
ALLOWED_UPLOAD_TYPES = ["docx", "pptx", "xlsx", "pdf", "png", "jpg", "jpeg"]

# Attachment roles: what the backend should do with a file (Auto lets it infer from the message).
FILE_ROLES = {
    "Auto": None,
    "Template (design to follow)": "TEMPLATE",
    "Source (facts to use)": "SOURCE",
    "Dataset (numbers to analyse)": "DATASET",
}
ROLE_SHORT = {"TEMPLATE": "template", "SOURCE": "source", "DATASET": "dataset", "REFERENCE": "auto"}

# Pipeline nodes → steps the user understands, in the order they normally run.
STEP_LABELS = {
    "load_context_node": "Reading the conversation",
    "plan_node": "Understanding the request",
    "analyze_templates_node": "Studying the template",
    "gather_context_node": "Researching sources",
    "generate_doc_node": "Writing the document",
    "generate_deck_node": "Building the slides",
    "generate_sheet_node": "Building the spreadsheet",
    "validate_node": "Checking quality",
    "finalize_node": "Rendering and saving files",
    "answer_node": "Answering",
    "edit_node": "Applying the edit",
    "convert_node": "Converting the file",
    "stub_node": "Preparing a reply",
}

EXAMPLES = [
    "Make a 10-slide deck on EV charging infrastructure in India",
    "Write a 2-page report on heat pumps in cold climates",
    "Summarise the attached file as 6 slides",
]

st.set_page_config(page_title="NexaWorks AI", page_icon="📄", layout="wide", initial_sidebar_state="expanded")

# ── Look (DocForge palette, dark) ─────────────────────────────────────────────
st.markdown(
    """
    <style>
    :root { --panel:#171a23; --panel2:#1e2230; --fg:#e6e8ef; --muted:#9aa3b5; --line:#2a2f3d;
            --accent:#8b8bf5; --accent2:#b08cff; --soft:#252547; --ok:#4cc79a; --okSoft:#12302a;
            --warn:#f0a85a; --warnSoft:#33260f; --bad:#ff7b70; --badSoft:#3a1714; }
    .stApp { background:#0f1117; }
    .block-container { padding-top:1.2rem; max-width:1500px; }
    section[data-testid="stSidebar"] { background:var(--panel); border-right:1px solid var(--line); }
    .brand { display:flex; align-items:center; gap:10px; font-weight:700; font-size:1.15rem; margin-bottom:.4rem; }
    .logo { width:30px; height:30px; border-radius:9px; display:grid; place-items:center; color:#fff;
            background:linear-gradient(135deg,var(--accent),var(--accent2)); }
    .side-label { font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:.6rem 0 .2rem; }
    .hero { text-align:center; padding:6vh 8px 18px; }
    .hero h1 { font-size:1.9rem; margin:.4rem 0; letter-spacing:-.02em; }
    .hero p { color:var(--muted); }
    .chip { display:inline-block; background:var(--soft); border:1px solid rgba(139,139,245,.4); color:var(--fg);
            padding:3px 10px; border-radius:12px; font-size:.82rem; margin:0 6px 6px 0; }
    .chip small { color:var(--muted); }
    .card { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:12px 14px; margin:.5rem 0; }
    .card .t { font-weight:600; }
    .card .m { color:var(--muted); font-size:.82rem; }
    .badge { padding:1px 8px; border-radius:6px; font-size:.72rem; font-weight:700; color:#fff; margin-right:6px; }
    .b-docx { background:#1565c0; } .b-pptx { background:#8e3fc9; } .b-xlsx { background:#1b7a3a; }
    .q-ok   { background:var(--okSoft); color:var(--ok); border-radius:10px; padding:8px 12px; }
    .q-warn { background:var(--warnSoft); color:var(--warn); border-radius:10px; padding:8px 12px; }
    .q-bad  { background:var(--badSoft); color:var(--bad); border-radius:10px; padding:8px 12px; }
    .step { font-size:.9rem; margin:2px 0; }
    .step.done { color:var(--ok); } .step.run { color:var(--accent); } .step.err { color:var(--bad); }
    .error-box { background:var(--badSoft); border:1px solid var(--bad); border-radius:10px; padding:.6rem 1rem;
                 color:var(--bad); margin-bottom:.5rem; }
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
        "chat_history": [],   # list[dict role/content/run_id/...]
        "selected_run_id": None,
        "attachments": [],    # list of file dicts from backend (+ "role")
        "request_in_flight": False,
        "last_submission_text": "",
        "last_submission_time": 0.0,
        "previews": {},       # (artifact_id, version) -> list[base64 png]
        "_uploaded_file_keys": set(),
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


def _get(path: str, *, params: dict | None = None, timeout: float = 15, quiet: bool = False) -> Any:
    """GET request; returns parsed JSON or None on error (errors shown unless ``quiet``)."""
    try:
        r = httpx.get(f"{API_BASE}{path}", headers=_headers(), params=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        if not quiet:
            _show_api_error("GET", path, r)
        return None
    except httpx.RequestError as exc:
        if not quiet:
            st.error(f"Cannot reach the backend: {exc}")
        return None


def _post(path: str, json: dict | None = None, files=None, data=None, params: dict | None = None, timeout: float = 60) -> dict | None:
    """POST request; returns parsed JSON or None on error."""
    try:
        headers = {"Authorization": f"Bearer {st.session_state.jwt}"} if st.session_state.jwt else {}
        if json is not None:
            headers["Content-Type"] = "application/json"
        r = httpx.post(f"{API_BASE}{path}", headers=headers, json=json, files=files, data=data, params=params, timeout=timeout)
        if r.status_code in (200, 201, 202):
            return r.json()
        _show_api_error("POST", path, r)
        return None
    except httpx.RequestError as exc:
        st.error(f"Cannot reach the backend: {exc}")
        return None


def _show_api_error(method: str, path: str, r: httpx.Response) -> None:
    try:
        detail = r.json().get("detail", r.text[:200])
    except Exception:
        detail = r.text[:200]
    st.markdown(f'<div class="error-box"><b>{method} {path}</b> → HTTP {r.status_code}: {detail}</div>', unsafe_allow_html=True)


def _download_url(artifact_id: Any, version: Any) -> str:
    """Browser download link: the backend's address plus the JWT, since a link cannot send headers."""
    return f"{API_BASE}/artifacts/{artifact_id}/download?version={version}&token={st.session_state.jwt or ''}"


def _absolute_links(markdown: str) -> str:
    """Replies link downloads as /artifacts/<id>/download?version=<n>; the browser needs address and token."""
    return re.sub(
        r"\]\(/artifacts/(\d+)/download\?version=(\d+)\)",
        lambda m: f"]({_download_url(m.group(1), m.group(2))})",
        markdown,
    ).replace("](/artifacts/", f"]({API_BASE}/artifacts/")


# ── Auth ──────────────────────────────────────────────────────────────────────


def _do_login(username: str, password: str) -> bool:
    try:
        r = httpx.post(f"{API_BASE}/auth/login", json={"username": username, "password": password}, timeout=10)
        if r.status_code == 200:
            st.session_state.jwt = r.json()["access_token"]
            st.session_state.username = username
            _reset_chat()
            return True
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        st.error(f"Login failed: {detail}")
        return False
    except httpx.RequestError as exc:
        st.error(f"Cannot reach backend at {API_BASE}: {exc}")
        return False


def _reset_chat() -> None:
    st.session_state.session_id = None
    st.session_state.chat_history = []
    st.session_state.selected_run_id = None
    st.session_state.attachments = []
    st.session_state["_uploaded_file_keys"] = set()
    if "session" in st.query_params:
        del st.query_params["session"]


def _entry_from_run(run: dict, content: str | None = None) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content if content is not None else (run.get("reply") or ""),
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "citations": run.get("citations", {}),
        "validation": run.get("validation_report"),
        "artifacts": run.get("artifacts", []),
    }


def _open_session(session_id: str) -> None:
    """Load a stored conversation: messages, and for recent replies their files and quality report."""
    msgs = _get(f"/sessions/{session_id}/messages")
    if msgs is None:
        return
    history: list[dict[str, Any]] = []
    detailed = {m.get("run_id") for m in msgs[-20:] if m["role"] == "assistant"}
    for m in msgs:
        if m["role"] == "assistant" and m.get("run_id") in detailed:
            run = _get(f"/runs/{m['run_id']}", quiet=True)
            if run:
                history.append(_entry_from_run(run, content=m["content"]))
                continue
        history.append({"role": m["role"], "content": m["content"], "run_id": m.get("run_id")})
    st.session_state.session_id = session_id
    st.session_state.chat_history = history
    st.session_state.selected_run_id = next((h.get("run_id") for h in reversed(history) if h.get("run_id")), None)
    st.query_params["session"] = session_id


# ══════════════════════════════════════════════════════════════════════════════
# LOGIN SCREEN
# ══════════════════════════════════════════════════════════════════════════════

if not st.session_state.jwt:
    col1, col2, col3 = st.columns([1, 1.3, 1])
    with col2:
        st.markdown('<div class="hero"><div class="brand" style="justify-content:center"><span class="logo">📄</span>'
                    'NexaWorks AI</div><p>Documents and presentations from a conversation.</p></div>', unsafe_allow_html=True)
        with st.form("login_form"):
            username = st.text_input("Username", value="demo")
            password = st.text_input("Password", type="password", value="demo123")
            submitted = st.form_submit_button("Sign in", use_container_width=True, type="primary")
        if submitted:
            with st.spinner("Signing in…"):
                ok = _do_login(username, password)
            if ok:
                st.rerun()
    st.stop()

# Restore session from URL query_params on page load / F5
if st.session_state.session_id is None and st.query_params.get("session"):
    _open_session(st.query_params.get("session"))

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR – chat history
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown('<div class="brand"><span class="logo">📄</span>NexaWorks AI</div>', unsafe_allow_html=True)
    if st.button("＋ New chat", use_container_width=True, type="primary"):
        _reset_chat()
        st.rerun()

    st.markdown('<div class="side-label">Chats</div>', unsafe_allow_html=True)
    sessions = _get("/sessions", quiet=True) or []
    if not sessions:
        st.caption("No conversations yet.")
    for s in sessions:
        sid = s["session_id"]
        active = sid == st.session_state.session_id
        label = ("● " if active else "") + (s.get("title") or "Untitled chat")[:42]
        when = (s.get("last_activity") or "")[:16].replace("T", " ")
        if st.button(label, key=f"sess_{sid}", use_container_width=True, help=f"{s.get('message_count', 0)} messages · {when}",
                     disabled=st.session_state.request_in_flight):
            _open_session(sid)
            st.rerun()

    st.divider()
    st.caption(f"Signed in as **{st.session_state.username}**")
    if st.button("Sign out", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# MAIN LAYOUT – chat | files
# ══════════════════════════════════════════════════════════════════════════════

main_col, art_col = st.columns([3, 2], gap="large")


def _progress(placeholder, steps: list[dict], status: str, elapsed: float) -> None:
    """Live steps: finished ones ticked, the current one highlighted, failures marked."""
    with placeholder.container():
        label = {"done": "Done", "failed": "Stopped"}.get(status, f"Working… {int(elapsed)}s")
        state = {"done": "complete", "failed": "error"}.get(status, "running")
        with st.status(label, state=state, expanded=status not in ("done",)):
            seen: list[str] = []
            for step in steps:
                name = step.get("agent_name", "?")
                if name in seen:
                    continue
                seen.append(name)
                text = STEP_LABELS.get(name, name.replace("_node", "").replace("_", " ").capitalize())
                ok = step.get("status") == "ok"
                css, mark = ("done", "✓") if ok else ("err", "✗")
                dur = step.get("duration_ms")
                took = f" · {dur / 1000:.1f}s" if dur and dur >= 1000 else ""
                st.markdown(f'<div class="step {css}">{mark} {text}{took}</div>', unsafe_allow_html=True)
            if status not in ("done", "failed"):
                st.markdown('<div class="step run">● working on the next step…</div>', unsafe_allow_html=True)


def _poll_run(run_id: str) -> dict | None:
    """Poll GET /runs/{run_id} until done/failed, showing live progress."""
    placeholder = st.empty()
    started = time.time()
    while time.time() - started < MAX_POLL_SECONDS:
        run = _get(f"/runs/{run_id}", timeout=10)
        if not run:
            return None
        status = run.get("status", "running")
        _progress(placeholder, run.get("trace_steps", []), status, time.time() - started)
        if status in ("done", "failed"):
            placeholder.empty()
            return run
        time.sleep(POLL_INTERVAL)
    placeholder.empty()
    st.warning("Still working after 10 minutes – open this chat again later to see the result.")
    return None


def _send_message(message: str) -> None:
    """Submit a message (and attachments) to POST /chat, follow the run, store the reply."""
    msg_clean = message.strip()
    attachments = list(st.session_state.attachments)
    if not msg_clean and not attachments:
        return
    now = time.time()
    if msg_clean and msg_clean == st.session_state.last_submission_text and now - st.session_state.last_submission_time < 5.0:
        st.warning("Duplicate request ignored (same message sent within 5 seconds).")
        return
    if st.session_state.request_in_flight:
        st.warning("A request is already running. Please wait for it to finish.")
        return

    st.session_state.request_in_flight = True
    st.session_state.last_submission_text = msg_clean
    st.session_state.last_submission_time = now
    try:
        chips = " ".join(f"`📎 {f.get('filename', 'file')}`" for f in attachments)
        st.session_state.chat_history.append({"role": "user", "content": f"{chips}\n\n{message}".strip()})
        payload: dict[str, Any] = {"message": message, "file_ids": [f["id"] for f in attachments]}
        if st.session_state.session_id:
            payload["session_id"] = st.session_state.session_id
        st.session_state.attachments = []
        st.session_state["_uploaded_file_keys"] = set()

        resp = _post("/chat", json=payload)
        if not resp:
            st.session_state.chat_history.append({"role": "assistant", "content": "I couldn't reach the backend. Nothing was sent; please try again.", "status": "failed"})
            return
        run_id = resp["run_id"]
        st.session_state.session_id = resp.get("session_id", st.session_state.session_id)
        st.query_params["session"] = st.session_state.session_id
        st.session_state.selected_run_id = run_id

        if resp.get("status") == "done" and resp.get("reply"):  # answered immediately (files without text)
            st.session_state.chat_history.append({"role": "assistant", "content": resp["reply"], "run_id": run_id, "status": "done"})
            return
        run = _poll_run(run_id)
        if not run:
            st.session_state.chat_history.append({"role": "assistant", "content": "No result yet for this request.", "run_id": run_id, "status": "failed"})
            return
        st.session_state.chat_history.append(_entry_from_run(run))
    finally:
        st.session_state.request_in_flight = False


# ── Message parts ─────────────────────────────────────────────────────────────


def _previews(artifact_id: int, version: int | None) -> list[str]:
    key = (artifact_id, version)
    if key not in st.session_state.previews:
        res = _get(f"/artifacts/{artifact_id}/previews", params={"version": version, "width": 480} if version else {"width": 480}, timeout=120, quiet=True)
        st.session_state.previews[key] = (res or {}).get("slides", [])
    return st.session_state.previews[key]


def _thumbnails(artifact_id: int, version: int | None, *, columns: int = 3) -> None:
    slides = _previews(artifact_id, version)
    if not slides:
        st.caption("No preview available.")
        return
    cols = st.columns(columns)
    for i, b64 in enumerate(slides):
        with cols[i % columns]:
            st.image(base64.b64decode(b64), caption=f"Slide {i + 1}", use_container_width=True)


def _download_card(art: dict, key: str) -> None:
    kind = (art.get("kind") or "?").lower()
    art_id, version = art.get("artifact_id"), art.get("version")
    url = _download_url(art_id, version)
    st.markdown(
        f'<div class="card"><span class="badge b-{kind}">{kind.upper()}</span><span class="t">{art.get("title", "File")}</span>'
        f'<div class="m">Version {version} · ready to download</div></div>',
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns([1, 1])
    with c1:
        st.link_button(f"⬇ Download .{kind}", url, use_container_width=True)
    if kind == "pptx":
        with c2:
            show = st.toggle("Slide preview", key=f"pv_{key}", value=False)
        if show:
            _thumbnails(art_id, version)


def _quality(val: dict | None) -> None:
    """Plain-language quality report: verdict first, then what to look at, grouped by slide or section."""
    if not val:
        return
    issues = val.get("issues", [])
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") != "error"]
    score = val.get("score", 0)
    if not issues:
        st.markdown(f'<div class="q-ok">✓ Quality checks passed (score {score:.0f}/100)</div>', unsafe_allow_html=True)
        return
    css = "q-bad" if errors else "q-warn"
    verdict = f"{len(errors)} problem(s) to fix" if errors else f"{len(warnings)} thing(s) to review"
    with st.expander(f"Quality report: {verdict} · score {score:.0f}/100", expanded=False):
        st.markdown(f'<div class="{css}">{verdict}</div>', unsafe_allow_html=True)
        groups: dict[str, list[dict]] = {}
        for i in errors + warnings:
            where = i.get("where", "")
            label = where.split(":", 1)[-1].replace("_", " ").capitalize() if ":" in where else "General"
            groups.setdefault(label, []).append(i)
        for label, items in groups.items():
            st.markdown(f"**{label}**")
            for i in items:
                mark = "🔴" if i.get("severity") == "error" else "🟠"
                st.markdown(f"{mark} {i.get('message', '')}")


def _render_assistant(entry: dict, idx: int) -> None:
    if entry.get("status") == "failed" and not entry.get("artifacts"):
        st.markdown(f'<div class="error-box">{_absolute_links(entry.get("content", ""))}</div>', unsafe_allow_html=True)
    elif entry.get("content"):
        st.markdown(_absolute_links(entry["content"]))
    for n, art in enumerate(entry.get("artifacts") or []):
        _download_card(art, key=f"{idx}_{n}")
    _quality(entry.get("validation"))
    citations = entry.get("citations") or {}
    if citations:
        with st.expander(f"Sources ({len(citations)})", expanded=False):
            for cid, src in citations.items():
                title, url = src.get("title", f"Source {cid}"), src.get("url", "")
                st.markdown(f"**[{cid}]** [{title}]({url})" if url else f"**[{cid}]** {title}")


# ══════════════════════════════════════════════════════════════════════════════
# CHAT COLUMN
# ══════════════════════════════════════════════════════════════════════════════

with main_col:
    in_flight = bool(st.session_state.request_in_flight)
    if not st.session_state.chat_history:
        st.markdown('<div class="hero"><h1>What should we make today?</h1>'
                    '<p>Ask for a deck, report or spreadsheet. Attach a template to follow its design, or files to use as sources.</p></div>',
                    unsafe_allow_html=True)
        ex_cols = st.columns(len(EXAMPLES))
        for col, example in zip(ex_cols, EXAMPLES):
            with col:
                if st.button(example, key=f"ex_{example[:20]}", use_container_width=True, disabled=in_flight):
                    _send_message(example)
                    st.rerun()

    for i, entry in enumerate(st.session_state.chat_history):
        with st.chat_message(entry.get("role", "user")):
            if entry.get("role") == "assistant":
                _render_assistant(entry, i)
            else:
                st.markdown(entry["content"])

    # ── Attachments with a role ───────────────────────────────────────────────
    if st.session_state.attachments:
        chips = "".join(
            f'<span class="chip">📎 <b>{f.get("filename")}</b> <small>· {ROLE_SHORT.get(f.get("purpose") or "REFERENCE", "auto")}</small></span>'
            for f in st.session_state.attachments
        )
        st.markdown(chips, unsafe_allow_html=True)
        a1, a2 = st.columns([1, 1])
        with a1:
            if st.button("Send attachment(s) without a message", use_container_width=True, disabled=in_flight):
                _send_message("")
                st.rerun()
        with a2:
            if st.button("✕ Remove attachments", use_container_width=True):
                st.session_state.attachments = []
                st.session_state["_uploaded_file_keys"] = set()
                st.rerun()

    with st.expander("📎 Attach files", expanded=False):
        role_label = st.radio("Use the files as", list(FILE_ROLES), horizontal=True, key="attach_role")
        new_files = st.file_uploader(
            "Files", type=ALLOWED_UPLOAD_TYPES, accept_multiple_files=True, key="composer_uploader", label_visibility="collapsed"
        )
        if new_files:
            uploaded_keys = st.session_state["_uploaded_file_keys"]
            purpose = FILE_ROLES[role_label]
            for f in new_files:
                file_key = f"{f.name}_{f.size}_{purpose}"
                if file_key in uploaded_keys:
                    continue
                params = {"purpose": purpose or "REFERENCE"}
                if st.session_state.session_id:
                    params["session_id"] = st.session_state.session_id
                with st.spinner(f"Uploading {f.name}…"):
                    res = _post("/files/upload", files={"file": (f.name, f.getvalue(), f.type or "application/octet-stream")}, params=params)
                if res and "id" in res:
                    res["purpose"] = purpose or "REFERENCE"
                    st.session_state.attachments.append(res)
                    uploaded_keys.add(file_key)
            st.session_state["_uploaded_file_keys"] = uploaded_keys

    user_input = st.chat_input("Ask for a deck, a report, a change… (say “try again” after a failure)", disabled=in_flight)
    if user_input:
        _send_message(user_input)
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# RIGHT PANEL – files of this chat + trace
# ══════════════════════════════════════════════════════════════════════════════

with art_col:
    tab_arts, tab_trace = st.tabs(["📄 Files", "🔍 Trace"])

    with tab_arts:
        curr_sess = st.session_state.session_id
        artifacts_list = (_get(f"/sessions/{curr_sess}/artifacts", quiet=True) or []) if curr_sess else []
        artifacts_list = [a for a in artifacts_list if not (a.get("title") or "").startswith("[MOCK]")]
        if not artifacts_list:
            st.info("Files you create in this chat appear here.")
        for art in reversed(artifacts_list):
            art_id = art["id"]
            kind = art.get("artifact_type", "?")
            latest_v = art.get("latest_version", 1)
            versions = art.get("versions", [])
            with st.expander(f"{kind.upper()} · {art.get('title', f'File {art_id}')} (v{latest_v})", expanded=False):
                st.link_button(f"⬇ Download v{latest_v}", _download_url(art_id, latest_v), use_container_width=True)
                if kind == "pptx" and st.toggle("Slide preview", key=f"side_pv_{art_id}_{latest_v}"):
                    _thumbnails(art_id, latest_v, columns=2)
                if len(versions) > 1:
                    st.markdown('<div class="side-label">Versions</div>', unsafe_allow_html=True)
                    for ver in reversed(versions):
                        vno = ver.get("version_no")
                        v1, v2, v3 = st.columns([1, 3, 2])
                        with v1:
                            st.markdown(f"**v{vno}**")
                        with v2:
                            st.caption(f"{ver.get('change_summary') or '–'} · {(ver.get('created_at') or '')[:16].replace('T', ' ')}")
                        with v3:
                            if vno != latest_v and st.button(f"Restore v{vno}", key=f"revert_{art_id}_{vno}", use_container_width=True):
                                rv = _post(f"/artifacts/{art_id}/revert", json={"version": vno})
                                if rv:
                                    st.success(f"Restored as v{rv.get('new_version_no')}")
                                    st.rerun()

    with tab_trace:
        run_id_display = st.session_state.selected_run_id
        if not run_id_display:
            st.info("Send a message to see the steps behind the reply.")
        else:
            st.caption(f"Run `{run_id_display}`")
            for row in _get(f"/runs/{run_id_display}/trace", quiet=True) or []:
                agent = row.get("agent_name", "?")
                icon = "✓" if row.get("status") == "ok" else "✗"
                dur = row.get("duration_ms")
                st.markdown(f"{icon} **{STEP_LABELS.get(agent, agent)}** · {dur or 0} ms")
                if row.get("output_summary"):
                    st.caption(row["output_summary"][:160])
