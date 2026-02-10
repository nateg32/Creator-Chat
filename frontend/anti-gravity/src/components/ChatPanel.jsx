import { useState, useRef, useEffect } from "react";
import { ask } from "../api/client";
import { resizeImage } from "../utils/image";
import { formatCreatorName, formatMessageText } from "../utils/format";
import "./ChatPanel.css";
import { CreatorSettingsModal } from "./CreatorSettingsModal";

// Icons 
const SparkleIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="12" cy="12" r="11" fill="#4285F4" fillOpacity="0.1" />
    <path d="M12 4L14.4 9.6L20 12L14.4 14.4L12 20L9.6 14.4L4 12L9.6 9.6L12 4Z" fill="#4285F4" />
  </svg>
);

const UserIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="12" cy="12" r="11" fill="#5f6368" fillOpacity="0.1" />
    <circle cx="12" cy="9" r="4" fill="#5f6368" />
    <path d="M5 19C5 15.134 8.134 12 12 12C15.866 12 19 15.134 19 19" stroke="#5f6368" strokeWidth="2" strokeLinecap="round" />
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

const SettingsIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3"></circle>
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
  </svg>
);

export function ChatPanel({
  creatorId,
  threadId, // New prop
  creatorDisplayName = "Creator",
  creatorHandle = "",
  topK,
  maxDistance,
  messages,
  setMessages,
  loading: externalLoading,
  setLoading: setExternalLoading,
  onResetChat,
  onChangePersona,
  onRescrape,
  creatorAvatarUrl = "",
  userAvatarUrl = "",
  onUpdateCreatorAvatar,
  onUpdateUserAvatar,
  onUpdateVisualConfig,
  visualConfig = {},
  userName = "You",
  debug = false,
  onInteraction
}) {
  const [showSettings, setShowSettings] = useState(false);
  const [input, setInput] = useState("");
  const [error, setError] = useState(null);
  const [debugInfo, setDebugInfo] = useState(null);
  const [localLoading, setLocalLoading] = useState(false);
  const messagesEndRef = useRef(null);
  const fileInputRef = useRef(null);
  const [activeAvatarEdit, setActiveAvatarEdit] = useState(null);

  const loading = localLoading;

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
    setLocalLoading(true);
    setError(null);
    if (!debug) setDebugInfo(null);

    try {
      const history = messages.map((m) => ({
        role: m.role,
        content: m.content ?? m.text ?? "",
      }));
      const result = await ask({
        creator_id: creatorId,
        thread_id: threadId, // Pass threadId
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

      if (onInteraction) onInteraction();
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
      setLocalLoading(false); // Reset local loading
    }
  }

  const handleAvatarClick = (type) => {
    setActiveAvatarEdit(type);
    if (fileInputRef.current) {
      fileInputRef.current.click();
    }
  };

  const processImageUpdate = async (e) => {
    const file = e.target.files?.[0];
    if (!file) {
      setActiveAvatarEdit(null);
      return;
    }

    try {
      const base64 = await resizeImage(file);
      if (activeAvatarEdit === "creator") {
        if (onUpdateCreatorAvatar) onUpdateCreatorAvatar(creatorId, base64);
      } else if (activeAvatarEdit === "user") {
        if (onUpdateUserAvatar) onUpdateUserAvatar(base64);
      }
    } catch (err) {
      setError("Failed to process image: " + err.message);
    } finally {
      setActiveAvatarEdit(null);
      // Reset input so the same file can be picked again
      e.target.value = "";
    }
  };

  return (
    <div className="gemini-layout">
      {/* Header */}
      <header className="gemini-header">
        <div className="gemini-title">
          <div
            className="header-avatar clickable"
            title="Change bot avatar"
            onClick={() => handleAvatarClick("creator")}
          >
            {creatorAvatarUrl ? (
              <img src={creatorAvatarUrl} alt={creatorDisplayName} className="header-avatar-img" />
            ) : (
              <SparkleIcon />
            )}
          </div>
          <span className="title-text">{formatCreatorName(creatorDisplayName)}</span>
        </div>
        <div className="gemini-actions">
          <button onClick={() => setShowSettings(true)} title="Settings" className="action-icon-btn"><SettingsIcon /></button>
          <button onClick={onResetChat} title="Reset Chat">Reset Chat</button>
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
              <h1>Hello, I'm {formatCreatorName(creatorDisplayName)}.</h1>
              <p>Ask me anything about my content, or how I can help you today.</p>
            </div>
          ) : (
            messages.map((m, idx) => {
              if (m.role === "system-notice") {
                return (
                  <div key={m.id ?? idx} className="system-notice-row">
                    <span className="system-notice-text">{m.content || m.text}</span>
                  </div>
                );
              }
              return (
                <div key={m.id ?? idx} className={`msg-row msg-${m.role}`}>
                  <div
                    className="msg-avatar clickable"
                    title={`Change ${m.role === "assistant" ? "bot" : "your"} avatar`}
                    onClick={() => handleAvatarClick(m.role === "assistant" ? "creator" : "user")}
                  >
                    {m.role === "assistant" ? (
                      creatorAvatarUrl ? <img src={creatorAvatarUrl} alt={creatorDisplayName} className="avatar-img" /> : <SparkleIcon />
                    ) : (
                      userAvatarUrl ? <img src={userAvatarUrl} alt={userName} className="avatar-img" /> : <UserIcon />
                    )}
                  </div>
                  <div className="msg-bubble">
                    <div className="msg-sender" style={{ color: m.role === "assistant" ? (visualConfig.creatorNameColor || "#4285F4") : (visualConfig.userNameColor || "#5f6368") }}>
                      {m.role === "assistant" ? formatCreatorName(creatorDisplayName) : userName}
                    </div>
                    <div className="msg-text">{formatMessageText(m.content ?? m.text, creatorDisplayName)}</div>
                  </div>
                </div>
              )
            })
          )}

          {loading && (
            <div className="msg-row msg-assistant">
              <div
                className="msg-avatar clickable"
                title="Change bot avatar"
                onClick={() => handleAvatarClick("creator")}
              >
                {creatorAvatarUrl ? <img src={creatorAvatarUrl} alt={creatorDisplayName} className="avatar-img" /> : <SparkleIcon />}
              </div>
              <div className="msg-bubble">
                <div className="msg-sender" style={{ color: visualConfig.creatorNameColor || "#4285F4" }}>{formatCreatorName(creatorDisplayName)}</div>
                <div className="thinking-indicator">
                  <span>Thinking</span>
                  <span className="thinking-dots">...</span>
                </div>
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
            placeholder={`Message ${formatCreatorName(creatorDisplayName)}`}
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
          AI-generated demo trained on publicly available creator content. Not affiliated with or endorsed by the creator.
        </div>

        {/* Hidden file input for avatar updates */}
        <input
          type="file"
          ref={fileInputRef}
          onChange={processImageUpdate}
          accept="image/*"
          hidden
        />
      </div>

      <CreatorSettingsModal
        isOpen={showSettings}
        onClose={() => setShowSettings(false)}
        creatorName={creatorDisplayName}
        creatorAvatarUrl={creatorAvatarUrl}
        visualConfig={visualConfig}
        onUpdateVisualConfig={(newConfig) => onUpdateVisualConfig(creatorId, newConfig)}
        onUpdateCreatorAvatar={async (base64) => {
          if (onUpdateCreatorAvatar) await onUpdateCreatorAvatar(creatorId, base64);
        }}
      />
    </div>
  );
}
