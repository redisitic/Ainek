from flask import Flask, request, render_template_string, jsonify
import time
import os
import webbrowser
from fuzzywuzzy import process
import pyautogui
import threading

app = Flask(__name__)

try:
    from flask_cors import CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})
except Exception:

    app.logger.warning("flask_cors not installed — responses will include simple CORS headers where needed.")

CHAT_HISTORY = []
CHAT_LOCK = threading.Lock()

APP_MAP = {
    "chrome": "chrome", "firefox": "firefox", "edge": "msedge",
    "word": "Microsoft Word", "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint", "notepad": "notepad",
    "calculator": "calc", "paint": "mspaint",
    "settings": "ms-settings:",
    "youtube": "https://www.youtube.com/",
    "gmail": "https://mail.google.com/",
    "outlook": "outlook",
    "spotify": "spotify",
    "telegram": "Telegram Desktop",
    "explorer": "explorer",
    "task manager": "taskmgr",
    "command prompt": "cmd",
    "terminal": "wt"
}

HTML = """
<!doctype html>
<title>Open App</title>
<h2>Type a prompt (example: "open chrome" or "launch notepad")</h2>
<form method="post" action="/open">
  <input name="prompt" style="width:500px" autofocus required>
  <button type="submit">Open</button>
</form>
<div id="result">{{ result }}</div>
"""

API_KEY = os.environ.get("FLASK_API_KEY", None)
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
def _add_history_entry(entry):
    with CHAT_LOCK:
        CHAT_HISTORY.append(entry)

def _open_app_by_name(app_name_raw):
    """Silent app-opening using fuzzy matching (no TTS, no persona).
       Returns (success: bool, message: str)
    """
    try:
        best = process.extractOne(app_name_raw.lower(), list(APP_MAP.keys()))
        if best:
            best_match_key, score = best
        else:
            best_match_key, score = None, 0

        if score >= 75 and best_match_key:
            target = APP_MAP[best_match_key]
            app_friendly = best_match_key
        else:
            target = app_name_raw
            app_friendly = app_name_raw

        if DRY_RUN:

            msg = f"(DRY_RUN) Would open: {app_friendly} -> {target}"
            return True, msg

        if isinstance(target, str) and (target.startswith("http") or target.endswith(":")):
            webbrowser.open(target)
        else:

            pyautogui.hotkey("win", "s")
            time.sleep(0.8)
            pyautogui.typewrite(str(target))
            time.sleep(0.6)
            pyautogui.press("enter")

        time.sleep(1.5)
        return True, f"Opened: {app_friendly} -> {target}"
    except Exception as e:
        return False, f"Error opening {app_name_raw}: {e}"

def _extract_open_app(prompt_text: str):
    """
    Basic local parser: accepts patterns like:
      - open chrome
      - launch notepad
      - please open chrome
    Returns the extracted app name or None.
    """
    if not prompt_text:
        return None
    low = prompt_text.strip().lower()
    for verb in ("open ", "launch ", "start ", "run "):
        if low.startswith(verb):
            return prompt_text[len(verb):].strip()
    if "open " in low:
        idx = low.find("open ")
        return prompt_text[idx+len("open "):].strip()
    return None

@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML, result="")

@app.route("/open", methods=["POST"])
def open_route():
    prompt = request.form.get("prompt", "").strip()
    if not prompt:
        return render_template_string(HTML, result="Please provide a prompt.")
    app_name = _extract_open_app(prompt)
    if not app_name:
        return render_template_string(HTML, result="I only accept 'open/launch/start/run ...' commands. Nothing else is allowed.")

    success, msg = _open_app_by_name(app_name)
    return render_template_string(HTML, result=msg)

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

@app.route("/api/open", methods=["POST", "OPTIONS"])
def open_api():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200

    ok, errmsg = _require_api_key(request)
    if not ok:
        return jsonify({"ok": False, "error": errmsg}), 401

    data = request.get_json(force=True, silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"ok": False, "error": "no prompt provided"}), 400

    app_name = _extract_open_app(prompt)
    if not app_name:
        return jsonify({"ok": False, "error": "only open/launch/start/run commands are allowed."}), 400

    user_entry = {"id": f"u-{int(time.time()*1000)}", "sender": "user", "text": prompt, "time": time.time()}
    _add_history_entry(user_entry)

    sync = request.args.get("sync") == "1"

    if sync:
        success, msg = _open_app_by_name(app_name)
        bot_entry = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": msg, "time": time.time()}
        _add_history_entry(bot_entry)
        status_code = 200 if success else 500
        return jsonify({"ok": success, "message": msg}), status_code

    def background_open(name, prompt_text):
        success_bg, msg_bg = _open_app_by_name(name)
        bot_entry_bg = {"id": f"b-{int(time.time()*1000)}", "sender": "bot", "text": msg_bg, "time": time.time()}
        _add_history_entry(bot_entry_bg)

    t = threading.Thread(target=background_open, args=(app_name, prompt), daemon=True)
    t.start()

    return jsonify({"ok": True, "message": "Opening queued (running in background)"}), 202

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
    app.run(host="127.0.0.1", port=5003, debug=False, threaded=True)    