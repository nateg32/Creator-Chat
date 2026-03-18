import { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { ask, askStream } from "../api/client";
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

function looksLikeJunkLinkLabel(label = "") {
  const trimmed = label.trim();
  if (!trimmed) return true;
  if (/^\d{1,4}$/.test(trimmed)) return true;
  if (/^(here|link|source|video|resource)$/i.test(trimmed)) return true;
  if (/^[A-Za-z]{2,5}\d{2,}$/i.test(trimmed)) return true;
  if (/^[A-Za-z0-9_-]{5,12}$/i.test(trimmed) && /\d/.test(trimmed) && !/\s/.test(trimmed)) return true;
  return false;
}

function getDomainLabel(url = "") {
  try {
    return new URL(url).hostname.replace(/^www\./i, "").toLowerCase();
  } catch {
    return "";
  }
}

function cleanCardTitle(title = "", url = "") {
  const cleaned = String(title || "").replace(/\s+/g, " ").trim();
  const domain = getDomainLabel(url);
  const lowered = cleaned.toLowerCase();
  const genericHomeSuffixes = ["home", "homepage", "official site", "official website", "site"];
  const genericTitles = ["external resource", "resource", "link", "source", "website", "site"];

  if (!cleaned) {
    return domain || "External Resource";
  }

  if (domain && genericTitles.includes(lowered)) {
    return domain;
  }

  if (domain) {
    if (lowered === domain) {
      return domain;
    }
    if (genericHomeSuffixes.some((suffix) => lowered === `${domain} ${suffix}` || lowered === `${domain} | ${suffix}` || lowered === `${domain} - ${suffix}`)) {
      return domain;
    }
    if (genericHomeSuffixes.includes(lowered)) {
      return domain;
    }
  }

  return cleaned;
}

function getInlineLinkLabel(url = "", title = "") {
  const cleanedTitle = cleanCardTitle(title, url);
  if (!looksLikeJunkLinkLabel(cleanedTitle)) {
    return cleanedTitle;
  }
  return getDomainLabel(url) || cleanedTitle;
}

function stripInlineLinksFromMessageText(text = "") {
  return String(text || "")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, "$1")
    .replace(/(?:https?:\/\/[^\s)]+|(?:www\.)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:\/[^\s)]*)?)/g, "")
    .replace(/\s+([,.;:!?])/g, "$1")
    .replace(/([:;,-])\s*(?=\n|$)/g, "")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

const MIN_IMAGE_ZOOM = 0.75;
const MAX_IMAGE_ZOOM = 3;
const IMAGE_ZOOM_STEP = 0.25;

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
  onResolveApproval,
  creatorAvatarUrl = "",
  userAvatarUrl = "",
  onUpdateCreatorAvatar,
  onUpdateUserAvatar,
  onUpdateVisualConfig,
  onUpdateSearchMode,
  visualConfig = {},
  searchMode = "hybrid",
  userName = "You",
  debug = false,
  onInteraction
}) {
  const [showSettings, setShowSettings] = useState(false);
  const [input, setInput] = useState("");
  const [error, setError] = useState(null);
  const [approvalRequired, setApprovalRequired] = useState(false);
  const [debugInfo, setDebugInfo] = useState(null);
  const [localLoading, setLocalLoading] = useState(false);
  const [selectedImages, setSelectedImages] = useState([]);
  const messagesEndRef = useRef(null);
  const fileInputRef = useRef(null);
  const chatImageInputRef = useRef(null);
  const [activeAvatarEdit, setActiveAvatarEdit] = useState(null);
  const [activeImage, setActiveImage] = useState(null);
  const [imageZoom, setImageZoom] = useState(1);
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
      // Use 'auto' instead of 'smooth' for streaming performance
      messagesEndRef.current.scrollIntoView({ behavior: "auto" });
    }
  }, [messages, loading]);

  useEffect(() => {
    if (!debug) setDebugInfo(null);
  }, [debug]);

  useEffect(() => {
    if (!activeImage) return undefined;

    setImageZoom(1);
    const originalOverflow = document.body.style.overflow;
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        setActiveImage(null);
        return;
      }
      if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        setImageZoom((current) => Math.min(MAX_IMAGE_ZOOM, current + IMAGE_ZOOM_STEP));
        return;
      }
      if (event.key === "-") {
        event.preventDefault();
        setImageZoom((current) => Math.max(MIN_IMAGE_ZOOM, current - IMAGE_ZOOM_STEP));
        return;
      }
      if (event.key === "0") {
        event.preventDefault();
        setImageZoom(1);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = originalOverflow;
    };
  }, [activeImage]);

  const closeImagePreview = () => {
    setActiveImage(null);
    setImageZoom(1);
  };

  const openImagePreview = (src) => {
    setActiveImage(src);
    setImageZoom(1);
  };

  const adjustImageZoom = (delta) => {
    setImageZoom((current) => Math.min(MAX_IMAGE_ZOOM, Math.max(MIN_IMAGE_ZOOM, current + delta)));
  };

  const openImageInNewTab = () => {
    if (!activeImage || typeof window === "undefined") return;
    window.open(activeImage, "_blank", "noopener,noreferrer");
  };

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
      images: imagesPayload,
      ts: new Date().toISOString(),
    };

    setMessages((m) => [...m, userMessage]);

    // Clear inputs immediately
    setInput("");
    setSelectedImages([]);
    selectedImages.forEach(img => URL.revokeObjectURL(img.previewUrl));

    setError(null);
    setApprovalRequired(false);
    if (!debug) setDebugInfo(null);

    const assistantMessageId = Date.now() + 1;

    // Add an empty assistant message to be filled via stream
    setMessages((m) => [
      ...m,
      {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        text: "",
        ts: new Date().toISOString(),
      },
    ]);

    try {
      const history = messages
        .filter((m) => m.role !== "system-notice")
        .map((m) => ({
          role: m.role,
          content: m.content ?? m.text ?? "",
        }));

      await askStream({
        creator_id: creatorId,
        thread_id: threadId,
        question: q,
        top_k: topK,
        max_distance: maxDistance,
        messages: history,
        images: imagesPayload.length > 0 ? imagesPayload : undefined,
        onToken: (token) => {
          if (token.trim() !== "") {
            setLocalLoading(false); // Stop "Thinking" indicator as soon as actual content arrives
          }
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: msg.content + token, text: msg.text + token }
                : msg
            )
          );
        },
        onComplete: (fullAnswer, meta = {}) => {
          if (meta.cards?.length) {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantMessageId
                  ? { ...msg, cards: meta.cards }
                  : msg
              )
            );
          }
          if (onInteraction) onInteraction();
        },
        onError: (e) => {
          const rawMessage = e?.message || "Something went wrong.";
          const needsApproval = /approve content to continue/i.test(rawMessage);
          const friendlyMessage = needsApproval
            ? "Changes were detected for this creator. Review and confirm the current approvals before chatting again."
            : `Sorry, something went wrong: ${rawMessage}`;
          setApprovalRequired(needsApproval);
          setError(friendlyMessage);
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: friendlyMessage, text: friendlyMessage }
                : msg
            )
          );
        }
      });

    } catch (e) {
      setApprovalRequired(false);
      setError(e.message);
      if (debug) setDebugInfo(null);
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
                    <div className="msg-header" style={{ color: m.role === "assistant" ? (visualConfig?.creatorNameColor || "#1a73e8") : (visualConfig?.userNameColor || "#5f6368") }}>
                      <div className="msg-sender">
                        {m.role === "assistant" ? formatCreatorName(creatorDisplayName) : (userName || "User")}
                      </div>

                      {/* Inline Thinking Indicator */}
                      {m.role === "assistant" && loading && (!m.content && !m.text || (m.content || m.text).trim() === "") && (
                        <div className="thinking-indicator inline-thinking">
                          <div className="dot"></div>
                          <div className="dot"></div>
                          <div className="dot"></div>
                        </div>
                      )}
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
                              loading="lazy"
                              onClick={() => openImagePreview(img.data_url || img.url)}
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

                    {/* Text Content / Thinking Indicator */}
                    {((m.content || m.text) && (m.content || m.text).trim()) ? (
                      <div className="msg-text">
                        {(() => {
                          const text = formatMessageText(m.content ?? m.text, creatorDisplayName);
                          const explicitCards = Array.isArray(m.cards) && m.cards.length > 0
                            ? m.cards.map((card, idx) => {
                                let domain = "web";
                                let isVideo = false;
                                let videoId = null;
                                let platform = "web";
                                try {
                                  const urlObj = new URL(card.url);
                                  domain = getDomainLabel(urlObj.toString());
                                  if (domain.includes('youtube.com') || domain.includes('youtu.be')) {
                                    isVideo = true;
                                    platform = 'youtube';
                                    if (domain.includes('youtube.com')) {
                                      videoId = urlObj.searchParams.get('v') || urlObj.pathname.split('/shorts/')[1];
                                    } else {
                                      videoId = urlObj.pathname.slice(1);
                                    }
                                  }
                                } catch (e) {}
                                return {
                                  id: card.id || `meta-${idx}`,
                                  url: card.url,
                                  domain,
                                  isVideo,
                                  videoId,
                                  platform,
                                  title: cleanCardTitle(card.title || 'External Resource', card.url),
                                };
                              })
                            : [];

                          const regex = /\[([^\]]+)\]\((https?:\/\/[^\s\)]+)\)|(https?:\/\/[^\s\)]+)|((?:www\.)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:\/[^\s\)]+)?)/g;
                          const textParts = [];
                          const linkCards = [];
                          let displayText = text;

                          if (explicitCards.length === 0) {
                            let lastIndex = 0;
                            let match;
                            let linkCount = 0;

                            while ((match = regex.exec(text)) !== null) {
                              let matchUrl = match[2] || match[3] || match[4];
                              const isMarkdownLink = Boolean(match[2]);
                              let rawUrlMatch = !isMarkdownLink;

                              if (matchUrl && !/^https?:\/\//i.test(matchUrl)) {
                                matchUrl = `https://${matchUrl}`;
                              }

                              if (rawUrlMatch) {
                                const trailing = matchUrl.match(/[\.,!?;:)]+$/);
                                if (trailing) {
                                  const punLength = trailing[0].length;
                                  matchUrl = matchUrl.substring(0, matchUrl.length - punLength);
                                  regex.lastIndex -= punLength;
                                }
                              }

                              if (match.index > lastIndex) {
                                textParts.push(text.substring(lastIndex, match.index));
                              }

                              let isValidUrl = false;
                              let domain = "";
                              let isVideo = false;
                              let videoId = null;
                              let platform = "web";
                              let linkTitle = match[1] || "";

                              try {
                                const urlObj = new URL(matchUrl);
                                domain = getDomainLabel(urlObj.toString());

                                if (domain.includes('youtube.com') || domain.includes('youtu.be')) {
                                  isVideo = true;
                                  platform = 'youtube';
                                  if (domain.includes('youtube.com')) {
                                    videoId = urlObj.searchParams.get('v') || urlObj.pathname.split('/shorts/')[1];
                                  } else if (domain.includes('youtu.be')) {
                                    videoId = urlObj.pathname.slice(1);
                                  }
                                } else if (domain.includes('instagram.com')) {
                                  isVideo = matchUrl.includes('/reel/') || matchUrl.includes('/p/');
                                  platform = 'instagram';
                                } else if (domain.includes('tiktok.com')) {
                                  isVideo = matchUrl.includes('/video/') || matchUrl.includes('/@');
                                  platform = 'tiktok';
                                } else if (domain.includes('facebook.com')) {
                                  isVideo = matchUrl.includes('/watch') || matchUrl.includes('/reel');
                                  platform = 'facebook';
                                } else if (domain.includes('twitter.com') || domain.includes('x.com')) {
                                  isVideo = matchUrl.includes('/status/');
                                  platform = 'twitter';
                                }

                                isValidUrl = true;
                                if (!linkTitle) {
                                  const platformLabels = {
                                    youtube: 'YouTube Video',
                                    instagram: 'Instagram Reel',
                                    tiktok: 'TikTok Video',
                                    facebook: 'Facebook Video',
                                    twitter: 'Tweet',
                                    web: 'External Resource'
                                  };
                                  linkTitle = platformLabels[platform] || 'External Resource';
                                }
                                linkTitle = cleanCardTitle(linkTitle, matchUrl);
                              } catch (e) {
                                isValidUrl = false;
                              }

                              if (isValidUrl) {
                                linkCount++;
                                linkCards.push({
                                  id: linkCount,
                                  url: matchUrl,
                                  domain,
                                  isVideo,
                                  videoId,
                                  platform,
                                  title: linkTitle
                                });

                                const inlineLabel = rawUrlMatch
                                  ? getInlineLinkLabel(matchUrl, linkTitle)
                                  : cleanCardTitle(match[1] || linkTitle, matchUrl);

                                if (!looksLikeJunkLinkLabel(inlineLabel)) {
                                  textParts.push(<span key={`text-link-${match.index}`} className="chat-inline-link">{inlineLabel}</span>);
                                }
                              } else {
                                textParts.push(
                                  <a key={match.index} href={matchUrl} target="_blank" rel="noopener noreferrer" className="chat-link">
                                    {linkTitle || matchUrl}
                                  </a>
                                );
                              }

                              lastIndex = regex.lastIndex;
                            }

                            if (lastIndex < text.length) {
                              textParts.push(text.substring(lastIndex));
                            }
                          } else {
                            displayText = stripInlineLinksFromMessageText(text);
                          }

                          const renderedCards = explicitCards.length > 0
                            ? explicitCards
                            : linkCards.filter((card, idx, arr) => {
                                const key = (card.url || '').toLowerCase();
                                return key && arr.findIndex((item) => (item.url || '').toLowerCase() === key) === idx;
                              });

                          return (
                            <div className="msg-content-wrapper">
                              <div className="msg-text-blocks">
                                {textParts.length > 0 ? textParts : displayText}
                              </div>
                              {renderedCards.length > 0 && (
                                <div className="msg-preview-cards">
                                  {renderedCards.map((card, idx) => (
                                    <a key={`${card.id}-${idx}`} href={card.url} target="_blank" rel="noopener noreferrer" className="chat-link-card">
                                      {card.platform === 'youtube' && card.videoId && (
                                        <div className="chat-link-card-thumbnail">
                                          <img src={`https://img.youtube.com/vi/${card.videoId}/mqdefault.jpg`} alt="thumbnail" />
                                          <div className="play-overlay">
                                            <svg width="24" height="24" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg">
                                              <path d="M8 5v14l11-7z" />
                                            </svg>
                                          </div>
                                        </div>
                                      )}
                                      <div className="chat-link-card-content">
                                        <div className="chat-link-card-domain">
                                          {card.platform === 'youtube' ? (
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="#FF0000" xmlns="http://www.w3.org/2000/svg">
                                              <path d="M21.582 6.186a2.693 2.693 0 0 0-1.895-1.908C17.989 3.8 12 3.8 12 3.8s-5.989 0-7.687.478A2.693 2.693 0 0 0 2.418 6.186C1.94 7.894 1.94 11.5 1.94 11.5s0 3.606.478 5.314a2.693 2.693 0 0 0 1.895 1.908c1.698.478 7.687.478 7.687.478s5.989 0 7.687-.478a2.693 2.693 0 0 0 1.895-1.908c.478-1.708.478-5.314.478-5.314s0-3.606-.478-5.314zM9.95 14.814v-6.628L15.694 11.5l-5.744 3.314z" />
                                            </svg>
                                          ) : card.platform === 'instagram' ? (
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="#E1306C" xmlns="http://www.w3.org/2000/svg">
                                              <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z" />
                                            </svg>
                                          ) : card.platform === 'tiktok' ? (
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="#000000" xmlns="http://www.w3.org/2000/svg">
                                              <path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.1v-3.5a6.37 6.37 0 00-.79-.05A6.34 6.34 0 003.15 15.2a6.34 6.34 0 0010.86 4.48V13a8.28 8.28 0 005.58 2.16v-3.45a4.85 4.85 0 01-5.58-1.43V6.69h5.58z" />
                                            </svg>
                                          ) : card.platform === 'facebook' ? (
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="#1877F2" xmlns="http://www.w3.org/2000/svg">
                                              <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z" />
                                            </svg>
                                          ) : card.platform === 'twitter' ? (
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="#000000" xmlns="http://www.w3.org/2000/svg">
                                              <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
                                            </svg>
                                          ) : (
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                              <circle cx="12" cy="12" r="10"></circle>
                                              <line x1="2" y1="12" x2="22" y2="12"></line>
                                              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
                                            </svg>
                                          )}
                                          <span>{card.domain}</span>
                                        </div>
                                        <div className="chat-link-card-title">{card.title}</div>
                                      </div>
                                      {!card.isVideo && (
                                        <div className="chat-link-card-arrow">
                                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <path d="M5 12h14"></path>
                                            <path d="M12 5l7 7-7 7"></path>
                                          </svg>
                                        </div>
                                      )}
                                    </a>
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })()}
                      </div>
                    ) : null}

                    {/* Mode Chip (Subtle debug) */}
                    {debug && m.role === "assistant" && m.meta?.plan_obj && (
                      <div className="mode-chip">
                        {m.meta.plan_obj.mode}
                      </div>
                    )}




                    {/* Switch Creator CTA */}
                    {m.meta?.domain_action === "DECLINE_HANDOFF" && m.meta?.suggestions && (
                      <div className="switch-creator-cta">
                        <div className="cta-label">Suggesting other experts:</div>
                        <div className="suggestion-chips">
                          {m.meta.suggestions.map((s) => (
                            <button key={s.id} onClick={() => onChangePersona(s.id)} className="suggestion-chip">
                              {s.profile_picture_url && <img src={s.profile_picture_url} alt={s.name} />}
                              <span>{formatCreatorName(s.name)}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Greeting Quick Actions */}
                    {m.role === "assistant" && m.meta?.plan_obj?.stage === "GREETING" && idx === messages.length - 1 && (
                      <div className="quick-actions">
                        <button className="quick-action-btn" onClick={() => { setInput("Give me some advice"); }}>Get advice</button>
                        <button className="quick-action-btn" onClick={() => { setInput("Help me build a plan"); }}>Build a plan</button>
                        <button className="quick-action-btn" onClick={() => { setInput("Tell me more about yourself"); }}>Ask about creator</button>
                      </div>
                    )}
                  </div>
                </div>
              )
            })
          )}


          {error && (
            <div className="error-banner">
              <span>Error: {error}</span>
              {approvalRequired && onResolveApproval ? (
                <button type="button" className="quick-action-btn" onClick={onResolveApproval}>
                  Review approvals
                </button>
              ) : null}
            </div>
          )}

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
              placeholder={
                messages.length > 0 && messages[messages.length - 1].role === "assistant" && messages[messages.length - 1].meta?.plan_obj?.mode === "CLARIFY"
                  ? (messages[messages.length - 1].meta.plan_obj.next_question || "Answer the question above...")
                  : `Ask ${formatCreatorName(creatorDisplayName)} anything...`
              }
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
        {activeImage && typeof document !== "undefined" && createPortal(
          <div className="image-modal-overlay" onClick={closeImagePreview}>
            <div className="image-modal-content" onClick={(e) => e.stopPropagation()}>
              <div className="image-modal-topbar">
                <div className="image-modal-meta">
                  <span>Image preview</span>
                  <span>{Math.round(imageZoom * 100)}%</span>
                </div>
                <div className="image-modal-actions">
                  <button
                    className="image-modal-control"
                    onClick={() => adjustImageZoom(-IMAGE_ZOOM_STEP)}
                    type="button"
                    aria-label="Zoom out"
                    disabled={imageZoom <= MIN_IMAGE_ZOOM}
                  >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                      <path d="M5 12H19" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                    </svg>
                  </button>
                  <button className="image-modal-control image-modal-reset" onClick={() => setImageZoom(1)} type="button">
                    Fit
                  </button>
                  <button
                    className="image-modal-control"
                    onClick={() => adjustImageZoom(IMAGE_ZOOM_STEP)}
                    type="button"
                    aria-label="Zoom in"
                    disabled={imageZoom >= MAX_IMAGE_ZOOM}
                  >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                      <path d="M12 5V19M5 12H19" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                    </svg>
                  </button>
                  <button className="image-modal-control image-modal-open" onClick={openImageInNewTab} type="button">
                    Open
                  </button>
                  <button className="image-modal-close" onClick={closeImagePreview} type="button" aria-label="Close image preview">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                      <path d="M18 6L6 18M6 6L18 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>
                </div>
              </div>
              <div className="image-modal-frame">
                <div className="image-modal-stage">
                  <img
                    src={activeImage}
                    alt="Expanded attachment"
                    style={{
                      width: imageZoom > 1 ? `${imageZoom * 100}%` : "auto",
                      maxWidth: imageZoom > 1 ? "none" : "100%",
                      maxHeight: imageZoom > 1 ? "none" : "calc(min(92vh, 980px) - 96px)",
                    }}
                  />
                </div>
              </div>
            </div>
          </div>,
          document.body
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
        searchMode={searchMode}
        onUpdateSearchMode={(mode) => onUpdateSearchMode(creatorId, mode)}
        onUpdateCreatorAvatar={async (base64) => {
          if (onUpdateCreatorAvatar) await onUpdateCreatorAvatar(creatorId, base64);
        }}
      />
    </div>
  );
}
