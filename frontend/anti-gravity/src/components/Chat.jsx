import { useState } from "react";
import { ask } from "../api/client";

export function Chat({ creatorId, topK, maxDistance, messages, setMessages, loading, setLoading }) {
  const [input, setInput] = useState("");
  const [error, setError] = useState(null);

  async function send() {
    const q = input.trim();
    if (!q || loading) return;

    setMessages((m) => [...m, { role: "user", text: q }]);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const result = await ask({
        creator_id: creatorId,
        question: q,
        top_k: topK,
        max_distance: maxDistance,
      });

      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          text: result.answer,
          retrieved: result.retrieved || [],
        },
      ]);
    } catch (e) {
      setError(e.message);
      setMessages((m) => [
        ...m,
        { role: "assistant", text: `Error: ${e.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat-panel">
      <div className="chat-messages">
        {messages.map((m, idx) => (
          <div key={idx} className={`message message-${m.role}`}>
            <div className="message-role">{m.role.toUpperCase()}</div>
            <div className="message-content">{m.text}</div>
          </div>
        ))}
        {loading && <div className="message-loading">Thinking…</div>}
        {error && <div className="message-error">Error: {error}</div>}
      </div>
      <div className="chat-input">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Type a message…"
          disabled={loading}
        />
        <button onClick={send} disabled={loading || !input.trim()}>
          Send
        </button>
      </div>
    </div>
  );
}
