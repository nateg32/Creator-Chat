import { useState, useRef, useEffect } from "react";
import { ask } from "../api/client";
import "./ChatPanel.css";

export function ChatPanel({
  creatorId,
  creatorDisplayName = "the creator",
  topK,
  maxDistance,
  messages,
  setMessages,
  loading,
  setLoading,
  onResetChat,
  onChangePersona,
  onRescrape,
  debug = false,
}) {
  const [input, setInput] = useState("");
  const [error, setError] = useState(null);
  const [debugInfo, setDebugInfo] = useState(null);
  const messagesEndRef = useRef(null);

  // Auto-scroll to the latest message (ChatGPT-style)
  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, loading]);

  useEffect(() => {
    if (!debug) setDebugInfo(null);
  }, [debug]);

  async function send() {
    const q = input.trim();
    if (!q || loading) return;

    const userMessage = {
      id: Date.now(),
      role: "user",
      content: q,
      text: q,
      ts: new Date().toISOString(),
    };

    setMessages((m) => [...m, userMessage]);
    setInput("");
    setLoading(true);
    setError(null);
    if (!debug) setDebugInfo(null);

    try {
      const history = messages.map((m) => ({
        role: m.role,
        content: m.content ?? m.text ?? "",
      }));
      const result = await ask({
        creator_id: creatorId,
        question: q,
        top_k: topK,
        max_distance: maxDistance,
        messages: history,
        debug,
      });
      if (debug && result.debug_info) setDebugInfo(result.debug_info);

      setMessages((m) => [
        ...m,
        {
          id: Date.now() + 1,
          role: "assistant",
          content: result.answer,
          text: result.answer,
          retrieved: result.retrieved || [],
          ts: new Date().toISOString(),
        },
      ]);
    } catch (e) {
      setError(e.message);
      if (debug) setDebugInfo(null);
      setMessages((m) => [
        ...m,
        {
          id: Date.now() + 2,
          role: "assistant",
          content: `Sorry, something went wrong: ${e.message}`,
          text: `Sorry, something went wrong: ${e.message}`,
          ts: new Date().toISOString(),
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat-panel-card">
      <div className="chat-header">
        <h2>Chat</h2>
        <div className="chat-actions">
          <button onClick={onResetChat} className="link-button">
            Reset chat
          </button>
          <button onClick={onChangePersona} className="link-button">
            Change persona
          </button>
          <button onClick={onRescrape} className="link-button">
            Edit Bot
          </button>
        </div>
      </div>

      <div className="chat-messages">
        {messages.length === 0 ? (
          <div className="chat-empty">
            <p>Ask anything as if you're chatting to {creatorDisplayName}.</p>
          </div>
        ) : (
          messages.map((m, idx) => (
            <div key={m.id ?? idx} className={`message message-${m.role}`}>
              <div className="message-role">{m.role === "user" ? "You" : "Bot"}</div>
              <div className="message-content">
                {m.content ?? m.text}
              </div>
            </div>
          ))
        )}
        {loading && (
          <div className="message message-assistant">
            <div className="message-role">Bot</div>
            <div className="message-content message-loading-bubble">Thinking…</div>
          </div>
        )}
        {error && <div className="message-error">Error: {error}</div>}
        {debug && debugInfo && (
          <details className="chat-debug">
            <summary>Debug: persona, sources, history</summary>
            <pre>{JSON.stringify(debugInfo, null, 2)}</pre>
          </details>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-wrapper">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder={`Ask anything as if you're chatting to ${creatorDisplayName}...`}
          rows={3}
          disabled={loading}
          className="chat-input-textarea"
        />
        <button
          onClick={send}
          disabled={loading || !input.trim()}
          className="send-button"
        >
          Send
        </button>
      </div>
    </div>
  );
}
