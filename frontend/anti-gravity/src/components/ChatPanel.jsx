import { useState, useRef, useEffect } from "react";
import { ask } from "../api/client";
import "./ChatPanel.css";

// Icons 
const SparkleIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M12 4L14.4 9.6L20 12L14.4 14.4L12 20L9.6 14.4L4 12L9.6 9.6L12 4Z" fill="url(#sparkle-gradient)" />
    <defs>
      <linearGradient id="sparkle-gradient" x1="4" y1="4" x2="20" y2="20" gradientUnits="userSpaceOnUse">
        <stop stopColor="#4285F4" />
        <stop offset="1" stopColor="#9B72CB" />
      </linearGradient>
    </defs>
  </svg>
);

const UserIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="#5f6368" xmlns="http://www.w3.org/2000/svg">
    <circle cx="12" cy="8" r="4" />
    <path d="M4 20C4 16 8 14 12 14C16 14 20 16 20 20" stroke="#5f6368" strokeWidth="2" strokeLinecap="round" />
  </svg>
);

const SendIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M2.01 21L23 12L2.01 3L2 10L17 12L2 14L2.01 21Z" fill="currentColor" />
  </svg>
);

const EditIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
  </svg>
);

export function ChatPanel({
  creatorId,
  creatorDisplayName = "Gemini",
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

  // Auto-scroll to the latest message
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
    <div className="gemini-layout">
      {/* Header */}
      <header className="gemini-header">
        <div className="gemini-title">
          <span className="title-text">{creatorDisplayName}</span>
          <span className="badge-pro">PRO</span>
        </div>
        <div className="gemini-actions">
          <button onClick={onResetChat} title="Reset Chat">New Chat</button>
          <button onClick={onChangePersona} title="Change Persona">Persona</button>
          <button onClick={onRescrape} title="Edit Bot" className="action-icon-btn"><EditIcon /></button>
        </div>
      </header>

      {/* Content Area */}
      <div className="gemini-content">
        <div className="messages-stream">
          {messages.length === 0 ? (
            <div className="welcome-message">
              <SparkleIcon />
              <h1>Hello, I'm {creatorDisplayName}.</h1>
              <p>Ask me anything about my content, or how I can help you today.</p>
            </div>
          ) : (
            messages.map((m, idx) => (
              <div key={m.id ?? idx} className={`msg-row msg-${m.role}`}>
                <div className="msg-avatar">
                  {m.role === "assistant" ? <SparkleIcon /> : <UserIcon />}
                </div>
                <div className="msg-bubble">
                  {m.role === "user" ? (
                    <div className="msg-text">{m.content ?? m.text}</div>
                  ) : (
                    <div className="msg-text assistant-text">{m.content ?? m.text}</div>
                  )}
                </div>
              </div>
            ))
          )}

          {loading && (
            <div className="msg-row msg-assistant">
              <div className="msg-avatar"><SparkleIcon /></div>
              <div className="msg-bubble">
                <div className="typing-flashing" />
              </div>
            </div>
          )}

          {error && <div className="error-banner">Error: {error}</div>}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input Area */}
      <div className="gemini-input-area">
        <div className="input-pill">
          <input
            className="gemini-input"
            placeholder="Enter your prompt here"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") send();
            }}
            disabled={loading}
          />
          <button
            className="gemini-send-btn"
            onClick={send}
            disabled={!input.trim() || loading}
          >
            <SendIcon />
          </button>
        </div>
        <div className="gemini-footer-note">
          {creatorDisplayName} may display inaccurate info, so double-check its responses.
        </div>
      </div>
    </div>
  );
}
