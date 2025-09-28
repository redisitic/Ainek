import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

// shadcn/ui components
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";

export default function Landing({ apiBase = "http://127.0.0.1:5003", apiKey = null }) {
  const [prompt, setPrompt] = useState("");
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // ✅ Use the ScrollArea ref directly (shadcn forwards it to the viewport)
  const listRef = useRef(null);
  // ✅ Bottom sentinel that we always scroll into view
  const bottomRef = useRef(null);

  const base = apiBase?.replace(/\/$/, "");

  // Always stick to the newest message
  useEffect(() => {
    // Prefer sentinel for robust behavior
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });

    // Fallback: direct scroll if needed
    if (listRef.current) {
      const el = listRef.current;
      // Some shadcn builds wrap viewport; this keeps us safe
      const viewport =
        el.querySelector?.("[data-radix-scroll-area-viewport]") || el;
      viewport.scrollTo({
        top: viewport.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [history, loading]);

  const append = (entry) => setHistory((h) => [...h, entry]);

  async function sendPrompt(textOverride = null) {
    const text = (textOverride || prompt).trim();
    if (!text) return;

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
        body: JSON.stringify({ prompt: text }),
      });

      const json = await res.json();
      if (!res.ok) {
        const msg = json?.error || json?.message || `HTTP ${res.status}`;
        throw new Error(msg);
      }

      const reply = json?.summary || json?.message || (json?.ok ? "OK" : "Failed");
      append({ id: `b-${Date.now()}`, sender: "ainek", text: reply, time: new Date().toISOString() });
    } catch (e) {
      console.error(e);
      setError(e.message || "Request failed");
      append({ id: `b-err-${Date.now()}`, sender: "ainek", text: `Error: ${e.message}` });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const headers = {};
        if (apiKey) headers["X-API-Key"] = apiKey;
        const res = await fetch(`${base}/api/history`, { headers });
        if (!res.ok) return;
        const data = await res.json();
        setHistory(data);
      } catch (e) {
        console.error("History poll failed:", e);
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [base, apiKey]);

  return (
    <div className="flex flex-col h-screen">
      {/* Top bar */}
      <div className="sticky top-0 z-20 bg-white/60 dark:bg-zinc-900/50 backdrop-blur-md px-[4rem] supports-[backdrop-filter:blur(0px)]:bg-white/50 border-b">
        <div className="w-full px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="text-base text-[1.25rem]">Ainek</Badge>
          </div>
          <div className="text-xs text-muted-foreground">Backend: {base}</div>
        </div>
      </div>

      {/* Conversation area */}
      <div className="flex-1 bg-muted/20">
        <div className="mx-auto max-w-7xl w-full h-full p-4">
          <Card className="h-full">
            <CardContent className="p-0 h-full flex flex-col">
              {history.length === 0 && (
                <div className="px-4 pt-4 text-sm text-muted-foreground">
                  No activity yet — try speaking ("Ainek is always listening…") or type "hello".
                </div>
              )}
              <Separator className="my-0" />
              {/* ✅ Attach ref directly to ScrollArea; ensure it can flex to fill */}
              <ScrollArea ref={listRef} className="flex-1 p-4">
                <div className="space-y-3">
                  {history.map((m) => (
                    <div key={m.id} className={`flex ${m.sender === "user" ? "justify-end" : "justify-start"}`}>
                      <div
                        className={`max-w-[44rem] rounded-2xl shadow-sm px-4 py-2 border transition-colors ${
                          m.sender === "user"
                            ? "bg-primary text-primary-foreground rounded-br-sm"
                            : "bg-secondary text-secondary-foreground rounded-bl-sm"
                        }`}
                      >
                        <div className="prose prose-invert dark:prose-invert prose-p:my-2 prose-pre:my-2">
                          {m.sender === "ainek" ? <strong>Ainek:</strong> : null}{" "}
                          <ReactMarkdown>{m.text}</ReactMarkdown>
                        </div>
                        {m.time && (
                          <div className="text-[10px] text-muted-foreground mt-1">
                            {new Date(m.time).toLocaleString()}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}

                  {loading && (
                    <div className="flex justify-start">
                      <div className="px-3 py-2 rounded-2xl bg-muted text-muted-foreground animate-pulse">
                        Thinking…
                      </div>
                    </div>
                  )}

                  {/* ✅ Bottom sentinel */}
                  <div ref={bottomRef} />
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Composer */}
      <div className="sticky bottom-0 z-20 bg-white/60 dark:bg-zinc-900/50 backdrop-blur-md px-[6rem] supports-[backdrop-filter:blur(0px)]:bg-white/50 border-t">
        <div className="w-full px-4 py-4">
          <div className="flex gap-2 items-center text-[2rem]">
            <Input
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendPrompt()}
              placeholder='Type here, or just speak — Ainek is always listening'
              className="text-[2rem]"
            />
            <Button onClick={() => sendPrompt()} disabled={loading}>
              {loading ? "Sending…" : "Send"}
            </Button>
          </div>
          {error && <div className="text-xs text-destructive mt-2">{error}</div>}
        </div>
      </div>
    </div>
  );
}
