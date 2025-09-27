import os
import time
import threading
import webbrowser
import logging
import json
import calendar
from datetime import date, timedelta
import re
import html as htmllib
from collections import Counter

from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template_string
import pyautogui

import urllib.parse
from email.mime.text import MIMEText
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

from openai import OpenAI

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

try:
    from flask_cors import CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})
except Exception:
    app.logger.info("flask_cors not installed — continuing without it.")

CHAT_HISTORY = []
CHAT_LOCK = threading.Lock()

CURRENT_DRAFT = {}

_MONTHS = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12
}

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

APP_MAP = {
    "chrome": "chrome",
    "firefox": "firefox",
    "edge": "msedge",
    "word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
    "notepad": "notepad",
    "calculator": "calc",
    "paint": "mspaint",
    "settings": "ms-settings:",
    "youtube": "https://www.youtube.com/",
    "gmail": "https://mail.google.com/",
    "outlook": "outlook",
    "spotify": "spotify",
    "telegram": "Telegram Desktop",
    "explorer": "explorer",
    "task manager": "taskmgr",
    "command prompt": "cmd",
    "terminal": "wt",
    "instagram": "https://www.instagram.com/",
    "reels": "https://www.instagram.com/reels/",
}

HTML = """
<!doctype html>
<title>Ainek</title>
<h2>Ainek (web)</h2>
<form method="post" action="/open">
  <input name="prompt" style="width:700px" autofocus required>
  <button type="submit">Send</button>
</form>
<div id="result">{{ result }}</div>
"""

API_KEY = os.environ.get("FLASK_API_KEY", None)
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

FASTR_BASE = os.environ.get("FASTR_BASE", "https://go.fastrouter.ai/api/v1")
FASTR_API_KEY = os.environ.get("FASTR_API_KEY")
LLM_MODEL = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-20250514")

REELS_SCROLL_INTERVAL = int(os.environ.get("REELS_SCROLL_INTERVAL", "8"))
REELS_SCROLL_STEPS = int(os.environ.get("REELS_SCROLL_STEPS", "45"))
REELS_CANCEL = threading.Event()

if not FASTR_API_KEY:
    app.logger.warning("FASTR_API_KEY is not set. LLM calls are disabled until FASTR_API_KEY is provided.")

llm_client = None
if FASTR_API_KEY:
    llm_client = OpenAI(base_url=FASTR_BASE, api_key=FASTR_API_KEY)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CRED_PATH = os.environ.get("GMAIL_CREDENTIALS_PATH") or os.path.join(APP_DIR, "credentials.json")
TOKEN_PATH = os.environ.get("GMAIL_TOKEN_PATH") or os.path.join(APP_DIR, "token.json")


def _add_history_entry(entry: dict):
    with CHAT_LOCK:
        CHAT_HISTORY.append(entry)


def _search_and_open(query: str):
    try:
        if DRY_RUN:
            return True, f"(DRY_RUN) Would search and open: {query}"
        pyautogui.hotkey("win", "s")
        time.sleep(0.6)
        pyautogui.typewrite(str(query))
        time.sleep(1.0)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True, f"Searched and opened: {query}"
    except Exception as e:
        app.logger.exception("Error searching and opening app")
        return False, f"Error searching and opening '{query}': {e}"


def _open_mapped_target(key: str):
    try:
        target = APP_MAP.get(key)
        if not target:
            return False, f"No map target for '{key}'"
        if DRY_RUN:
            return True, f"(DRY_RUN) Would open mapped: {key} -> {target}"
        if isinstance(target, str) and (target.startswith("http") or target.endswith(":")):
            webbrowser.open(target)
        else:
            pyautogui.hotkey("win", "s")
            time.sleep(0.7)
            pyautogui.typewrite(str(target))
            time.sleep(0.6)
            pyautogui.press("enter")
        time.sleep(1.5)
        return True, f"Opened mapped: {key} -> {target}"
    except Exception as e:
        app.logger.exception("Error opening mapped target")
        return False, f"Error opening mapped '{key}': {e}"


def _open_app_by_name_from_llm(app_name_raw: str):
    app_name_raw = (app_name_raw or "").strip()
    if not app_name_raw:
        return False, "Empty app name"
    key = app_name_raw.lower()
    if key in APP_MAP:
        return _open_mapped_target(key)
    return _search_and_open(app_name_raw)


def _build_messages_for_llm(prompt: str, history_entries: list):
    system_msg = {
        "role": "system",
        "content": (
            "You are Ainek: a casual, friendly assistant for blind users. "
            "You can use mild slang and speak informally, but keep answers clear and task-focused.\n"
            "Decide user intent as exactly one of: ",
            "\"open_app\", \"scroll_reels\", \"stop_reels\", "
            "\"compose_email\", \"send_email\", \"discard_email\", "
            "\"summarize_emails\", \"chat\".\n"
            "Return ONLY JSON with keys:\n"
            "  \"intent\" (one of the above),\n"
            "  \"app\" (string when intent is \"open_app\"; else null),\n"
            "  \"to\" (array for compose; else null),\n"
            "  \"subject\" (string for compose; else null),\n"
            "  \"body\" (string for compose; else null),\n"
            "  \"sender\" (string email or name for summarize_emails; else null),\n"
            "  \"query\" (string Gmail query for summarize_emails; else null),\n"
            "  \"limit\" (int for summarize_emails; else null),\n"
            "  \"reply\" (casual, short confirmation line).\n"
            "Rules:\n"
            "- If user asks to write/compose an email → \"compose_email\" and produce to/subject/body.\n"
            "- If user says send now → \"send_email\".\n"
            "- If user cancels → \"discard_email\".\n"
            "- If user asks to summarize past emails (e.g., 'summarize emails from Alice last week') → \"summarize_emails\" "
            "  and set either sender or a Gmail query like 'from:alice newer_than:7d'.\n"
            "- Else map to appropriate existing intents or \"chat\".\n"
            "Examples (JSON only):\n"
            "{\"intent\":\"summarize_emails\",\"app\":null,\"to\":null,\"subject\":null,\"body\":null,"
            "\"sender\":\"alice@example.com\",\"query\":null,\"limit\":20,"
            "\"reply\":\"Cool—pulling Alice’s mails and recapping.\"}\n"
            "{\"intent\":\"summarize_emails\",\"app\":null,\"to\":null,\"subject\":null,\"body\":null,"
            "\"sender\":null,\"query\":\"from:hr@acme.com newer_than:30d\",\"limit\":50,"
            "\"reply\":\"On it—summarizing HR threads from last month.\"}\n"
        )
    }
    MAX_TURNS = 6
    recent = history_entries[-MAX_TURNS:] if history_entries else []
    msgs = [system_msg]
    for e in recent:
        role = "user" if e.get("sender") == "user" else "assistant"
        msgs.append({"role": role, "content": e.get("text", "")})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def _ask_llm_for_intent(prompt: str, history_entries: list):
    if not llm_client:
        return False, "LLM disabled: FASTR_API_KEY not set (FastRouter only)."
    messages = _build_messages_for_llm(prompt, history_entries)
    try:
        resp = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=300,
        )
        text = resp.choices[0].message.content
        try:
            parsed = json.loads(text)
            return True, parsed
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    parsed = json.loads(text[start:end+1])
                    return True, parsed
                except Exception:
                    return False, f"LLM responded but JSON parse failed. Raw: {text}"
            return False, f"LLM responded but did not return JSON. Raw: {text}"
    except Exception as e:
        app.logger.exception("LLM call failed")
        return False, f"LLM error: {e}"


def _require_api_key(req):
    if not API_KEY:
        return True, ""
    provided = req.headers.get("X-API-Key") or req.args.get("api_key")
    if not provided:
        return False, "Missing API key"
    if provided != API_KEY:
        return False, "Invalid API key"
    return True, ""

def _open_instagram_reels_and_autoscroll(interval: int = REELS_SCROLL_INTERVAL, steps: int = REELS_SCROLL_STEPS):
    try:
        if DRY_RUN:
            return True, f"(DRY_RUN) Would open Instagram Reels and auto-scroll every {interval}s for {steps} steps."
        REELS_CANCEL.clear()
        webbrowser.open("https://www.instagram.com/reels/")
        time.sleep(4.0)
        pyautogui.hotkey("ctrl", "l"); time.sleep(0.1)
        pyautogui.typewrite("https://www.instagram.com/reels/")
        pyautogui.press("enter"); time.sleep(3.0)
        try:
            w, h = pyautogui.size()
            pyautogui.moveTo(w // 2, int(h * 0.6), duration=0.1)
        except Exception:
            pass
        for i in range(int(steps)):
            if REELS_CANCEL.is_set():
                return True, f"Stopped after {i} steps."
            pyautogui.scroll(-1500)
            pyautogui.press("pagedown")
            ticks = max(1, int(float(interval) / 0.1))
            for _ in range(ticks):
                if REELS_CANCEL.is_set():
                    return True, f"Stopped after {i} steps."
                time.sleep(0.1)
        return True, f"Reels auto-scrolled {steps} steps (every {interval}s)."
    except Exception as e:
        app.logger.exception("Error during Reels autoscroll")
        return False, f"Autoscroll error: {e}"

def _gmail_service():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CRED_PATH):
                raise RuntimeError(f"credentials.json not found at: {CRED_PATH}. Set GMAIL_CREDENTIALS_PATH or place the file there.")
            flow = InstalledAppFlow.from_client_secrets_file(CRED_PATH, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)

def _gmail_recent(n=10):
    svc = _gmail_service()
    msgs = svc.users().messages().list(userId="me", labelIds=["INBOX"], maxResults=n).execute().get("messages", [])
    out = []
    for m in msgs:
        full = svc.users().messages().get(userId="me", id=m["id"], format="metadata", metadataHeaders=["From","Subject","Date"]).execute()
        hdrs = {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}
        out.append({
            "id": m["id"],
            "threadId": full.get("threadId"),
            "from": hdrs.get("from",""),
            "subject": hdrs.get("subject",""),
            "date": hdrs.get("date",""),
            "snippet": full.get("snippet",""),
        })
    return out


def _gmail_send(to_list, subject, body):
    svc = _gmail_service()
    msg = MIMEText(body, _subtype="plain", _charset="utf-8")
    msg["to"] = ", ".join(to_list or [])
    msg["subject"] = subject or ""
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return sent.get("id")

def _draft_email_with_context(user_prompt: str):
    recent = _gmail_recent(10)
    context_lines = []
    for r in recent:
        line = f"FROM: {r['from']} | SUBJECT: {r['subject']} | SNIPPET: {r['snippet']}"
        context_lines.append(line)
    context = "\n".join(context_lines)
    if not llm_client:
        return False, "LLM disabled: FASTR_API_KEY not set."
    sys = {
        "role":"system",
        "content":(
            "Write a professional email draft.\n"
            "Return strict JSON: {\"to\":[...],\"subject\":\"...\",\"body\":\"...\"}.\n"
            "Use the provided context only if it helps; do not leak unrelated details.\n"
            "Prefer the recipient(s) explicitly mentioned by the user; otherwise infer none."
        )
    }
    msgs = [
        sys,
        {"role":"user","content":f"CONTEXT (last 10 emails):\n{context}\n\nUSER REQUEST:\n{user_prompt}"}
    ]
    resp = llm_client.chat.completions.create(
        model=LLM_MODEL, messages=msgs, temperature=0.3, max_tokens=600
    )
    txt = resp.choices[0].message.content
    try:
        start, end = txt.find("{"), txt.rfind("}")
        draft = json.loads(txt[start:end+1]) if start!=-1 and end!=-1 else json.loads(txt)
        to_list = draft.get("to") or []
        if isinstance(to_list, str):
            to_list = [to_list]
        return True, {"to": to_list, "subject": draft.get("subject",""), "body": draft.get("body","")}
    except Exception as e:
        return False, f"Draft parse failed: {txt} ({e})"
    
def _open_gmail_compose(to_list, subject, body):
    to_param = ",".join(to_list or [])
    url = ("https://mail.google.com/mail/?view=cm&fs=1"
           f"&to={urllib.parse.quote(to_param)}"
           f"&su={urllib.parse.quote(subject or '')}"
           f"&body={urllib.parse.quote(body or '')}")
    if DRY_RUN:
        return f"(DRY_RUN) Would open compose: {url}"
    webbrowser.open(url)
    return f"Opened Gmail compose with prefilled draft."

def _gmail_search(query: str, n: int = 25):
    svc = _gmail_service()
    resp = svc.users().messages().list(userId="me", q=query, maxResults=n).execute()
    return resp.get("messages", [])

def _gmail_get_full_message(msg_id: str):
    svc = _gmail_service()
    m = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = m.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

    def _walk_parts(p):
        if not p:
            return []
        if p.get("mimeType", "").startswith("multipart/"):
            parts = p.get("parts", []) or []
            out = []
            for sub in parts:
                out.extend(_walk_parts(sub))
            return out
        else:
            return [p]

    def _decode_body(b64):
        try:
            return base64.urlsafe_b64decode(b64.encode("utf-8")).decode("utf-8", errors="ignore")
        except Exception:
            return ""

    parts = _walk_parts(payload) or []
    text_plain, text_html = "", ""
    for p in parts:
        mt = p.get("mimeType", "")
        data = p.get("body", {}).get("data")
        if not data:
            continue
        decoded = _decode_body(data)
        if mt == "text/plain" and not text_plain:
            text_plain = decoded
        elif mt == "text/html" and not text_html:
            text_html = decoded

    if not text_plain and text_html:
        text = re.sub(r"<(script|style)[^>]*>.*?</\\1>", "", text_html, flags=re.S|re.I)
        text = re.sub(r"<br\\s*/?>", "\n", text, flags=re.I)
        text = re.sub(r"</p\\s*>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = htmllib.unescape(text)
        text_plain = re.sub(r"[ \\t]+", " ", text).strip()

    return {
        "id": m.get("id"),
        "threadId": m.get("threadId"),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": m.get("snippet", ""),
        "body": text_plain or "",
    }

def _gmail_fetch_messages(query: str, limit: int = 25):
    ids = _gmail_search(query, n=min(limit, 30))
    out = []
    for item in ids[:limit]:
        try:
            out.append(_gmail_get_full_message(item["id"]))
        except Exception:
            continue
    return out

def _extractive_summary(messages: list) -> str:
    if not messages:
        return "No matching emails found."
    subjects = [m.get("subject","").strip() for m in messages if m.get("subject")]
    top_subjects = Counter(subjects).most_common(5)
    senders = [m.get("from","") for m in messages]
    top_senders = Counter(senders).most_common(3)
    bullets = []
    bullets.append(f"Matched {len(messages)} emails.")
    if top_senders:
        bullets.append("Top senders: " + ", ".join(f"{s} ({n})" for s,n in top_senders))
    if top_subjects:
        bullets.append("Frequent subjects: " + "; ".join(f"“{s}” ×{n}" for s,n in top_subjects))
    for m in messages[:5]:
        bullets.append(f"- {m.get('date','')}: {m.get('subject','(no subject)')}")
    return "\n".join(bullets)

def _summarize_emails_with_llm(messages: list, user_request: str = "", timeout_s: int = 20):
    if not llm_client:
        return False, "LLM disabled: FASTR_API_KEY not set."
    def clip(s, n=800):
        return (s[:n] + "…") if len(s) > n else s
    bundle = []
    for m in messages:
        bundle.append(
            f"FROM: {m.get('from','')}\n"
            f"SUBJECT: {m.get('subject','')}\n"
            f"DATE: {m.get('date','')}\n"
            f"BODY:\n{clip(m.get('body',''))}\n---"
        )
    context = "\n".join(bundle) if bundle else "(no messages)"
    sys = {
        "role": "system",
        "content": (
            "You are Ainek, a casual, friendly assistant for blind users. "
            "Summarize emails in clear, short bullets. Extract key points, decisions, dates, and action items. "
            "Output:\n- Quick summary (3–6 bullets)\n- Action items\n- Notable dates/links\n"
        )
    }
    usr = {"role": "user", "content": f"{user_request}\n\nEmails:\n{context}"}
    result = {"ok": False, "text": None, "err": None}
    def run():
        try:
            resp = llm_client.chat.completions.create(
                model=LLM_MODEL, messages=[sys, usr], temperature=0.2, max_tokens=600
            )
            result["ok"] = True
            result["text"] = resp.choices[0].message.content.strip()
        except Exception as e:
            result["err"] = str(e)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout_s)
    if not result["ok"]:
        fb = _extractive_summary(messages)
        return True, fb
    return True, result["text"]

def _sender_to_query(sender: str) -> str:
    s = (sender or "").strip().lower()
    common = {
        "linkedin": "(from:linkedin.com OR from:*@linkedin.com)",
        "github": "(from:github.com OR from:*@github.com)",
        "google": "(from:google.com OR from:*@google.com)",
        "facebook": "(from:facebookmail.com OR from:*@facebookmail.com)",
        "twitter": "(from:twitter.com OR from:*@twitter.com)",
    }
    return common.get(s, f"from:{sender}")

def _parse_date_range_from_text(text: str):
    if not text:
        return None
    t = text.lower().strip()
    pat = re.compile(
        r"\b(?:from|between)\s+"
        r"(?:(\d{1,2})\s+)?"
        r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})"
        r"\s+(?:to|until|-)\s+"
        r"(?:(\d{1,2})\s+)?"
        r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})\b"
    )
    m = pat.search(t)
    if not m:
        return None
    d1, m1, y1, d2, m2, y2 = m.groups()
    m1n = _MONTHS[m1]; y1 = int(y1)
    m2n = _MONTHS[m2]; y2 = int(y2)
    if d1: d1 = int(d1)
    else: d1 = 1
    if d2: d2 = int(d2)
    else: d2 = calendar.monthrange(y2, m2n)[1]
    start = date(y1, m1n, d1)
    end_inclusive = date(y2, m2n, d2)
    before = end_inclusive + timedelta(days=1)
    return {
        "after": f"{start.year:04d}/{start.month:02d}/{start.day:02d}",
        "before": f"{before.year:04d}/{before.month:02d}/{before.day:02d}",
    }

# ---- Routes ----
@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML, result="")


@app.route("/open", methods=["POST"])
def open_route():
    prompt = request.form.get("prompt", "").strip()
    if not prompt:
        return render_template_string(HTML, result="Please provide a prompt.")
    user_entry = {"id": f"u-{int(time.time()*1000)}", "sender": "user", "text": prompt, "time": time.time()}
    _add_history_entry(user_entry)
    ok, resp = _ask_llm_for_intent(prompt, CHAT_HISTORY)
    if not ok:
        bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": resp, "time": time.time()}
        _add_history_entry(bot_entry)
        return render_template_string(HTML, result=resp)
    intent = resp.get("intent")
    app_name = resp.get("app")
    reply_text = resp.get("reply") or ""
    if intent == "scroll_reels":
        def bg_scroll():
            success_bg, msg_bg = _open_instagram_reels_and_autoscroll()
            bot_entry_bg = {
                "id": f"b-{int(time.time()*1000)}",
                "sender": "bot",
                "text": f"{reply_text} ({msg_bg})",
                "time": time.time(),
            }
            _add_history_entry(bot_entry_bg)
        threading.Thread(target=bg_scroll, daemon=True).start()
        start_msg = f"{reply_text} (Starting Instagram Reels autoscroll every {REELS_SCROLL_INTERVAL}s for {REELS_SCROLL_STEPS} steps)"
        bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": start_msg, "time": time.time()}
        _add_history_entry(bot_entry)
        return render_template_string(HTML, result=start_msg)
    if intent == "stop_reels":
        REELS_CANCEL.set()
        msg = reply_text or "Stopping reels autoscroll."
        bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": msg, "time": time.time()}
        _add_history_entry(bot_entry)
        return render_template_string(HTML, result=msg)
    if intent == "open_app" and app_name:
        success, msg = _open_app_by_name_from_llm(app_name)
        full_reply = f"{reply_text} ({msg})"
        bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": full_reply, "time": time.time()}
        _add_history_entry(bot_entry)
        return render_template_string(HTML, result=full_reply)
    else:
        bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": reply_text, "time": time.time()}
        _add_history_entry(bot_entry)
        return render_template_string(HTML, result=reply_text)


@app.route("/api/open", methods=["POST", "OPTIONS"])
def open_api():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    ok_req, errmsg = _require_api_key(request)
    if not ok_req:
        return jsonify({"ok": False, "error": errmsg}), 401
    data = request.get_json(force=True, silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"ok": False, "error": "no prompt provided"}), 400
    user_entry = {"id": f"u-{int(time.time()*1000)}", "sender": "user", "text": prompt, "time": time.time()}
    _add_history_entry(user_entry)
    ok, resp = _ask_llm_for_intent(prompt, CHAT_HISTORY)
    if not ok:
        bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": resp, "time": time.time()}
        _add_history_entry(bot_entry)
        return jsonify({"ok": False, "message": resp}), 500
    intent = resp.get("intent")
    app_name = resp.get("app")
    reply_text = resp.get("reply") or ""
    if intent == "scroll_reels":
        def bg_scroll():
            success_bg, msg_bg = _open_instagram_reels_and_autoscroll()
            bot_entry_bg = {
                "id": f"b-{int(time.time()*1000)}",
                "sender": "bot",
                "text": f"{reply_text} ({msg_bg})",
                "time": time.time(),
            }
            _add_history_entry(bot_entry_bg)
        threading.Thread(target=bg_scroll, daemon=True).start()
        return jsonify({
            "ok": True,
            "message": f"{reply_text} (Opening Instagram Reels and auto-scrolling every {REELS_SCROLL_INTERVAL}s for {REELS_SCROLL_STEPS} steps)"
        }), 202
    if intent == "stop_reels":
        REELS_CANCEL.set()
        bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": reply_text or "Stopping reels.", "time": time.time()}
        _add_history_entry(bot_entry)
        return jsonify({"ok": True, "message": reply_text or "Stopping reels."}), 200
    if intent == "compose_email":
        payload = {
            "prompt": prompt,
            "to": resp.get("to"),
            "subject": resp.get("subject"),
            "body": resp.get("body"),
        }
        with app.test_request_context():
            with app.test_client() as c:
                r = c.post("/api/email/draft", json=payload, headers={"X-API-Key": API_KEY} if API_KEY else {})
                j = r.get_json()
                if r.status_code != 200 or not j.get("ok"):
                    return jsonify({"ok": False, "message": j.get("error","draft failed")}), 500
                draft = j["draft"]; msg = j.get("message","")
        reply = reply_text or "Draft ready."
        preview = [
          "Draft staged:",
          f"To: {', '.join(draft.get('to', []))}",
          f"Subject: {draft.get('subject','')}",
          "",
          draft.get("body","")
        ]
        bot_entry_msg = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": reply, "time": time.time()}
        _add_history_entry(bot_entry_msg)
        bot_entry_preview = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": "\n".join(preview), "time": time.time()}
        _add_history_entry(bot_entry_preview)
        return jsonify({"ok": True, "message": f"{reply} ({msg})", "draft": draft, "needs_confirmation": True}), 200
    if intent == "send_email":
        with app.test_request_context():
            with app.test_client() as c:
                r = c.post("/api/email/send", headers={"X-API-Key": API_KEY} if API_KEY else {})
                j = r.get_json()
                code = 200 if j.get("ok") else 500
                if j.get("ok"):
                    _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot","text":"Email sent.", "time": time.time()})
                return jsonify(j), code
    if intent == "discard_email":
        with app.test_request_context():
            with app.test_client() as c:
                r = c.post("/api/email/discard", headers={"X-API-Key": API_KEY} if API_KEY else {})
                j = r.get_json()
                code = 200 if j.get("ok") else 500
                if j.get("ok"):
                    _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot","text":"Draft discarded.", "time": time.time()})
                return jsonify(j), code
    if intent == "summarize_emails":
        q = (resp.get("query") or "").strip()
        sender = (resp.get("sender") or "").strip()
        limit = int(resp.get("limit") or 15)
        if not q:
            if not sender:
                return jsonify({"ok": False, "message": "Need sender or query to summarize."}), 400
            q = _sender_to_query(sender)
        rng = _parse_date_range_from_text(prompt)
        if rng:
            q = f"{q} after:{rng['after']} before:{rng['before']}"
        else:
            if "newer_than:" not in q and "after:" not in q and "before:" not in q:
                q = f"{q} newer_than:30d"
        try:
            t0 = time.time()
            messages = _gmail_fetch_messages(q, limit=limit)
            fetch_ms = int((time.time()-t0)*1000)
            t1 = time.time()
            ok_s, summary = _summarize_emails_with_llm(messages, user_request=f"Summarize {q}", timeout_s=20)
            sum_ms = int((time.time()-t1)*1000)
            app.logger.info(f"Summarize: fetched {len(messages)} in {fetch_ms}ms; summarized in {sum_ms}ms")
            if not ok_s:
                return jsonify({"ok": False, "message": summary}), 500
            intro = reply_text or "Here you go."
            _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot","text": intro, "time": time.time()})
            _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot","text": summary, "time": time.time()})
            return jsonify({"ok": True, "message": intro, "query": q, "count": len(messages), "summary": summary}), 200
        except Exception as e:
            app.logger.exception("Summarize via router failed")
            return jsonify({"ok": False, "message": f"Summarize failed: {e}"}), 500
    if intent == "open_app" and app_name:
        sync = request.args.get("sync") == "1"
        if sync:
            success, msg = _open_app_by_name_from_llm(app_name)
            full_reply = f"{reply_text} ({msg})"
            bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": full_reply, "time": time.time()}
            _add_history_entry(bot_entry)
            return jsonify({"ok": success, "message": full_reply}), (200 if success else 500)
        def bg_open(name, prompt_text):
            success_bg, msg_bg = _open_app_by_name_from_llm(name)
            bot_entry_bg = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": f"{reply_text} ({msg_bg})", "time": time.time()}
            _add_history_entry(bot_entry_bg)
        t = threading.Thread(target=bg_open, args=(app_name, prompt), daemon=True)
        t.start()
        return jsonify({"ok": True, "message": f"{reply_text} (Opening queued: {app_name})"}), 202
    else:
        bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": reply_text, "time": time.time()}
        _add_history_entry(bot_entry)
        return jsonify({"ok": True, "message": reply_text}), 200


@app.route("/api/history", methods=["GET"])
def api_history():
    ok, errmsg = _require_api_key(request)
    if not ok:
        if API_KEY:
            return jsonify({"ok": False, "error": errmsg}), 401
    with CHAT_LOCK:
        hist_copy = list(CHAT_HISTORY)
    def norm(e):
        entry = dict(e)
        if isinstance(entry.get("time"), (int, float)):
            entry["time"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(entry["time"]))
        return entry
    return jsonify([norm(x) for x in hist_copy]), 200

@app.route("/api/email/draft", methods=["POST"])
def api_email_draft():
    ok_req, errmsg = _require_api_key(request)
    if not ok_req and API_KEY:
        return jsonify({"ok": False, "error": errmsg}), 401
    data = request.get_json(force=True, silent=True) or {}
    supplied_to = data.get("to")
    supplied_subject = data.get("subject")
    supplied_body = data.get("body")
    user_prompt = (data.get("prompt") or "").strip()
    if not (supplied_to and supplied_subject and supplied_body):
        ok, draft = _draft_email_with_context(user_prompt or "Compose an email.")
        if not ok:
            return jsonify({"ok": False, "error": draft}), 500
        to_list, subject, body = draft["to"], draft["subject"], draft["body"]
    else:
        to_list = supplied_to if isinstance(supplied_to, list) else [supplied_to]
        subject = supplied_subject
        body = supplied_body
    CURRENT_DRAFT.clear()
    CURRENT_DRAFT.update({"to": to_list, "subject": subject, "body": body, "opened_compose": False})
    msg = _open_gmail_compose(to_list, subject, body)
    CURRENT_DRAFT["opened_compose"] = True if not msg.startswith("(") else False
    return jsonify({"ok": True, "draft": CURRENT_DRAFT, "message": msg, "opened_compose": CURRENT_DRAFT["opened_compose"]}), 200


@app.route("/api/email/send", methods=["POST"])
def api_email_send():
    ok_req, errmsg = _require_api_key(request)
    if not ok_req and API_KEY:
        return jsonify({"ok": False, "error": errmsg}), 401
    if not CURRENT_DRAFT:
        return jsonify({"ok": False, "error": "No draft staged."}), 400
    try:
        msg_id = _gmail_send(CURRENT_DRAFT.get("to"), CURRENT_DRAFT.get("subject"), CURRENT_DRAFT.get("body"))
        sent_info = {"messageId": msg_id}
        CURRENT_DRAFT.clear()
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot","text":"Email sent.", "time": time.time()})
        return jsonify({"ok": True, "sent": sent_info, "message": "Email sent via Gmail API."}), 200
    except Exception as e:
        app.logger.exception("Send failed")
        return jsonify({"ok": False, "error": f"Send failed: {e}"}), 500


@app.route("/api/email/discard", methods=["POST"])
def api_email_discard():
    ok_req, errmsg = _require_api_key(request)
    if not ok_req and API_KEY:
        return jsonify({"ok": False, "error": errmsg}), 401
    CURRENT_DRAFT.clear()
    _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot","text":"Draft discarded.", "time": time.time()})
    return jsonify({"ok": True, "message": "Draft discarded."}), 200

@app.route("/api/email/summarize", methods=["POST"])
def api_email_summarize():
    ok_req, errmsg = _require_api_key(request)
    if not ok_req and API_KEY:
        return jsonify({"ok": False, "error": errmsg}), 401
    data = request.get_json(force=True, silent=True) or {}
    sender = (data.get("sender") or "").strip()
    query = (data.get("query") or "").strip()
    limit = int(data.get("limit") or 15)
    user_req = (data.get("request") or "").strip()
    if not query:
        if not sender:
            return jsonify({"ok": False, "error": "Provide 'sender' or 'query'."}), 400
        query = _sender_to_query(sender) + " newer_than:30d"
    try:
        t0 = time.time()
        messages = _gmail_fetch_messages(query, limit=limit)
        fetch_ms = int((time.time()-t0)*1000)
        t1 = time.time()
        ok, result = _summarize_emails_with_llm(messages, user_request=user_req or f"Summarize {query}", timeout_s=20)
        sum_ms = int((time.time()-t1)*1000)
        app.logger.info(f"/api/email/summarize: fetched {len(messages)} in {fetch_ms}ms; summarized in {sum_ms}ms")
        if not ok:
            return jsonify({"ok": False, "error": result}), 500
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot","text":"Here you go.", "time": time.time()})
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot","text": result, "time": time.time()})
        return jsonify({
            "ok": True,
            "query": query,
            "count": len(messages),
            "summary": result
        }), 200
    except Exception as e:
        app.logger.exception("Summarize failed")
        return jsonify({"ok": False, "error": f"Summarize failed: {e}"}), 500

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("FLASK_PORT", 5003)), debug=False, threaded=True)
