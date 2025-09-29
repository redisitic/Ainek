import os
import time
import threading
import webbrowser
import logging
import json
import calendar
import unicodedata
import base64
import requests
from datetime import date, timedelta
import re
import html as htmllib
from collections import Counter
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template_string
import pyautogui
import urllib.parse
from email.mime.text import MIMEText
from PIL import Image
import pytesseract
import pyperclip
import difflib
import subprocess
import shutil

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from openai import OpenAI

logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

try:
    from flask_cors import CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})
except Exception:
    app.logger.info("flask_cors not installed — continuing without it.")

TESSERACT_CMD = os.getenv("TESSERACT_CMD", "").strip().strip('"').strip("'")
if TESSERACT_CMD:
    TESSERACT_CMD = os.path.expandvars(os.path.expanduser(TESSERACT_CMD))
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
try:
    _ = pytesseract.get_tesseract_version()
    OCR_AVAILABLE = True
except Exception as e:
    OCR_AVAILABLE = False
    app.logger.warning(f"Tesseract not available: {e}")
BROWSER_PATH = os.getenv("BROWSER_PATH")
EXPLORER_NEW_WINDOW = os.getenv("EXPLORER_NEW_WINDOW", "1") == "1"
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

# --- JSON sanitize helpers ---
SMARTS = {
    ord('“'): '"', ord('”'): '"', ord('„'): '"', ord('‟'): '"',
    ord('’'): "'", ord('‘'): "'", ord('‚'): "'", ord('ʼ'): "'",
    ord('\u00A0'): ' ',  # nbsp
}
CODEFENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.I | re.M)
FIRST_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)

API_KEY = os.environ.get("FLASK_API_KEY", None)
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

FASTR_BASE = os.environ.get("FASTR_BASE", "https://go.fastrouter.ai/api/v1")
FASTR_API_KEY = os.environ.get("FASTR_API_KEY")
LLM_MODEL = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-20250514")

REELS_SCROLL_INTERVAL = int(os.environ.get("REELS_SCROLL_INTERVAL", "8"))
REELS_SCROLL_STEPS = int(os.environ.get("REELS_SCROLL_STEPS", "45"))
REELS_CANCEL = threading.Event()

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID")
SEARCH_MAX_RESULTS = int(os.environ.get("SEARCH_MAX_RESULTS", "5"))

if not FASTR_API_KEY:
    app.logger.warning("FASTR_API_KEY is not set. LLM calls are disabled until FASTR_API_KEY is provided.")

llm_client = None
if FASTR_API_KEY:
    llm_client = OpenAI(base_url=FASTR_BASE, api_key=FASTR_API_KEY)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CRED_PATH = os.environ.get("GMAIL_CREDENTIALS_PATH") or os.path.join(APP_DIR, "credentials.json")
TOKEN_PATH = os.environ.get("GMAIL_TOKEN_PATH") or os.path.join(APP_DIR, "token.json")

# ---------------- core utils ----------------
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
        if isinstance(target, str) and target.startswith("http"):
            ok, msg = _open_url_new_window(target)
            if not ok:
                return False, msg
        elif isinstance(target, str) and target.endswith(":"):
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

# ---------------- LLM intent ----------------
def _build_messages_for_llm(prompt: str, history_entries: list):
    system_msg = {
        "role": "system",
        "content":
            "You are Ainek: a casual, friendly assistant for blind users. "
            "Decide user intent as exactly one of: "
            "\"open_app\", \"scroll_reels\", \"stop_reels\", "
            "\"compose_email\", \"send_email\", \"discard_email\", "
            "\"summarize_emails\", \"web_search\", \"desktop_task\", \"chat\".\n"
            "Return ONLY a single JSON object.\n"
            "Keys:\n"
            "  \"intent\" (string),\n"
            "  \"app\" (string or null),\n"
            "  \"to\" (array or null),\n"
            "  \"subject\" (string or null),\n"
            "  \"body\" (string or null),\n"
            "  \"sender\" (string or null),\n"
            "  \"query\" (string or null),\n"
            "  \"limit\" (integer or null),\n"
            "  \"search_query\" (string or null),\n"
            "  \"k\" (integer or null),\n"
            "  \"instruction\" (string or null),\n"
            "  \"reply\" (string).\n"
            "Rules:\n"
            "- If filesystem or on-screen navigation is requested (open/list/click folders/files, Explorer, paths) → intent=\"desktop_task\" and put the original instruction into \"instruction\".\n"
            "- If user asks to write/compose an email → \"compose_email\".\n"
            "- If user says send now → \"send_email\".\n"
            "- If user cancels → \"discard_email\".\n"
            "- If user asks to summarize past emails → \"summarize_emails\".\n"
            "- If the user asks to look up info on the web → \"web_search\".\n"
            "- Otherwise → \"chat\"."
    }
    MAX_TURNS = 6
    recent = history_entries[-MAX_TURNS:] if history_entries else []
    msgs = [system_msg]
    for e in recent:
        role = "user" if e.get("sender") == "user" else "assistant"
        msgs.append({"role": role, "content": e.get("text", "")})
    msgs.append({"role": "user", "content": prompt})
    return msgs

def _coerce_json_from_text(text: str):
    if text is None:
        raise ValueError("empty content")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\ufeff", "").replace("\u200b", "")
    text = CODEFENCE_RE.sub("", text.strip())
    text = text.translate(SMARTS)
    m = FIRST_JSON_OBJECT_RE.search(text)
    if m:
        text = m.group(0)
    return json.loads(text)

def _maybe_force_web_search(user_prompt: str, parsed: dict) -> dict:
    if not isinstance(parsed, dict):
        return parsed
    if _is_desktopish_request(user_prompt):
        parsed["intent"] = "desktop_task"
        parsed["instruction"] = parsed.get("instruction") or user_prompt.strip()
        return parsed
    intent = (parsed.get("intent") or "").lower()
    if intent != "web_search":
        q = (user_prompt or "").lower()
        triggers = (" search ", "google", "look up", "find info", "latest", "news", "review", "spec")
        if any(t in f" {q} " for t in triggers) and not any(parsed.get(k) for k in ("to","subject","body","app","sender","query","instruction")):
            parsed["intent"] = "web_search"
            parsed["search_query"] = parsed.get("search_query") or user_prompt.strip()
            parsed["k"] = parsed.get("k") or SEARCH_MAX_RESULTS
    return parsed

def _ask_llm_for_intent(prompt: str, history_entries: list):
    if not llm_client:
        return False, "LLM disabled: FASTR_API_KEY not set (FastRouter only)."
    messages = _build_messages_for_llm(prompt, history_entries)
    try:
        parsed = None
        try:
            resp = llm_client.chat.completions.create(
                model=LLM_MODEL, messages=messages,
                temperature=0.7, max_tokens=300,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content
            parsed = json.loads(content)
        except Exception:
            resp = llm_client.chat.completions.create(
                model=LLM_MODEL, messages=messages,
                temperature=0.7, max_tokens=300,
            )
            content = resp.choices[0].message.content
            parsed = _coerce_json_from_text(content)
        parsed = _maybe_force_web_search(prompt, parsed)
        if (parsed.get("intent") == "desktop_task") and not parsed.get("instruction"):
            parsed["instruction"] = prompt.strip()
        return True, parsed
    except Exception as e:
        try:
            content = locals().get("content", "")
            start, end = (content or "").find("{"), (content or "").rfind("}")
            if start != -1 and end > start:
                parsed = json.loads(content[start:end+1])
                parsed = _maybe_force_web_search(prompt, parsed)
                return True, parsed
        except Exception:
            pass
        app.logger.warning("JSON parse failed; raw=%r", locals().get("content", ""))
        return False, f"LLM responded but JSON parse failed. Raw: {locals().get('content','')}"

def _require_api_key(req):
    if not API_KEY:
        return True, ""
    provided = req.headers.get("X-API-Key") or req.args.get("api_key")
    if not provided:
        return False, "Missing API key"
    if provided != API_KEY:
        return False, "Invalid API key"
    return True, ""

# ---------------- reels ----------------
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

# ---------------- Gmail ----------------
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

# ---------------- Google Search ----------------
def _google_search(query: str, k: int = SEARCH_MAX_RESULTS):
    """
    Returns (ok, results_or_error). results_or_error is a list of dicts:
    {title, link, snippet, displayLink}
    """
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return False, "Google search is not configured. Set GOOGLE_API_KEY and GOOGLE_CSE_ID."
    k = max(1, min(int(k or 5), 10))
    try:
        r = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query, "num": k},
            timeout=10,
        )
        if r.status_code != 200:
            return False, f"Google CSE error {r.status_code}: {r.text[:200]}"
        data = r.json()
        items = data.get("items", []) or []
        results = []
        for it in items:
            results.append({
                "title": it.get("title", "").strip(),
                "link": it.get("link", ""),
                "snippet": (it.get("snippet") or it.get("htmlSnippet") or "").strip(),
                "displayLink": it.get("displayLink", ""),
            })
        return True, results
    except Exception as e:
        app.logger.exception("Google CSE call failed")
        return False, f"Search failed: {e}"

def _render_search_results_text(query: str, results: list) -> str:
    """Plain text (good for TTS)."""
    if not results:
        return f"No results for: {query}"
    lines = [f"Top {len(results)} results for: {query}"]
    for i, r in enumerate(results, 1):
        title = r.get("title") or "(no title)"
        snippet = r.get("snippet") or "(no snippet)"
        dom = r.get("displayLink") or ""
        link = r.get("link") or ""
        lines.append(f"{i}. {title}\n   {snippet}\n   Source: {dom}\n   Link: {link}")
    return "\n".join(lines)

def _render_search_results_markdown(query: str, results: list) -> str:
    """Markdown for your ReactMarkdown bubble."""
    if not results:
        return f"**No results** for: `{query}`"
    lines = [f"**Top {len(results)} results for:** `{query}`", ""]
    for i, r in enumerate(results, 1):
        title = r.get("title") or "(no title)"
        link = r.get("link") or ""
        dom = r.get("displayLink") or ""
        snippet = (r.get("snippet") or "").replace("\n", " ").strip()
        lines.append(f"{i}. [{title}]({link})  _(source: {dom})_")
        if snippet:
            lines.append(f"   - {snippet}")
    return "\n".join(lines)

# ---------------- Desktop Search ----------------
def _norm_text(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def _screenshot(region=None):
    if DRY_RUN:
        return None
    img = pyautogui.screenshot(region=region)
    return img

def _ocr_words(img):
    if img is None or not OCR_AVAILABLE:
        return []
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    out = []
    n = len(data.get("text", []))
    for i in range(n):
        txt = data["text"][i].strip()
        if not txt:
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1.0
        if conf < 60:
            continue
        out.append({
            "text": txt,
            "left": int(data["left"][i]),
            "top": int(data["top"][i]),
            "width": int(data["width"][i]),
            "height": int(data["height"][i]),
            "conf": conf,
        })
    return out

def _find_click_target_by_text(target, region=None):
    if DRY_RUN:
        return None, 0.0
    screen = _screenshot(region=region)
    words = _ocr_words(screen)
    if not words:
        return None, 0.0
    tnorm = _norm_text(target)
    best = None
    best_score = 0.0
    for w in words:
        score = difflib.SequenceMatcher(None, tnorm, _norm_text(w["text"])).ratio()
        if score > best_score:
            cx = w["left"] + w["width"] // 2
            cy = w["top"] + w["height"] // 2
            best = (cx, cy)
            best_score = score
    return best, best_score

def _click_xy(x, y, double=False):
    if DRY_RUN:
        return True, "(DRY_RUN) click"
    pyautogui.moveTo(x, y, duration=0.1)
    if double:
        pyautogui.doubleClick()
    else:
        pyautogui.click()
    time.sleep(0.6)
    return True, "clicked"

def _open_explorer_window():
    if DRY_RUN:
        return True, "(DRY_RUN) open explorer"
    try:
        pyautogui.hotkey("win", "e")
        time.sleep(1.5)
        return True, "explorer opened"
    except Exception as e:
        return False, f"explorer open failed: {e}"

def _addrbar_go(path_str):
    if DRY_RUN:
        return True, f"(DRY_RUN) go {path_str}"
    try:
        pyautogui.hotkey("ctrl", "l")
        time.sleep(0.1)
        pyautogui.typewrite(path_str)
        pyautogui.press("enter")
        time.sleep(1.2)
        return True, f"navigated {path_str}"
    except Exception as e:
        return False, f"addrbar failed: {e}"

def _expand_user_env_path(s):
    if not s:
        return s
    s = os.path.expandvars(s)
    s = os.path.expanduser(s)
    return s

def _downloads_path():
    return os.path.join(os.path.expanduser("~"), "Downloads")

def _list_dir(path_str, kind="folders"):
    p = _expand_user_env_path(path_str)
    if not os.path.isdir(p):
        return False, [], f"not a directory: {p}"
    try:
        names = os.listdir(p)
        if kind == "folders":
            items = sorted([n for n in names if os.path.isdir(os.path.join(p, n))])
        elif kind == "files":
            items = sorted([n for n in names if os.path.isfile(os.path.join(p, n))])
        else:
            items = sorted(names)
        return True, items, ""
    except Exception as e:
        return False, [], str(e)

def _get_clipboard_path_from_explorer():
    if DRY_RUN:
        return ""
    try:
        pyautogui.hotkey("alt", "d")
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.1)
        return (pyperclip.paste() or "").strip()
    except Exception:
        return ""

def _plan_desktop_instruction(instr):
    def _rule_plan(t):
        text = _norm_text(t)
        plan = [{"action": "open_explorer"}]
        path = None
        mpath = re.search(r'([A-Za-z]:\\[^\n\r]+|%userprofile%[^\n\r]+|~[^\n\r]+|\\\\[^\n\r]+)', t)
        if mpath:
            path = mpath.group(1).strip()
        elif "downloads" in text:
            path = _downloads_path()
        if path:
            plan.append({"action": "open_path", "path": path})
        m = re.search(r"open\s+([a-z0-9 _\-\.\(\)]+)\s+folder", text)
        if m:
            plan.append({"action": "open_item", "name": m.group(1).strip()})
        m2 = re.search(r"open\s+([a-z0-9 _\-\.\(\)]+)\s+file", text)
        if m2:
            plan.append({"action": "open_item", "name": m2.group(1).strip()})
        list_kind = "all"
        if "list folders" in text or "what folders" in text:
            list_kind = "folders"
        elif "list files" in text or "what files" in text:
            list_kind = "files"
        if any(k in text for k in ("tell", "list", "what", "show")):
            plan.append({"action": "list", "type": list_kind})
        return {"intent": "desktop_task", "plan": plan, "reply": "Done."}

    if not llm_client:
        return True, _rule_plan(instr)

    sys = {
        "role": "system",
        "content": (
            "Return only JSON. Plan desktop steps for Windows using these actions:\n"
            "- open_explorer\n- open_path {path}\n- open_downloads\n- click_text {text}\n- open_item {name}\n- list {type: folders|files|all}\n"
            "Prefer open_path. Output: {\"intent\":\"desktop_task\",\"plan\":[...],\"reply\":\"...\"}"
        ),
    }
    usr = {"role": "user", "content": instr}

    raw = ""
    try:
        try:
            r = llm_client.chat.completions.create(
                model=LLM_MODEL,
                messages=[sys, usr],
                temperature=0.2,
                max_tokens=500,
                response_format={"type": "json_object"},
            )
            raw = r.choices[0].message.content or ""
            j = json.loads(raw)
        except Exception:
            r = llm_client.chat.completions.create(
                model=LLM_MODEL,
                messages=[sys, usr],
                temperature=0.2,
                max_tokens=500,
            )
            raw = r.choices[0].message.content or ""
            j = _coerce_json_from_text(raw)

        if not isinstance(j, dict):
            return True, _rule_plan(instr)
        if j.get("intent") != "desktop_task":
            j["intent"] = "desktop_task"
        if not isinstance(j.get("plan"), list) or not j["plan"]:
            return True, _rule_plan(instr)
        if "reply" not in j or not isinstance(j["reply"], str):
            j["reply"] = "Done."
        return True, j
    except Exception:
        return True, _rule_plan(instr)

def _execute_desktop_plan(plan_obj):
    logs = []
    current_path = None
    listed = {"folders": [], "files": [], "all": []}
    if not isinstance(plan_obj, dict):
        return False, logs, listed, "bad plan"
    steps = plan_obj.get("plan") or []
    for step in steps:
        act = (step.get("action") or "").lower()
        if act == "open_explorer":
            ok, msg = _open_explorer_window()
            logs.append(f"open_explorer: {msg}")
            if not ok:
                return False, logs, listed, msg
            _screenshot()
        elif act == "open_downloads":
            p = _downloads_path()
            ok, msg = _explorer_open_path(p)
            logs.append(f"open_downloads: {msg}")
            if not ok:
                return False, logs, listed, msg
            current_path = p
            _screenshot()

        elif act == "open_path":
            p = _expand_user_env_path(step.get("path") or "")
            ok, msg = _explorer_open_path(p)
            logs.append(f"open_path: {msg}")
            if not ok:
                return False, logs, listed, msg
            current_path = p
            _screenshot()
        elif act == "click_text":
            target = step.get("text") or ""
            pos, score = _find_click_target_by_text(target)
            if not pos or score < 0.6:
                return False, logs, listed, f"text not found: {target}"
            ok, msg = _click_xy(pos[0], pos[1], double=False)
            logs.append(f"click_text:{target} score={round(score,2)} {msg}")
            _screenshot()
        elif act == "open_item":
            name = step.get("name") or ""
            pos, score = _find_click_target_by_text(name)
            if not pos or score < 0.6:
                return False, logs, listed, f"item not found: {name}"
            ok, msg = _click_xy(pos[0], pos[1], double=True)
            logs.append(f"open_item:{name} score={round(score,2)} {msg}")
            if current_path:
                current_path = os.path.join(current_path, name)
            _screenshot()
        elif act == "list":
            ltype = (step.get("type") or "all").lower()
            if not current_path:
                cp = _get_clipboard_path_from_explorer()
                if cp:
                    current_path = cp
            if not current_path:
                return False, logs, listed, "unknown current path"
            ok, items, err = _list_dir(current_path, kind=ltype)
            logs.append(f"list:{ltype} @ {current_path}")
            if not ok:
                return False, logs, listed, err
            if ltype == "folders":
                listed["folders"] = items
            elif ltype == "files":
                listed["files"] = items
            else:
                listed["all"] = items
        else:
            logs.append(f"skip:{act}")
    return True, logs, listed, ""

def _is_desktopish_request(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    if any(k in t for k in (
        "file explorer","explorer","open folder","open file","list files","list folders",
        "downloads","documents","desktop","pictures","videos","directory","path","navigate to"
    )):
        return True
    if re.search(r"(?i)\b[a-z]:\\", text) or ("\\" in text and ":\\" in text):
        return True
    if ("%userprofile%" in t) or ("~" in t and "http" not in t):
        return True
    if "screenshot" in t or "click" in t or "double click" in t:
        return True
    return False

def _open_url_new_window(url: str):
    try:
        exe = BROWSER_PATH
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, "--new-window", url], close_fds=True)
            return True, f"launched:{exe}"
        for cand in ("msedge.exe", "chrome.exe", "firefox.exe"):
            path = shutil.which(cand)
            if path:
                subprocess.Popen([path, "--new-window", url], close_fds=True)
                return True, f"launched:{cand}"
        webbrowser.open_new(url)
        return True, "launched:default"
    except Exception as e:
        return False, f"browser launch failed: {e}"

def _explorer_open_path(path: str, new_window: bool = EXPLORER_NEW_WINDOW):
    p = _expand_user_env_path(path)
    if DRY_RUN:
        return True, f"(DRY_RUN) explorer {p}"
    try:
        if new_window:
            subprocess.Popen(["explorer.exe", "/n,", p], close_fds=True)
            time.sleep(1.2)
            return True, f"explorer new-window {p}"
        else:
            return _addrbar_go(p)
    except Exception as e:
        return False, f"explorer launch failed: {e}"


# ---------------- Routes ----------------
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

    if intent == "web_search":
        q = (resp.get("search_query") or prompt or "").strip()
        k = int(resp.get("k") or SEARCH_MAX_RESULTS)
        ok_s, results_or_err = _google_search(q, k=k)
        if not ok_s:
            msg = f"{reply_text or 'Could not search.'} ({results_or_err})"
            _add_history_entry({"id": f"b-{int(time.time()*1000)}","sender":"bot","text": msg,"time": time.time()})
            return render_template_string(HTML, result=msg)
        md = _render_search_results_markdown(q, results_or_err)
        tts = _render_search_results_text(q, results_or_err)
        _add_history_entry({"id": f"b-{int(time.time()*1000)}","sender":"bot","text": reply_text or "Here’s what I found:", "time": time.time()})
        _add_history_entry({"id": f"b-{int(time.time()*1000)}","sender":"bot","text": md, "time": time.time()})
        # The minimal HTML page shows plain text; your SPA will render markdown via /api/open
        return render_template_string(HTML, result=f"{reply_text or 'Here’s what I found:'}\n\n{tts}")

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
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": start_msg, "time": time.time()})
        return render_template_string(HTML, result=start_msg)

    if intent == "stop_reels":
        REELS_CANCEL.set()
        msg = reply_text or "Stopping reels autoscroll."
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": msg, "time": time.time()})
        return render_template_string(HTML, result=msg)

    if intent == "desktop_task":
        instruction = resp.get("instruction") or prompt
        ok_plan, plan_or_err = _plan_desktop_instruction(instruction)
        if not ok_plan:
            _add_history_entry({"id": f"b-{int(time.time()*1000)}","sender":"bot","text": plan_or_err,"time": time.time()})
            return render_template_string(HTML, result=plan_or_err)
        exec_ok, logs, listed, err = _execute_desktop_plan(plan_or_err)
        if not exec_ok:
            _add_history_entry({"id": f"b-{int(time.time()*1000)}","sender":"bot","text": err,"time": time.time()})
            return render_template_string(HTML, result=err)
        reply = resp.get("reply") or "Done."
        folders = listed.get("folders")
        files = listed.get("files")
        lines = [reply]
        if folders is not None:
            lines.append("Folders: " + (", ".join(folders) if folders else "(none)"))
        if files is not None:
            lines.append("Files: " + (", ".join(files) if files else "(none)"))
        if folders is None and files is None:
            items = listed.get("all") or []
            lines.append("Items: " + (", ".join(items) if items else "(none)"))
        text_out = "\n".join(lines)
        _add_history_entry({"id": f"b-{int(time.time()*1000)}","sender":"bot","text": text_out,"time": time.time()})
        return render_template_string(HTML, result=text_out)

    if intent == "open_app" and app_name:
        success, msg = _open_app_by_name_from_llm(app_name)
        full_reply = f"{reply_text} ({msg})"
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": full_reply, "time": time.time()})
        return render_template_string(HTML, result=full_reply)
    _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": reply_text, "time": time.time()})
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

    _add_history_entry({"id": f"u-{int(time.time()*1000)}", "sender": "user", "text": prompt, "time": time.time()})
    ok, resp = _ask_llm_for_intent(prompt, CHAT_HISTORY)
    if not ok:
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": resp, "time": time.time()})
        return jsonify({"ok": False, "message": resp}), 500

    intent = resp.get("intent")
    app_name = resp.get("app")
    reply_text = resp.get("reply") or ""

    if intent == "web_search":
        q = (resp.get("search_query") or prompt or "").strip()
        k = int(resp.get("k") or SEARCH_MAX_RESULTS)
        ok_s, results_or_err = _google_search(q, k=k)
        if not ok_s:
            msg = f"{reply_text or 'Could not search.'} ({results_or_err})"
            _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot","text": msg, "time": time.time()})
            return jsonify({"ok": False, "message": msg}), 500
        md = _render_search_results_markdown(q, results_or_err)
        tts = _render_search_results_text(q, results_or_err)
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot","text": reply_text or "Here’s what I found:", "time": time.time()})
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot","text": md, "time": time.time()})
        return jsonify({
            "ok": True,
            "message": reply_text or "Here’s what I found:",
            "query": q,
            "results": results_or_err,
            "readable": tts,
            "markdown": md
        }), 200

    if intent == "scroll_reels":
        def bg_scroll():
            success_bg, msg_bg = _open_instagram_reels_and_autoscroll()
            _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot", "text": f"{reply_text} ({msg_bg})", "time": time.time()})
        threading.Thread(target=bg_scroll, daemon=True).start()
        return jsonify({
            "ok": True,
            "message": f"{reply_text} (Opening Instagram Reels and auto-scrolling every {REELS_SCROLL_INTERVAL}s for {REELS_SCROLL_STEPS} steps)"
        }), 202

    if intent == "stop_reels":
        REELS_CANCEL.set()
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot","text": reply_text or "Stopping reels.", "time": time.time()})
        return jsonify({"ok": True, "message": reply_text or "Stopping reels."}), 200

    if intent == "compose_email":
        payload = {"prompt": prompt, "to": resp.get("to"), "subject": resp.get("subject"), "body": resp.get("body")}
        with app.test_request_context():
            with app.test_client() as c:
                r = c.post("/api/email/draft", json=payload, headers={"X-API-Key": API_KEY} if API_KEY else {})
                j = r.get_json()
                if r.status_code != 200 or not j.get("ok"):
                    return jsonify({"ok": False, "message": j.get("error","draft failed")}), 500
                draft = j["draft"]; msg = j.get("message","")
        reply = reply_text or "Draft ready."
        preview = ["Draft staged:", f"To: {', '.join(draft.get('to', []))}", f"Subject: {draft.get('subject','')}", "", draft.get("body","")]
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot", "text": reply, "time": time.time()})
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender":"bot", "text": "\n".join(preview), "time": time.time()})
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

    if intent == "desktop_task":
        instruction = resp.get("instruction") or prompt
        ok_plan, plan_or_err = _plan_desktop_instruction(instruction)
        if not ok_plan:
            _add_history_entry({"id": f"b-{int(time.time()*1000)}","sender":"bot","text": plan_or_err,"time": time.time()})
            return jsonify({"ok": False, "error": plan_or_err}), 500
        exec_ok, logs, listed, err = _execute_desktop_plan(plan_or_err)
        if not exec_ok:
            _add_history_entry({"id": f"b-{int(time.time()*1000)}","sender":"bot","text": err,"time": time.time()})
            return jsonify({"ok": False, "plan": plan_or_err, "logs": logs, "error": err}), 500
        reply = resp.get("reply") or "Done."
        _add_history_entry({"id": f"b-{int(time.time()*1000)}","sender":"bot","text": reply,"time": time.time()})
        return jsonify({"ok": True, "message": reply, "plan": plan_or_err, "logs": logs, "result": listed}), 200


    if intent == "open_app" and app_name:
        sync = request.args.get("sync") == "1"
        if sync:
            success, msg = _open_app_by_name_from_llm(app_name)
            full_reply = f"{reply_text} ({msg})"
            _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": full_reply, "time": time.time()})
            return jsonify({"ok": success, "message": full_reply}), (200 if success else 500)
        def bg_open(name, prompt_text):
            success_bg, msg_bg = _open_app_by_name_from_llm(name)
            _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": f"{reply_text} ({msg_bg})", "time": time.time()})
        t = threading.Thread(target=bg_open, args=(app_name, prompt), daemon=True)
        t.start()
        return jsonify({"ok": True, "message": f"{reply_text} (Opening queued: {app_name})"}), 202

    # default chat
    _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": reply_text, "time": time.time()})
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
        return jsonify({"ok": True, "query": query, "count": len(messages), "summary": result}), 200
    except Exception as e:
        app.logger.exception("Summarize failed")
        return jsonify({"ok": False, "error": f"Summarize failed: {e}"}), 500

@app.route("/api/search", methods=["POST", "OPTIONS"])
def api_search():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    ok_req, errmsg = _require_api_key(request)
    if not ok_req:
        return jsonify({"ok": False, "error": errmsg}), 401
    data = request.get_json(force=True, silent=True) or {}
    q = (data.get("query") or "").strip()
    k = int(data.get("k") or SEARCH_MAX_RESULTS)
    if not q:
        return jsonify({"ok": False, "error": "Provide 'query'."}), 400
    ok_s, results_or_err = _google_search(q, k=k)
    if not ok_s:
        msg = f"Search failed: {results_or_err}"
        _add_history_entry({"id": f"b-{int(time.time()*1000)}","sender":"bot","text": msg,"time": time.time()})
        return jsonify({"ok": False, "error": msg}), 500
    md = _render_search_results_markdown(q, results_or_err)
    tts = _render_search_results_text(q, results_or_err)
    _add_history_entry({"id": f"b-{int(time.time()*1000)}","sender":"bot","text": "Here’s what I found:", "time": time.time()})
    _add_history_entry({"id": f"b-{int(time.time()*1000)}","sender":"bot","text": md, "time": time.time()})
    return jsonify({"ok": True, "query": q, "results": results_or_err, "readable": tts, "markdown": md}), 200

@app.route("/api/desktop/run", methods=["POST", "OPTIONS"])
def api_desktop_run():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    ok_req, errmsg = _require_api_key(request)
    if not ok_req:
        return jsonify({"ok": False, "error": errmsg}), 401
    data = request.get_json(force=True, silent=True) or {}
    instruction = (data.get("instruction") or "").strip()
    if not instruction:
        return jsonify({"ok": False, "error": "provide 'instruction'"}), 400
    _add_history_entry({"id": f"u-{int(time.time()*1000)}", "sender": "user", "text": instruction, "time": time.time()})
    ok_plan, plan_or_err = _plan_desktop_instruction(instruction)
    if not ok_plan:
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": plan_or_err, "time": time.time()})
        return jsonify({"ok": False, "error": plan_or_err}), 500
    exec_ok, logs, listed, err = _execute_desktop_plan(plan_or_err)
    reply = plan_or_err.get("reply") or ("Done." if exec_ok else "Failed.")
    if exec_ok:
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": reply, "time": time.time()})
        return jsonify({"ok": True, "plan": plan_or_err, "logs": logs, "result": listed, "message": reply}), 200
    else:
        _add_history_entry({"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": err, "time": time.time()})
        return jsonify({"ok": False, "plan": plan_or_err, "logs": logs, "error": err}), 500

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("FLASK_PORT", 5003)), debug=False, threaded=True)
