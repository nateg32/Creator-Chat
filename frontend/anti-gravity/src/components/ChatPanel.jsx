import { useState, useRef, useEffect } from "react";
import { ask } from "../api/client";
import { resizeImage, compressChatImage } from "../utils/image";
import { formatCreatorName, formatMessageText } from "../utils/format";
import "./ChatPanel.css";
import { CreatorSettingsModal } from "./CreatorSettingsModal";
import { PreviewCard } from "./PreviewCard";

// ── Icons (Restored Colorful Aesthetic) ──────────────────────────────
const SparkleIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M12 4L14.4 9.6L20 12L14.4 14.4L12 20L9.6 14.4L4 12L9.6 9.6L12 4Z" fill="#4285F4" />
  </svg>
);

const PlusIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M12 4V20M4 12H20" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </svg>
);

const UserIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="12" cy="9" r="4" fill="#5f6368" />
    <path d="M5 19C5 15.134 8.134 12 12 12C15.866 12 19 15.134 19 19" stroke="#5f6368" strokeWidth="2" strokeLinecap="round" />
  </svg>
);

const SendIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M2.01 21L23 12L2.01 3L2 10L17 12L2 14L2.01 21Z" fill="currentColor" />
  </svg>
);

const EditIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const SettingsIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M4 21V14M4 10V3M12 21V12M12 8V3M20 21V16M20 12V3M1 14H7M9 8H15M17 16H23" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const ImageIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#64748b" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
  </svg>
);

const XIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
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
  const [selectedImages, setSelectedImages] = useState([]);
  const messagesEndRef = useRef(null);
  const fileInputRef = useRef(null);
  const chatImageInputRef = useRef(null);
  const [activeAvatarEdit, setActiveAvatarEdit] = useState(null);
  const [activeImage, setActiveImage] = useState(null);
  const [attachmentError, setAttachmentError] = useState(null);
  const errorTimeoutRef = useRef(null);

  const loading = localLoading;

  // Image handlers for Chat
  const handleChatImageSelect = (e) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;

    // Filter valid images
    const validFiles = files.filter(f => f.type.startsWith("image/"));
    if (validFiles.length < files.length) {
      setError("Only image files are allowed.");
    }

    // Check sizes
    const sizedFiles = validFiles.filter(f => f.size <= 10 * 1024 * 1024);
    if (sizedFiles.length < validFiles.length) {
      setError("Some files were skipped (max 10MB each).");
    }

    // Enforce max 4 images
    const currentCount = selectedImages.length;
    const availableSlots = 4 - currentCount;

    if (availableSlots <= 0) {
      showAttachmentError("Max 4 images allowed.");
      return;
    }

    const filesToAdd = sizedFiles.slice(0, availableSlots);

    // Create preview objects immediately
    const newAttachments = filesToAdd.map(file => ({
      id: Math.random().toString(36).substr(2, 9),
      file,
      previewUrl: URL.createObjectURL(file)
    }));

    setSelectedImages(prev => [...prev, ...newAttachments]);

    // Reset input so same file can be selected again if needed
    e.target.value = "";
  };

  const showAttachmentError = (msg) => {
    if (errorTimeoutRef.current) clearTimeout(errorTimeoutRef.current);
    setAttachmentError(msg);
    errorTimeoutRef.current = setTimeout(() => {
      setAttachmentError(null);
    }, 2500);
  };

  const removeImage = (index) => {
    setSelectedImages((prev) => {
      const newImages = [...prev];
      const removed = newImages.splice(index, 1)[0];
      if (removed?.previewUrl) {
        URL.revokeObjectURL(removed.previewUrl);
      }
      return newImages;
    });
    setAttachmentError(null);
  };

  // Cleanup object URLs on unmount
  useEffect(() => {
    return () => {
      selectedImages.forEach(img => {
        if (img.previewUrl) URL.revokeObjectURL(img.previewUrl);
      });
      if (errorTimeoutRef.current) clearTimeout(errorTimeoutRef.current);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
    if ((!q && selectedImages.length === 0) || loading) return;

    setLocalLoading(true);
    let imagesPayload = [];

    // Compress images before sending
    try {
      if (selectedImages.length > 0) {
        const compressedImages = await Promise.all(
          selectedImages.map(img => compressChatImage(img.file))
        );
        imagesPayload = compressedImages.map(img => ({
          data_url: img.dataUrl,
          detail: "auto"
        }));
      }
    } catch (err) {
      setError("Failed to process images: " + err.message);
      setLocalLoading(false);
      return;
    }

    const userMessage = {
      id: Date.now(),
      role: "user",
      content: q,
      text: q,
      images: imagesPayload, // Use the compressed dataUrls for consistent local display
      ts: new Date().toISOString(),
    };

    setMessages((m) => [...m, userMessage]);

    // Clear inputs immediately
    setInput("");
    setSelectedImages([]); // This triggers effect cleanup? No, strictly need to revoke? 
    // Actually, we shouldn't revoke immediately if we want to show them in the chat history using the same URLs?
    // Wait, we are replacing them with the compressed dataURLs in the message object above. So we CAN revoke the previews.
    selectedImages.forEach(img => URL.revokeObjectURL(img.previewUrl));

    setError(null);
    if (!debug) setDebugInfo(null);

    try {
      const history = messages
        .filter((m) => m.role !== "system-notice")
        .map((m) => ({
          role: m.role,
          content: m.content ?? m.text ?? "",
        }));
      const result = await ask({
        creator_id: creatorId,
        thread_id: threadId,
        question: q,
        top_k: topK,
        max_distance: maxDistance,
        messages: history,
        debug,
        images: imagesPayload.length > 0 ? imagesPayload : undefined,
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
      setLocalLoading(false);
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

  // Cleanup object URLs on unmount
  useEffect(() => {
    return () => {
      selectedImages.forEach(img => {
        if (img.previewUrl) URL.revokeObjectURL(img.previewUrl);
      });
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
    if ((!q && selectedImages.length === 0) || loading) return;

    setLocalLoading(true);
    let imagesPayload = [];

    // Compress images before sending
    try {
      if (selectedImages.length > 0) {
        const compressedImages = await Promise.all(
          selectedImages.map(img => compressChatImage(img.file))
        );
        imagesPayload = compressedImages.map(img => ({
          data_url: img.dataUrl,
          detail: "auto"
        }));
      }
    } catch (err) {
      setError("Failed to process images: " + err.message);
      setLocalLoading(false);
      return;
    }

    const userMessage = {
      id: Date.now(),
      role: "user",
      content: q,
      text: q,
      images: imagesPayload, // Use the compressed dataUrls for consistent local display
      ts: new Date().toISOString(),
    };

    setMessages((m) => [...m, userMessage]);

    // Clear inputs immediately
    setInput("");
    setSelectedImages([]); // This triggers effect cleanup? No, strictly need to revoke? 
    // Actually, we shouldn't revoke immediately if we want to show them in the chat history using the same URLs?
    // Wait, we are replacing them with the compressed dataURLs in the message object above. So we CAN revoke the previews.
    selectedImages.forEach(img => URL.revokeObjectURL(img.previewUrl));

    setError(null);
    if (!debug) setDebugInfo(null);

    try {
      const history = messages
        .filter((m) => m.role !== "system-notice")
        .map((m) => ({
          role: m.role,
          content: m.content ?? m.text ?? "",
        }));
      const result = await ask({
        creator_id: creatorId,
        thread_id: threadId,
        question: q,
        top_k: topK,
        max_distance: maxDistance,
        messages: history,
        debug,
        images: imagesPayload.length > 0 ? imagesPayload : undefined,
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
          card: result.card,
          cards: result.cards,
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
      setLocalLoading(false);
    }
  }



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
              <h1>I'm {formatCreatorName(creatorDisplayName)}</h1>
              <p>I can help you understand my content, answer questions based on my videos and posts, or just chat about life and business.</p>
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
                    <div className="msg-sender" style={{ color: m.role === "assistant" ? (visualConfig?.creatorNameColor || "#4285F4") : (visualConfig?.userNameColor || "#5f6368") }}>
                      {m.role === "assistant" ? formatCreatorName(creatorDisplayName) : (userName || "User")}
                    </div>

                    {/* Render Images Inside Bubble */}
                    {m.images && m.images.length > 0 && (
                      <div className="msg-images-container">
                        {m.images.map((img, i) => (
                          <div key={i} className="msg-image-wrapper">
                            <img
                              src={img.data_url || img.url}
                              alt="attachment"
                              className="msg-image-content clickable"
                              title="Click to expand"
                              onClick={() => setActiveImage(img.data_url || img.url)}
                              onError={(e) => {
                                e.target.style.display = 'none';
                                e.target.nextSibling.style.display = 'block';
                              }}
                            />
                            <div className="msg-image-fallback" style={{ display: 'none' }}>Image failed to load</div>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Text Content */}
                    {(m.content || m.text) && (
                      <div className="msg-text">{formatMessageText(m.content ?? m.text, creatorDisplayName)}</div>
                    )}

                    {/* Preview Cards */}
                    {m.cards && m.cards.length > 0 && (
                      <div className="msg-cards">
                        {m.cards.map((card, idx) => (
                          <PreviewCard key={idx} card={card} />
                        ))}
                      </div>
                    )}
                    {/* Backward compatibility for single card */}
                    {!m.cards && m.card && <PreviewCard card={m.card} />}
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
        <div className={`input-container ${selectedImages.length > 0 ? "has-files" : ""}`}>

          {/* Attachment Error Toast */}
          <div className={`attachment-error-toast ${attachmentError ? 'visible' : ''}`}>
            {attachmentError}
          </div>

          {selectedImages.length > 0 && (
            <div className="input-image-previews">
              {selectedImages.map((img, idx) => (
                <div key={img.id || idx} className="preview-chip">
                  <img src={img.previewUrl} alt="attachment" />
                  <button
                    className="preview-remove"
                    onClick={() => removeImage(idx)}
                    type="button"
                    title="Remove image"
                  >
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="18" y1="6" x2="6" y2="18"></line>
                      <line x1="6" y1="6" x2="18" y2="18"></line>
                    </svg>
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="input-pill">
            <button
              className="gemini-attach-btn"
              onClick={() => chatImageInputRef.current?.click()}
              title="Attach image"
              disabled={loading}
              type="button"
              aria-label="Attach files"
            >
              <PlusIcon />
            </button>
            <input
              className="gemini-input"
              placeholder={`Ask ${formatCreatorName(creatorDisplayName)} anything...`}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") send();
              }}
              onPaste={async (e) => {
                const items = e.clipboardData.items;
                const files = [];
                for (const item of items) {
                  if (item.type.startsWith("image/")) {
                    files.push(item.getAsFile());
                  }
                }
                if (files.length > 0) {
                  e.preventDefault();
                  // Mock event for handleChatImageSelect
                  handleChatImageSelect({ target: { files } });
                }
              }}
              disabled={loading}
            />
            <button
              className="gemini-send-btn"
              onClick={send}
              disabled={(!input.trim() && selectedImages.length === 0) || loading}
              type="button"
            >
              <SendIcon />
            </button>
          </div>
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

        {/* Image Modal Overlay */}
        {activeImage && (
          <div className="image-modal-overlay" onClick={() => setActiveImage(null)}>
            <div className="image-modal-content" onClick={(e) => e.stopPropagation()}>
              <img src={activeImage} alt="Full size" />
              <button className="image-modal-close" onClick={() => setActiveImage(null)}>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M18 6L6 18M6 6L18 18" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            </div>
          </div>
        )}
        {/* Hidden file input for chat images */}
        <input
          type="file"
          ref={chatImageInputRef}
          onChange={handleChatImageSelect}
          accept="image/*"
          multiple
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
