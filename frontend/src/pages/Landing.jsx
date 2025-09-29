import React, { useEffect, useRef, useState, useCallback } from "react";
import ReactMarkdown from "react-markdown";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";

export default function Landing({ apiBase = "http://127.0.0.1:5003", apiKey = null }) {
  const [prompt, setPrompt] = useState("");
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const autoStickRef = useRef(true);
  const lastIdRef = useRef(null);
  const [showJump, setShowJump] = useState(false);

  const composerRef = useRef(null);
  const [composerHeight, setComposerHeight] = useState(80);

  const base = apiBase?.replace(/\/$/, "");
  const BOTTOM_THRESHOLD_PX = 56;

  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    const update = () => setComposerHeight(el.offsetHeight || 80);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    window.addEventListener("resize", update);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", update);
    };
  }, []);

  const scrollToBottom = useCallback((smooth = true) => {
    requestAnimationFrame(() => {
      window.scrollTo({
        top: document.documentElement.scrollHeight,
        behavior: smooth ? "smooth" : "auto",
      });
    });
  }, []);

  useEffect(() => {
    const handle = () => {
      const doc = document.documentElement;
      const scrollTop = window.scrollY || doc.scrollTop || 0;
      const windowBottom = scrollTop + window.innerHeight;
      const fullHeight = doc.scrollHeight;
      const gap = fullHeight - windowBottom;
      const nearBottom = gap <= BOTTOM_THRESHOLD_PX;
      autoStickRef.current = nearBottom;
      setShowJump(!nearBottom);
    };
    window.addEventListener("scroll", handle, { passive: true });
    handle();
    return () => window.removeEventListener("scroll", handle);
  }, []);

  useEffect(() => {
    if (!history?.length) return;
    const last = history[history.length - 1];
    const lastId = last?.id ?? String(history.length);
    const isNew = lastIdRef.current !== lastId;
    if (isNew) {
      lastIdRef.current = lastId;
      if (autoStickRef.current) {
        scrollToBottom(true);
        setTimeout(() => autoStickRef.current && scrollToBottom(false), 80);
      }
    }
  }, [history, scrollToBottom]);

  const fetchHistory = useCallback(async () => {
    try {
      const headers = {};
      if (apiKey) headers["X-API-Key"] = apiKey;
      const res = await fetch(`${base}/api/history`, { headers });
      const data = await res.json();
      if (Array.isArray(data)) setHistory(data);
    } catch {}
  }, [base, apiKey]);

  useEffect(() => {
    let timer;
    const start = async () => {
      await fetchHistory();
      timer = setInterval(fetchHistory, 1000);
    };
    start();
    return () => timer && clearInterval(timer);
  }, [fetchHistory]);

  async function sendPrompt(textOverride = null) {
    const text = (textOverride || prompt).trim();
    if (!text) return;

    setLoading(true);
    setError(null);
    if (!textOverride) setPrompt("");

    try {
      const headers = { "Content-Type": "application/json" };
      if (apiKey) headers["X-API-Key"] = apiKey;

      const res = await fetch(`${base}/api/open`, {
        method: "POST",
        headers,
        body: JSON.stringify({ prompt: text }),
      });

      await res.json();
      if (!res.ok) throw new Error("Request failed");

      await fetchHistory();

      autoStickRef.current = true;
      setShowJump(false);
    } catch (e) {
      setError(e?.message || "Request failed");
      setHistory((h) => [
        ...h,
        {
          id: `b-err-${Date.now()}`,
          sender: "bot",
          text: `Error: ${e?.message || "Request failed"}`,
          time: new Date().toISOString(),
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col">
      {/* Top bar */}
      <div className="sticky top-0 z-30 bg-white/60 dark:bg-zinc-900/50 backdrop-blur-md border-b">
        <div className="w-full max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="text-base">Ainek</Badge>
          </div>
          <div className="text-xs text-muted-foreground">Backend: {base}</div>
        </div>
      </div>

      {/* Chat window */}
      <main className="flex-1 w-full max-w-7xl mx-auto px-4">
        <div className="pt-4 space-y-4" style={{ paddingBottom: `${composerHeight + 16}px` }}>
          {history.map((m) => {
            const isUser = m.sender === "user";
            return (
              <div key={m.id} className={`flex items-start gap-2 ${isUser ? "justify-end" : "justify-start"}`}>
                {!isUser && (
                  <Avatar className="h-8 w-8">
                    <AvatarImage src="/bot-avatar.png" alt="Ainek" />
                    <AvatarFallback>A</AvatarFallback>
                  </Avatar>
                )}
                <Card
                  className={`max-w-[44rem] rounded-2xl shadow-sm ${
                    isUser
                      ? "bg-primary text-primary-foreground rounded-br-sm"
                      : "bg-secondary text-secondary-foreground rounded-bl-sm"
                  }`}
                >
                  <CardContent className="px-4 py-2">
                    <div className="prose prose-sm md:prose-base prose-invert dark:prose-invert">
                      <ReactMarkdown>{m.text || ""}</ReactMarkdown>
                    </div>
                    {m.time && (
                      <div className="text-[10px] text-muted-foreground mt-1">
                        {new Date(m.time).toLocaleTimeString()}
                      </div>
                    )}
                  </CardContent>
                </Card>
                {isUser && (
                  <Avatar className="h-8 w-8">
                    <AvatarImage src="/user-avatar.png" alt="You" />
                    <AvatarFallback>U</AvatarFallback>
                  </Avatar>
                )}
              </div>
            );
          })}

          {loading && (
            <div className="flex items-center gap-2">
              <Avatar className="h-8 w-8">
                <AvatarFallback>A</AvatarFallback>
              </Avatar>
              <div className="px-3 py-2 rounded-2xl bg-muted text-muted-foreground animate-pulse">
                Thinking…
              </div>
            </div>
          )}
        </div>

        {showJump && (
          <div
            className="fixed left-0 right-0 flex justify-center"
            style={{ bottom: `${composerHeight + 16}px` }}
          >
            <Button
              size="sm"
              variant="secondary"
              className="shadow-md bg-white/70 dark:bg-zinc-800/70 backdrop-blur-md"
              onClick={() => {
                autoStickRef.current = true;
                scrollToBottom(true);
                setShowJump(false);
              }}
            >
              Jump to latest
            </Button>
          </div>
        )}
      </main>

      <Separator />

      {/* Composer */}
      <div
        ref={composerRef}
        className="sticky bottom-0 z-30 bg-white/60 dark:bg-zinc-900/50 backdrop-blur-md border-t"
      >
        <div className="w-full max-w-7xl mx-auto px-4 py-4">
          <div className="flex gap-2 items-center">
            <Input
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendPrompt()}
              placeholder="Type here, or just speak — Ainek is always listening"
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
