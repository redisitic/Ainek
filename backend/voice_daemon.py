import os
import io
import time
import queue
import wave
import threading

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

VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH")

audio_q = queue.Queue()
tts_q = queue.Queue()
SPEECH_ACTIVE = threading.Event()

try:
    import win32com.client as wincl
    HAS_SAPI = True
except Exception:
    HAS_SAPI = False


def _audio_cb(indata, frames, time_info, status):
    audio_q.put(indata.copy())


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x.astype(np.float32)))))


# ---- STT (Vosk offline) ----
_vosk_ready = False
_vosk_rec = None


def _vosk_init():
    global _vosk_ready, _vosk_rec
    if _vosk_ready:
        return
    if not VOSK_MODEL_PATH or not os.path.isdir(VOSK_MODEL_PATH):
        raise RuntimeError(
            "VOSK_MODEL_PATH is not set or not a directory. Download a Vosk model and set the env var."
        )
    from vosk import Model, KaldiRecognizer

    model = Model(VOSK_MODEL_PATH)
    rec = KaldiRecognizer(model, SAMPLE_RATE)
    _vosk_rec = rec
    _vosk_ready = True


def transcribe(audio_bytes: bytes) -> str:
    _vosk_init()
    import json as pyjson

    with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
        if wf.getframerate() != SAMPLE_RATE:
            raise RuntimeError(
                f"Expected {SAMPLE_RATE} Hz audio for Vosk; got {wf.getframerate()}"
            )
        _vosk_rec.Reset()
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            _vosk_rec.AcceptWaveform(data)
        res = pyjson.loads(_vosk_rec.FinalResult())
        return (res.get("text") or "").strip()


# ---- TTS worker ----
def _speak_once(text: str):
    if HAS_SAPI:
        voice = wincl.Dispatch("SAPI.SpVoice")
        voice.Speak(text)
    else:
        eng = pyttsx3.init()  # fresh engine per utterance avoids lockups
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


# ---- Orchestration ----
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


def listen_loop():
    with sd.InputStream(
        callback=_audio_cb,
        channels=1,
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        dtype="float32",
    ):
        print("Ainek is always listening... speak any time.")
        buf, speaking, last_voice = [], False, time.time()
        while True:
            block = audio_q.get().squeeze()

            if SPEECH_ACTIVE.is_set():
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
    try:
        listen_loop()
    finally:
        tts_q.put(None)
        t.join(timeout=2)
