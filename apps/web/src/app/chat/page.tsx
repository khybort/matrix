"use client";

import { useRef, useState } from "react";

type ToolCall = { name?: string; params?: unknown };
type Message = { role: "user" | "assistant"; content: string; tools?: ToolCall[] };

const COLORS = {
  bg: "#0b0e14",
  panel: "#131822",
  border: "#222a38",
  user: "#1c2638",
  text: "#d7dde8",
  muted: "#7d8799",
  accent: "#5cc8ff",
};

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const sessionId = useRef<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  function scrollDown() {
    requestAnimationFrame(() => {
      scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
    });
  }

  function patchLastAssistant(fn: (m: Message) => Message) {
    setMessages((prev) => {
      const next = [...prev];
      for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].role === "assistant") {
          next[i] = fn(next[i]);
          break;
        }
      }
      return next;
    });
  }

  function handleEvent(event: string, data: string) {
    if (event === "session") {
      try {
        sessionId.current = JSON.parse(data).session_id ?? sessionId.current;
      } catch {}
    } else if (event === "token") {
      patchLastAssistant((m) => ({ ...m, content: m.content + data }));
      scrollDown();
    } else if (event === "tool_call") {
      try {
        const tc = JSON.parse(data) as ToolCall;
        patchLastAssistant((m) => ({ ...m, tools: [...(m.tools ?? []), tc] }));
      } catch {}
    } else if (event === "error") {
      let msg = data;
      try {
        msg = JSON.parse(data).message ?? data;
      } catch {}
      patchLastAssistant((m) => ({ ...m, content: m.content + `\n\n⚠️ ${msg}` }));
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text },
      { role: "assistant", content: "" },
    ]);
    setStreaming(true);
    scrollDown();

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: sessionId.current, surface: "web" }),
      });
      if (!res.body) throw new Error("no response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE frames are separated by a blank line.
        let sep: number;
        while ((sep = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          let event = "message";
          const dataLines: string[] = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
          }
          handleEvent(event, dataLines.join("\n"));
        }
      }
    } catch (e) {
      patchLastAssistant((m) => ({ ...m, content: m.content + `\n\n⚠️ ${e}` }));
    } finally {
      setStreaming(false);
      scrollDown();
    }
  }

  return (
    <div style={{ minHeight: "100vh", background: COLORS.bg, color: COLORS.text,
      display: "flex", flexDirection: "column", fontFamily: "system-ui, sans-serif" }}>
      <header style={{ padding: "14px 20px", borderBottom: `1px solid ${COLORS.border}`,
        display: "flex", alignItems: "baseline", gap: 12 }}>
        <strong style={{ fontSize: 16 }}>Matrix Brain</strong>
        <span style={{ color: COLORS.muted, fontSize: 13 }}>
          read-only · ask anything about the system
        </span>
        <a href="/" style={{ marginLeft: "auto", color: COLORS.accent, fontSize: 13 }}>
          ← dashboard
        </a>
      </header>

      <div ref={scroller} style={{ flex: 1, overflowY: "auto", padding: 20,
        display: "flex", flexDirection: "column", gap: 14, maxWidth: 900,
        width: "100%", margin: "0 auto" }}>
        {messages.length === 0 && (
          <div style={{ color: COLORS.muted, fontSize: 14, marginTop: 40 }}>
            Ask e.g. <em>“What’s our open BTC exposure and why?”</em>,{" "}
            <em>“Which agent lessons are currently set to avoid?”</em>,{" "}
            <em>“Summarize the news graph around ETH this week.”</em>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} style={{ alignSelf: m.role === "user" ? "flex-end" : "flex-start",
            maxWidth: "85%", background: m.role === "user" ? COLORS.user : COLORS.panel,
            border: `1px solid ${COLORS.border}`, borderRadius: 10, padding: "10px 14px" }}>
            {m.tools && m.tools.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
                {m.tools.map((t, j) => (
                  <span key={j} style={{ fontSize: 11, color: COLORS.accent,
                    border: `1px solid ${COLORS.border}`, borderRadius: 6, padding: "2px 6px" }}>
                    🔧 {(t.name ?? "").replace("mcp__matrix__", "")}
                  </span>
                ))}
              </div>
            )}
            <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.5, fontSize: 14 }}>
              {m.content || (m.role === "assistant" && streaming ? "…" : "")}
            </div>
          </div>
        ))}
      </div>

      <div style={{ borderTop: `1px solid ${COLORS.border}`, padding: 16 }}>
        <div style={{ maxWidth: 900, margin: "0 auto", display: "flex", gap: 10 }}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Ask the system anything…"
            rows={1}
            style={{ flex: 1, resize: "none", background: COLORS.panel, color: COLORS.text,
              border: `1px solid ${COLORS.border}`, borderRadius: 10, padding: "12px 14px",
              fontSize: 14, fontFamily: "inherit", outline: "none" }}
          />
          <button
            onClick={send}
            disabled={streaming || !input.trim()}
            style={{ background: streaming ? COLORS.border : COLORS.accent, color: "#06121f",
              border: "none", borderRadius: 10, padding: "0 20px", fontWeight: 600,
              cursor: streaming || !input.trim() ? "default" : "pointer", fontSize: 14 }}
          >
            {streaming ? "…" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
