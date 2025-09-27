# app.py
# Flask backend — FASTRouter-only LLM intent detection (no fuzzy search).
# DO NOT hard-code API keys. Set FASTR_BASE and FASTR_API_KEY in your environment.

import os
import time
import threading
import webbrowser
import logging
import json
import time

from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template_string
import pyautogui

# OpenAI client (works with proxy FastRouter by setting base_url)
from openai import OpenAI

load_dotenv()  # loads .env into environment if present

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Optional CORS
try:
    from flask_cors import CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})
except Exception:
    app.logger.info("flask_cors not installed — continuing without it.")

# In-memory chat history
CHAT_HISTORY = []
CHAT_LOCK = threading.Lock()

# Map of known apps -> launch target (URLs or Windows program names)
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
    "store": "ms-windows-store://home"  # optional Windows store protocol
}

HTML = """
<!doctype html>
<title>Rude Assistant</title>
<h2>Rude Assistant (web)</h2>
<form method="post" action="/open">
  <input name="prompt" style="width:700px" autofocus required>
  <button type="submit">Send</button>
</form>
<div id="result">{{ result }}</div>
"""

# ---- Config from environment (FastRouter only) ----
API_KEY = os.environ.get("FLASK_API_KEY", None)
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

FASTR_BASE = os.environ.get("FASTR_BASE", "https://go.fastrouter.ai/api/v1")
FASTR_API_KEY = os.environ.get("FASTR_API_KEY")  # REQUIRED to enable LLM
LLM_MODEL = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-20250514")

if not FASTR_API_KEY:
    app.logger.warning("FASTR_API_KEY is not set. LLM calls are disabled until FASTR_API_KEY is provided.")

llm_client = None
if FASTR_API_KEY:
    llm_client = OpenAI(base_url=FASTR_BASE, api_key=FASTR_API_KEY)


# ---- Helpers ----
def _add_history_entry(entry: dict):
    with CHAT_LOCK:
        CHAT_HISTORY.append(entry)


def _search_and_open(query: str):
    """
    Use Windows Search to find the best match and open it.
    If DRY_RUN is set, simulate only.
    """
    try:
        if DRY_RUN:
            return True, f"(DRY_RUN) Would search and open: {query}"

        # Open Windows search bar
        pyautogui.hotkey("win", "s")
        time.sleep(0.6)

        # Type the query
        pyautogui.typewrite(str(query))
        time.sleep(1.0)  # allow search to populate

        # Press Enter to open the top result
        pyautogui.press("enter")
        time.sleep(1.5)
        return True, f"Searched and opened: {query}"
    except Exception as e:
        app.logger.exception("Error searching and opening app")
        return False, f"Error searching and opening '{query}': {e}"


def _open_mapped_target(key: str):
    """
    Open a mapped APP_MAP target (URL or program).
    """
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
    """
    Given an app name string suggested by the LLM, try:
      1) exact map lookup in APP_MAP
      2) fallback to system search/open (typing the app_name_raw into search)
    """
    app_name_raw = (app_name_raw or "").strip()
    if not app_name_raw:
        return False, "Empty app name"

    key = app_name_raw.lower()
    # attempt exact key match in APP_MAP
    if key in APP_MAP:
        return _open_mapped_target(key)

    # fallback: try direct search and open
    return _search_and_open(app_name_raw)


def _build_messages_for_llm(prompt: str, history_entries: list):
    """Build messages (system + recent history + user)."""
    system_msg = {
        "role": "system",
        "content": (
            "You are RudeBot: curt, sarcastic and blunt. "
            "Your job is to EITHER (A) decide the user intent is to open an application, "
            "or (B) produce a short rude chat reply. "
            "You MUST reply ONLY in JSON with these keys: "
            "\"intent\" (one of \"open_app\" or \"chat\"), "
            "\"app\" (string, name of app or search query — required if intent is \"open_app\"; otherwise null), "
            "\"reply\" (string: the chat text the assistant would say to the user). "
            "Examples (JSON only, no extra text):\n"
            '{"intent":"open_app","app":"instagram","reply":"Sure, opening Instagram. Don\\\'t drool on it."}\n'
            '{"intent":"chat","app":null,"reply":"You\\\'re dull. Ask something real."}\n'
            "If you detect that the user is asking to open something, set intent to \"open_app\" and set \"app\" to the best short app name or search query. "
            "Otherwise set intent to \"chat\" and set \"app\" to null. Reply should be short and rude."
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
    """
    Send prompt+history to the LLM that must return JSON as per system instruction.
    Returns (ok: bool, parsed_dict_or_error_str)
    """
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
        # Try to parse JSON — be tolerant of whitespace
        try:
            parsed = json.loads(text)
            return True, parsed
        except Exception:
            # If not strict JSON, attempt to locate a JSON object substring
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
    """Return (ok: bool, errmsg: str)"""
    if not API_KEY:
        return True, ""
    provided = req.headers.get("X-API-Key") or req.args.get("api_key")
    if not provided:
        return False, "Missing API key"
    if provided != API_KEY:
        return False, "Invalid API key"
    return True, ""


# ---- Routes ----
@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML, result="")


@app.route("/open", methods=["POST"])
def open_route():
    """
    Web form endpoint: free-form chat. LLM decides intent and server acts.
    """
    prompt = request.form.get("prompt", "").strip()
    if not prompt:
        return render_template_string(HTML, result="Please provide a prompt.")

    user_entry = {"id": f"u-{int(time.time()*1000)}", "sender": "user", "text": prompt, "time": time.time()}
    _add_history_entry(user_entry)

    ok, resp = _ask_llm_for_intent(prompt, CHAT_HISTORY)
    if not ok:
        # treat as fallback chat error
        bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": resp, "time": time.time()}
        _add_history_entry(bot_entry)
        return render_template_string(HTML, result=resp)

    # parsed response expected to be a dict with keys 'intent','app','reply'
    intent = resp.get("intent")
    app_name = resp.get("app")
    reply_text = resp.get("reply") or ""

    if intent == "open_app" and app_name:
        success, msg = _open_app_by_name_from_llm(app_name)
        # include the assistant's reply + the opening status
        full_reply = f"{reply_text} ({msg})"
        bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": full_reply, "time": time.time()}
        _add_history_entry(bot_entry)
        return render_template_string(HTML, result=full_reply)
    else:
        # simple chat reply from LLM
        bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": reply_text, "time": time.time()}
        _add_history_entry(bot_entry)
        return render_template_string(HTML, result=reply_text)


@app.route("/api/open", methods=["POST", "OPTIONS"])
def open_api():
    """
    API endpoint used by frontend. Returns JSON.
    """
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
        # LLM error or parse error
        bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": resp, "time": time.time()}
        _add_history_entry(bot_entry)
        return jsonify({"ok": False, "message": resp}), 500

    intent = resp.get("intent")
    app_name = resp.get("app")
    reply_text = resp.get("reply") or ""

    if intent == "open_app" and app_name:
        # support sync param: ?sync=1 to open synchronously (for API clients)
        sync = request.args.get("sync") == "1"
        if sync:
            success, msg = _open_app_by_name_from_llm(app_name)
            full_reply = f"{reply_text} ({msg})"
            bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": full_reply, "time": time.time()}
            _add_history_entry(bot_entry)
            return jsonify({"ok": success, "message": full_reply}), (200 if success else 500)

        # background open
        def bg_open(name, prompt_text):
            success_bg, msg_bg = _open_app_by_name_from_llm(name)
            bot_entry_bg = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": f"{reply_text} ({msg_bg})", "time": time.time()}
            _add_history_entry(bot_entry_bg)

        t = threading.Thread(target=bg_open, args=(app_name, prompt), daemon=True)
        t.start()
        return jsonify({"ok": True, "message": f"{reply_text} (Opening queued: {app_name})"}), 202
    else:
        # normal chat reply
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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("FLASK_PORT", 5003)), debug=False, threaded=True)
