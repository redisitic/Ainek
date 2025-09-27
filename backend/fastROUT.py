from flask import Flask, request, render_template_string, jsonify
import time, os, webbrowser
from fuzzywuzzy import process
import pyautogui
import threading

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

app = Flask(__name__)

def _open_app_by_name(app_name_raw):
    """Silent app-opening using fuzzy matching (no TTS, no persona)."""
    best_match_key, score = process.extractOne(app_name_raw.lower(), list(APP_MAP.keys()))
    if score >= 75:
        target = APP_MAP[best_match_key]
        app_friendly = best_match_key
    else:
        target = app_name_raw
        app_friendly = app_name_raw

    try:
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
        return False, f"Error opening {app_friendly}: {e}"

def _extract_open_app(prompt_text: str):
    """
    Basic local parser: accepts patterns like:
      - open chrome
      - launch notepad
      - please open chrome
    Returns the extracted app name or None.
    """
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
    # local parse
    app_name = _extract_open_app(prompt)
    if not app_name:
        return render_template_string(HTML, result="I only accept 'open/launch/start/run ...' commands. Nothing else is allowed.")

    success, msg = _open_app_by_name(app_name)
    return render_template_string(HTML, result=msg)

@app.route("/api/open", methods=["POST"])
def open_api():
    data = request.get_json(force=True, silent=True) or {}
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"ok": False, "error": "no prompt provided"}), 400

    app_name = _extract_open_app(prompt)
    if not app_name:
        return jsonify({"ok": False, "error": "only open/launch/start/run commands are allowed."}), 400

    success, msg = _open_app_by_name(app_name)
    return jsonify({"ok": success, "message": msg})

if __name__ == "__main__":
     app.run(host="127.0.0.1", port=5003, debug=False)