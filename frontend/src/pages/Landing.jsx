// Landing.jsx
import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { isSpeechSupported, speak, startRecognition } from "../voice/speechService";

// Usage:
// <Landing apiBase="http://127.0.0.1:5003" apiKey={process.env.REACT_APP_FLASK_API_KEY} />
export default function Landing({ apiBase = "http://127.0.0.1:5003", apiKey = null }) {
  const [prompt, setPrompt] = useState("");
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const listRef = useRef(null);
  const [listening, setListening] = useState(false);
  const recogStopRef = useRef(null);
  const navigate = useNavigate();
  const [tone, setTone] = useState("rude"); // rude | friendly | professional | excited

  const base = apiBase?.replace(/\/$/, "");

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [history]);

  const append = (entry) => setHistory((h) => [...h, entry]);

  function speakOptsForTone(t) {
    const map = {
      rude: { rate: 1, pitch: 1 },
      friendly: { rate: 1, pitch: 1.15 },
      professional: { rate: 0.95, pitch: 0.95 },
      excited: { rate: 1.15, pitch: 1.3 },
    };
    return map[t] || map.rude;
  }

  function botLabelForSender(s) {
    if (!s || !s.endsWith("-bot")) return null;
    const toneKey = s.slice(0, -4);
    switch (toneKey) {
      case "rude":
        return "RudeBot";
      case "friendly":
        return "FriendlyBot";
      case "professional":
        return "ProBot";
      case "excited":
        return "ExcitedBot";
      default:
        return "Assistant";
    }
  }

  async function sendPrompt(textOverride = null) {
    const raw = (textOverride ?? prompt).trim();
    if (!raw) return;
    const text = raw;
    append({ id: `u-${Date.now()}`, sender: "user", text, time: new Date().toISOString() });
    if (!textOverride) setPrompt("");
    setLoading(true);
    setError(null);

    try {
      const headers = { "Content-Type": "application/json" };
      if (apiKey) headers["X-API-Key"] = apiKey;

      const res = await fetch(`${base}/api/open`, {
        method: "POST",
        headers,
        body: JSON.stringify({ prompt: text, tone }),
      });

      const json = await res.json();
      if (!res.ok) {
        const msg = json?.error || json?.message || `HTTP ${res.status}`;
        throw new Error(msg);
      }

      const reply = json?.message || (json?.ok ? "OK" : "Failed");
      append({ id: `b-${Date.now()}`, sender: `${tone}-bot`, text: reply, time: new Date().toISOString() });
      // Speak the assistant reply if speech is supported
      if (isSpeechSupported() && reply) {
        speak(reply, speakOptsForTone(tone));
      }
    } catch (e) {
      console.error(e);
      setError(e.message || "Request failed");
      append({ id: `b-err-${Date.now()}`, sender: `${tone}-bot`, text: `Error: ${e.message}` });
      if (isSpeechSupported() && e?.message) {
        speak(`Error: ${e.message}`, speakOptsForTone(tone));
      }
    } finally {
      setLoading(false);
    }
  }

  function handleLocalIntent(text) {
    const t = text.toLowerCase().trim();
    if (t.startsWith("open signup")) {
      navigate("/signup");
      append({ id: `sys-${Date.now()}`, sender: `${tone}-bot`, text: "Opening signup page" });
      speak("Opening signup page", speakOptsForTone(tone));
      return true;
    }
    if (t.startsWith("open login")) {
      navigate("/login");
      append({ id: `sys-${Date.now()}`, sender: `${tone}-bot`, text: "Opening login page" });
      speak("Opening login page", speakOptsForTone(tone));
      return true;
    }
    if (t.startsWith("open home") || t.startsWith("go home")) {
      navigate("/");
      append({ id: `sys-${Date.now()}`, sender: `${tone}-bot`, text: "Opening home" });
      speak("Opening home", speakOptsForTone(tone));
      return true;
    }
    return false;
  }

  function startVoiceCapture() {
    if (!isSpeechSupported()) {
      setError("Speech recognition is not supported in this browser");
      return;
    }
    setListening(true);
    const stop = startRecognition({
      interimResults: false,
      onResult: ({ transcript, isFinal }) => {
        if (!transcript) return;
        // Try local intents first
        if (handleLocalIntent(transcript)) return;
        // Auto-send immediately with captured transcript; no need to click
        if (isFinal) {
          setTimeout(() => sendPrompt(transcript), 25);
        }
      },
      onError: (err) => {
        console.error("speech error", err);
        setError(typeof err === "string" ? err : err?.message || "Speech error");
        setListening(false);
      },
      onEnd: () => {
        setListening(false);
      },
    });
    // Save stopper to allow UI to stop recognition
    recogStopRef.current = stop;
    return stop;
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      <header className="bg-white border-b p-3 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Rude Assistant — Frontend</h1>
        <div className="flex items-center gap-3">
          <div className="text-sm text-gray-500">Backend: {base}</div>
          <select
            value={tone}
            onChange={(e) => setTone(e.target.value)}
            className="border rounded-lg px-2 py-1 text-sm"
            title="Assistant tone"
          >
            <option value="rude">Rude</option>
            <option value="friendly">Friendly</option>
            <option value="professional">Professional</option>
            <option value="excited">Excited</option>
          </select>
          {isSpeechSupported() ? (
            <button
              onClick={() => {
                if (listening && recogStopRef.current) {
                  try { recogStopRef.current(); } catch {}
                  return;
                }
                startVoiceCapture();
              }}
              className={`px-3 py-1 rounded-full border text-sm ${
                listening ? "bg-red-600 text-white border-red-600" : "bg-gray-100 text-gray-700"
              }`}
              title={listening ? "Listening… click to stop" : "Click to speak your query"}
            >
              {listening ? "Listening…" : "🎤 Speak"}
            </button>
          ) : (
            <span className="text-xs text-gray-400">Speech unsupported</span>
          )}
        </div>
      </header>

      <main className="flex-1 overflow-hidden">
        <div ref={listRef} className="h-full overflow-y-auto p-4 space-y-3">
          {history.length === 0 && (
            <div className="text-gray-400">
              No activity yet — try "hello", "what is python", or "open instagram"
            </div>
          )}

          {history.map((m) => (
            <div key={m.id} className={`flex ${m.sender === "user" ? "justify-end" : "justify-start"}`}>
              <div
                className={`max-w-xl px-4 py-2 rounded-2xl shadow ${
                  m.sender === "user" ? "bg-blue-600 text-white rounded-br-none" : "bg-gray-900 text-white rounded-bl-none"
                }`}
              >
                <div className="whitespace-pre-wrap">
                  {botLabelForSender(m.sender) ? <strong>{botLabelForSender(m.sender)}:</strong> : null} {m.text}
                </div>
                {m.time && <div className="text-xs text-gray-400 mt-1">{new Date(m.time).toLocaleString()}</div>}
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex justify-start">
              <div className="px-3 py-2 rounded-2xl bg-gray-200 text-gray-700 animate-pulse">Thinking…</div>
            </div>
          )}
        </div>
      </main>

      <footer className="p-3 bg-white border-t">
        <div className="flex gap-2">
          <input
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && sendPrompt()}
            placeholder='Chat or ask to open an app, e.g. "open chrome" or "hey, open store"'
            className="flex-1 rounded-lg border px-3 py-2 outline-none focus:ring focus:ring-blue-200"
          />
          {isSpeechSupported() && (
            <button
              onClick={() => {
                if (listening && recogStopRef.current) {
                  try { recogStopRef.current(); } catch {}
                  return;
                }
                startVoiceCapture();
              }}
              disabled={loading}
              className={`px-3 py-2 rounded-lg border ${listening ? "bg-red-600 text-white border-red-600" : "bg-gray-100"}`}
              title="Speak your query"
            >
              {listening ? "Listening…" : "🎤"}
            </button>
          )}
          <button onClick={sendPrompt} disabled={loading} className="bg-blue-600 text-white px-4 py-2 rounded-lg disabled:opacity-60">
            Send
          </button>
        </div>
        {error && <div className="text-sm text-red-500 mt-2">{error}</div>}
      </footer>
    </div>
  );
}
