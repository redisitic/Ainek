// Landing.jsx
import React, { useEffect, useRef, useState } from "react";

// Usage:
// <Landing apiBase="http://127.0.0.1:5003" apiKey={process.env.REACT_APP_FLASK_API_KEY} />
export default function Landing({ apiBase = "http://127.0.0.1:5003", apiKey = null }) {
  const [prompt, setPrompt] = useState("");
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const listRef = useRef(null);

  const base = apiBase?.replace(/\/$/, "");

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [history]);

  const append = (entry) => setHistory((h) => [...h, entry]);

  async function sendPrompt() {
    if (!prompt.trim()) return;
    const text = prompt.trim();
    append({ id: `u-${Date.now()}`, sender: "user", text, time: new Date().toISOString() });
    setPrompt("");
    setLoading(true);
    setError(null);

    try {
      const headers = { "Content-Type": "application/json" };
      if (apiKey) headers["X-API-Key"] = apiKey;

      const res = await fetch(`${base}/api/open`, {
        method: "POST",
        headers,
        body: JSON.stringify({ prompt: text }),
      });

      const json = await res.json();
      if (!res.ok) {
        const msg = json?.error || json?.message || `HTTP ${res.status}`;
        throw new Error(msg);
      }

      const reply = json?.message || (json?.ok ? "OK" : "Failed");
      append({ id: `b-${Date.now()}`, sender: "rude-bot", text: reply, time: new Date().toISOString() });
    } catch (e) {
      console.error(e);
      setError(e.message || "Request failed");
      append({ id: `b-err-${Date.now()}`, sender: "rude-bot", text: `Error: ${e.message}` });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      <header className="bg-white border-b p-3 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Rude Assistant — Frontend</h1>
        <div className="text-sm text-gray-500">Backend: {base}</div>
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
                  {m.sender === "rude-bot" ? <strong>RudeBot:</strong> : null} {m.text}
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

          <button onClick={sendPrompt} disabled={loading} className="bg-blue-600 text-white px-4 py-2 rounded-lg disabled:opacity-60">
            Send
          </button>
        </div>
        {error && <div className="text-sm text-red-500 mt-2">{error}</div>}
      </footer>
    </div>
  );
}
