import os
import io
import time
import queue
import wave
import threading
import tempfile

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
import pyttsx3

# ---- env ----
load_dotenv()
API_BASE = os.getenv("AINEK_API_BASE", "http://127.0.0.1:5003")
API_KEY = os.getenv("FLASK_API_KEY")

SAMPLE_RATE = 16000
BLOCK_SIZE = 1024
SILENCE_THRESHOLD = 0.01
MAX_RECORD_SECONDS = 15

WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE_ENV = os.getenv("WHISPER_DEVICE", "").strip().lower()

audio_q = queue.Queue()
tts_q = queue.Queue()

SPEECH_ACTIVE = threading.Event()  # TTS speaking -> ignore mic
MIC_MUTED = threading.Event()      # Hotkey toggled mute
MUTEX = threading.Lock()           # tiny guard for prints/state

try:
    import win32com.client as wincl
    HAS_SAPI = True
except Exception:
    HAS_SAPI = False


def _audio_cb(indata, frames, time_info, status):
    audio_q.put(indata.copy())


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x.astype(np.float32)))))


# ---- STT (OpenAI Whisper local) ----
_whisper_ready = False
_whisper_model = None
_whisper_device = "cpu"


def _whisper_init():
    global _whisper_ready, _whisper_model, _whisper_device
    if _whisper_ready:
        return
    import torch
    import whisper

    if WHISPER_DEVICE_ENV in ("cpu", "cuda"):
        _whisper_device = WHISPER_DEVICE_ENV
    else:
        _whisper_device = "cuda" if torch.cuda.is_available() else "cpu"

    _whisper_model = whisper.load_model(WHISPER_MODEL_NAME, device=_whisper_device)
    _whisper_ready = True


def transcribe(audio_bytes: bytes) -> str:
    _whisper_init()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        # Whisper will resample internally; file path is simplest/most robust
        result = _whisper_model.transcribe(
            tmp_path,
            fp16=False if _whisper_device == "cpu" else True,
            language=None,  # autodetect
            condition_on_previous_text=False,
            initial_prompt=None,
            temperature=0.0,
        )
        text = (result.get("text") or "").strip()
        return text
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ---- TTS worker ----
def _speak_once(text: str):
    if not text:
        return
    if HAS_SAPI:
        voice = wincl.Dispatch("SAPI.SpVoice")
        voice.Speak(text)
    else:
        eng = pyttsx3.init()
        try:
            eng.say(text)
            eng.runAndWait()
        finally:
            try:
                eng.stop()
            except Exception:
                pass


def tts_worker():
    while True:
        text = tts_q.get()
        if text is None:
            break
        SPEECH_ACTIVE.set()
        try:
            _speak_once(text)
        finally:
            SPEECH_ACTIVE.clear()
            tts_q.task_done()


def speak(text: str):
    if text:
        tts_q.put(text)


# ---- Backend call + orchestration ----
def handle_phrase(audio: np.ndarray):
    import requests

    wav_bytes = io.BytesIO()
    with wave.open(wav_bytes, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes((audio * 32767).astype(np.int16).tobytes())
    wav_bytes.seek(0)

    text = transcribe(wav_bytes.read())
    if not text:
        return
    print("User:", text)

    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    try:
        r = requests.post(
            f"{API_BASE}/api/open", headers=headers, json={"prompt": text}, timeout=60
        )
        r.raise_for_status()
        j = r.json()
        reply = j.get("summary") or j.get("message") or "Okay."
    except Exception as e:
        reply = f"Error talking to backend: {e}"

    print("Ainek:", reply)
    speak(reply)


# ---- Hotkey (Shift+1) to toggle mute ----
def _toggle_mic():
    with MUTEX:
        if MIC_MUTED.is_set():
            MIC_MUTED.clear()
            print("[Hotkey] Mic UNMUTED")
        else:
            MIC_MUTED.set()
            print("[Hotkey] Mic MUTED")


def hotkey_worker():
    """
    Prefer 'keyboard' if available (simple global hotkeys).
    Fall back to 'pynput' if not.
    """
    try:
        import keyboard  # pip install keyboard (Linux may require root/udev)
        keyboard.add_hotkey('shift+1', _toggle_mic)
        print("Hotkey ready: Shift+1 to mute/unmute")
        keyboard.wait()  # block this thread
    except Exception:
        try:
            from pynput import keyboard as kb  # pip install pynput
            pressed = set()

            def on_press(key):
                try:
                    if key in (kb.Key.shift, kb.Key.shift_l, kb.Key.shift_r):
                        pressed.add('shift')
                    elif hasattr(key, 'char') and key.char == '1':
                        if 'shift' in pressed:
                            _toggle_mic()
                except Exception:
                    pass

            def on_release(key):
                try:
                    if key in (kb.Key.shift, kb.Key.shift_l, kb.Key.shift_r):
                        pressed.discard('shift')
                except Exception:
                    pass

            print("Hotkey ready (pynput): Shift+1 to mute/unmute")
            with kb.Listener(on_press=on_press, on_release=on_release) as listener:
                listener.join()
        except Exception as e:
            print(f"Hotkey disabled (no backend available): {e}")


# ---- Mic listen loop ----
def listen_loop():
    with sd.InputStream(
        callback=_audio_cb,
        channels=1,
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        dtype="float32",
    ):
        print("Ainek is always listening... speak any time. (Shift+1 to mute/unmute)")
        buf, speaking, last_voice = [], False, time.time()
        while True:
            block = audio_q.get().squeeze()

            # If TTS is speaking, drop any buffered input.
            if SPEECH_ACTIVE.is_set():
                buf, speaking = [], False
                continue

            # If mic is muted, keep draining but ignore audio; also clear any partial buffer.
            if MIC_MUTED.is_set():
                if buf or speaking:
                    buf, speaking = [], False
                continue

            buf.append(block)
            if _rms(block) > SILENCE_THRESHOLD:
                speaking = True
                last_voice = time.time()

            timed_out = time.time() - last_voice > 1.5
            too_long = len(buf) > SAMPLE_RATE * MAX_RECORD_SECONDS / BLOCK_SIZE
            if speaking and (timed_out or too_long):
                audio = np.concatenate(buf)
                handle_phrase(audio)
                buf, speaking = [], False


if __name__ == "__main__":
    t = threading.Thread(target=tts_worker, daemon=True)
    t.start()

    hk = threading.Thread(target=hotkey_worker, daemon=True)
    hk.start()

    try:
        listen_loop()
    finally:
        tts_q.put(None)
        t.join(timeout=2)
