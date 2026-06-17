import { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { askStream } from "../api/client";
import { resizeImage, compressChatImage } from "../utils/image";
import { formatCreatorName, formatMessageText } from "../utils/format";
import { buildCreatorWelcomeBody } from "../utils/creatorWelcome";
import { PreviewCard } from "./PreviewCard";
import "./ChatPanel.css";
import { CreatorSettingsModal } from "./CreatorSettingsModal";

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

const ResetIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true" xmlns="http://www.w3.org/2000/svg">
    <path d="M4 7V3H8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M5.6 9A7 7 0 1 1 5 14.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const PersonaIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true" xmlns="http://www.w3.org/2000/svg">
    <path d="M12 12A4 4 0 1 0 12 4a4 4 0 0 0 0 8Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M5 20a7 7 0 0 1 14 0" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
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

const GROUNDING_REDIRECT_HOSTS = new Set([
  "vertexaisearch.cloud.google.com",
  "vertexaisearch.cloud.googleusercontent.com",
]);

function normalizeSourceUrl(rawUrl = "", title = "") {
  const url = String(rawUrl || "").trim();
  if (!url) return "";

  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./i, "").toLowerCase();
    const isRedirect = GROUNDING_REDIRECT_HOSTS.has(host) || parsed.pathname.includes("grounding-api-redirect");
    if (!isRedirect) return url;

    for (const key of ["url", "q", "target", "dest", "destination", "redirect", "redirect_url"]) {
      const candidate = parsed.searchParams.get(key);
      if (candidate && /^https?:\/\//i.test(candidate)) {
        return candidate;
      }
    }

    const bareTitle = String(title || "").trim();
    if (/^(?:www\.)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:\/[^\s]*)?$/i.test(bareTitle)) {
      return /^https?:\/\//i.test(bareTitle) ? bareTitle : `https://${bareTitle}`;
    }
  } catch {
    return url;
  }

  return url;
}

function getDomainLabel(url = "") {
  try {
    return new URL(normalizeSourceUrl(url)).hostname.replace(/^www\./i, "").toLowerCase();
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

function escapeRegExp(value = "") {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function cleanupMessageTextPunctuation(text = "") {
  return String(text || "")
    .replace(/\s+([,.;:!?])/g, "$1")
    .replace(/,\s*([.!?])/g, "$1")
    .replace(/([:;,-])\s*(?=\n|$)/g, "")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function stripInlineLinksFromMessageText(text = "", cards = []) {
  let cleaned = String(text || "")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, "$1")
    .trim();

  const replacements = [];
  (cards || []).forEach((card) => {
    const url = normalizeSourceUrl(card?.url || "", card?.title || card?.subtitle || "");
    if (!url) return;
    const domain = getDomainLabel(url);
    const label = cleanCardTitle(
      card?.title || card?.short_snippet || card?.subtitle || domain || "Source",
      url
    );
    if (!label || looksLikeJunkLinkLabel(label)) return;

    const variants = new Set([
      url,
      url.replace(/^https?:\/\//i, ""),
      url.replace(/^https?:\/\//i, "").replace(/\/$/, ""),
      domain,
      domain ? `www.${domain}` : "",
    ]);

    variants.forEach((variant) => {
      const value = String(variant || "").trim();
      if (value) replacements.push({ value, label });
    });
  });

  if (replacements.length > 0) {
    replacements
      .sort((a, b) => b.value.length - a.value.length)
      .forEach(({ value, label }) => {
        const pattern = new RegExp(`(^|[^\\w@/])(${escapeRegExp(value)})(?![\\w])`, "gi");
        cleaned = cleaned.replace(pattern, (_, prefix) => `${prefix}${label}`);
      });
    return cleanupMessageTextPunctuation(cleaned);
  }

  return cleanupMessageTextPunctuation(
    cleaned.replace(/(?:https?:\/\/[^\s)]+|(?:www\.)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:\/[^\s)]*)?)/g, "")
  );
}

function hasVisibleMessageText(message) {
  const text = message?.content ?? message?.text ?? "";
  return String(text).trim().length > 0;
}

const TERMINAL_MESSAGE_STATUSES = new Set(["done", "error"]);
const SEARCH_PENDING_STATUSES = new Set(["websearch", "searching", "grounding", "searching_knowledge", "retrieving_sources", "finding_resources"]);
const PENDING_STATUS_META = {
  thinking: { label: "Finding the angle", variant: "thinking", kind: "compose", ariaLabel: "finding the angle" },
  gathering_context: { label: "Reading the room", variant: "thinking", kind: "context", ariaLabel: "gathering context" },
  routing: { label: "Choosing the lane", variant: "thinking", kind: "route", ariaLabel: "choosing the best response path" },
  checking_rag_mode: { label: "Checking saved sources", variant: "searching", kind: "knowledge", ariaLabel: "checking saved sources" },
  strict_rag: { label: "Searching saved content", variant: "searching", kind: "knowledge", ariaLabel: "searching saved creator content" },
  checking_profiles: { label: "Checking the profile", variant: "searching", kind: "profile", ariaLabel: "checking creator profile" },
  reading_knowledge: { label: "Scanning creator notes", variant: "searching", kind: "knowledge", ariaLabel: "reading saved creator knowledge" },
  searching_knowledge: { label: "Finding the best material", variant: "searching", kind: "search", ariaLabel: "searching creator knowledge" },
  searching_visual_knowledge: { label: "Matching image to content", variant: "searching", kind: "image", ariaLabel: "matching image to saved creator content" },
  retrieving_sources: { label: "Matching sources", variant: "searching", kind: "source", ariaLabel: "matching relevant sources" },
  finding_resources: { label: "Finding the strongest link", variant: "searching", kind: "source", ariaLabel: "finding relevant resources" },
  checking_memory: { label: "Checking the thread", variant: "thinking", kind: "memory", ariaLabel: "checking conversation memory" },
  safety_check: { label: "Handling this carefully", variant: "thinking", kind: "safety", ariaLabel: "handling safety-sensitive message" },
  websearch: { label: "Checking public signals", variant: "searching", kind: "web", ariaLabel: "checking public web signals" },
  analyzing: { label: "Breaking it down", variant: "thinking", kind: "analyze", ariaLabel: "analyzing" },
  preparing_image: { label: "Preparing the image", variant: "thinking", kind: "image", ariaLabel: "preparing image" },
  analyzing_image: { label: "Reading the image", variant: "thinking", kind: "image", ariaLabel: "analyzing image" },
  checking_visual_scope: { label: "Checking the lane", variant: "thinking", kind: "route", ariaLabel: "checking creator fit" },
  visual_reasoning: { label: "Reading the setup", variant: "thinking", kind: "image", ariaLabel: "reasoning over image" },
  formulating_response: { label: "Shaping the reply", variant: "thinking", kind: "compose", ariaLabel: "formulating response" },
  repairing: { label: "Tightening the wording", variant: "thinking", kind: "polish", ariaLabel: "polishing response" },
  wrapping_up: { label: "Locking it in", variant: "thinking", kind: "compose", ariaLabel: "wrapping up" },
  working: { label: "Building the answer", variant: "thinking", kind: "compose", ariaLabel: "building the answer" },
  streaming: { label: "Writing it out", variant: "thinking", kind: "compose", ariaLabel: "writing response" },
};

const STATUS_LABEL_COPY = {
  gathering_context: ["Reading the room", "Checking the setup", "Getting the context"],
  routing: ["Choosing the lane", "Reading the ask", "Picking the path"],
  checking_rag_mode: ["Checking saved sources", "Keeping it to saved material", "Reading the saved lane"],
  strict_rag: ["Searching saved content", "Checking approved material", "Looking through saved sources"],
  checking_profiles: ["Checking the profile", "Reading the profile", "Verifying details"],
  reading_knowledge: ["Scanning creator notes", "Reading saved material", "Checking the content"],
  searching_knowledge: ["Finding the best material", "Looking for the match", "Pulling creator context"],
  searching_visual_knowledge: ["Matching image to content", "Checking saved visual context", "Linking the image to saved material"],
  retrieving_sources: ["Matching sources", "Checking references", "Choosing the receipts"],
  finding_resources: ["Finding the strongest link", "Looking for a useful source", "Checking the best reference"],
  checking_memory: ["Checking the thread", "Reading the conversation", "Using what you shared"],
  safety_check: ["Handling this carefully", "Responding directly", "Keeping this safe"],
  websearch: ["Checking public signals", "Verifying outside context", "Looking beyond saved content"],
  analyzing: ["Breaking it down", "Reading the intent", "Finding the useful angle"],
  preparing_image: ["Preparing the image", "Compressing the upload", "Getting the image ready"],
  analyzing_image: ["Reading the image", "Scanning the visual", "Inspecting the attachment", "Looking at the image"],
  checking_visual_scope: ["Checking the lane", "Testing the fit", "Checking creator fit", "Keeping it in scope"],
  visual_reasoning: ["Reading the setup", "Connecting the dots", "Working through the visual", "Matching image to question"],
  formulating_response: ["Shaping the reply", "Turning it into an answer", "Building the response"],
  repairing: ["Tightening the wording", "Cleaning up the answer", "Making the final pass"],
  wrapping_up: ["Locking it in", "Finishing cleanly", "Getting it ready"],
  working: ["Building the answer", "Keeping it accurate", "Making the cleaner pass", "Checking the final move"],
  streaming: ["Writing it out", "Sending the answer", "Putting it into chat"],
  thinking: ["Finding the angle", "Thinking it through", "Working out the move"],
};

const STATUS_DETAIL_COPY = {
  gathering_context: [
    "Looking at the conversation before answering.",
    "Getting the setup clear so the reply lands properly.",
    "Checking what matters in this chat first.",
  ],
  routing: [
    "Deciding if this needs memory, sources, or a straight answer.",
    "Choosing the cleanest way to answer this.",
    "Separating quick chat from a real research turn.",
  ],
  checking_rag_mode: [
    "Keeping this inside the saved material.",
    "Confirming this should stay with approved content.",
    "Making sure no public lookup gets used for this turn.",
  ],
  strict_rag: [
    "Only checking approved saved material.",
    "Looking inside saved creator material only.",
    "Staying inside the creator's saved content.",
  ],
  checking_profiles: [
    "Verifying the creator details before using them.",
    "Checking the saved profile for the right context.",
    "Making sure the creator details are clean.",
  ],
  reading_knowledge: [
    "Looking through the approved creator material.",
    "Finding what the creator has actually talked about.",
    "Pulling from saved content before making a claim.",
  ],
  searching_knowledge: [
    "Looking for the most relevant creator examples.",
    "Checking saved material for a strong match.",
    "Finding the piece that best answers your question.",
  ],
  searching_visual_knowledge: [
    "Using the image read, then checking saved creator material.",
    "Matching what is visible with approved creator material.",
    "Keeping the visual answer tied to saved context.",
  ],
  retrieving_sources: [
    "Matching the answer to the right source cards.",
    "Checking which references deserve to be attached.",
    "Keeping the receipts aligned with the reply.",
  ],
  finding_resources: [
    "Looking for the most useful resource to attach.",
    "Finding a link that actually supports the point.",
    "Sorting the best match from the noisy ones.",
  ],
  checking_memory: [
    "Checking what you already told this creator.",
    "Using the thread context so it does not restart from zero.",
    "Looking for preferences from this conversation.",
  ],
  safety_check: [
    "Giving this a direct, careful response.",
    "Keeping the reply immediate and careful.",
    "This needs attention before anything else.",
  ],
  websearch: [
    "Checking public info because this needs verification.",
    "Looking outside the saved content for a current fact.",
    "Cross-checking the public trail before answering.",
  ],
  analyzing: [
    "Breaking the question into the useful parts.",
    "Separating the signal from the noise.",
    "Working out what you are really asking for.",
  ],
  analyzing_image: [
    "Reading visible details before answering.",
    "Checking what is actually in the attachment.",
    "Looking closely at the image.",
    "Matching the visual evidence to your question.",
  ],
  preparing_image: [
    "Getting the attachment ready without losing the useful detail.",
    "Keeping the upload light without losing the useful detail.",
    "Getting the attachment ready for a closer look.",
  ],
  checking_visual_scope: [
    "Checking whether this belongs with the creator.",
    "Deciding if this should be answered or redirected.",
    "Separating the ask from the creator's actual lane.",
    "Making sure the creator does not fake expertise.",
  ],
  visual_reasoning: [
    "Connecting the image details to what you asked.",
    "Working through the visible clues before replying.",
    "Turning the visual read into a useful response.",
    "Checking the image evidence before writing.",
  ],
  formulating_response: [
    "Turning the useful bits into a natural reply.",
    "Writing it in the creator's voice without forcing it.",
    "Keeping it clear, specific, and conversational.",
  ],
  repairing: [
    "Cleaning up the response before it hits the chat.",
    "Removing rough edges and formatting issues.",
    "Making sure the answer does not clip awkwardly.",
  ],
  wrapping_up: [
    "Attaching anything useful and finishing cleanly.",
    "Getting the final answer ready.",
    "Making the last pass before sending.",
  ],
  working: [
    "Keeping the reply useful, accurate, and in the creator's voice.",
    "Still lining up the useful context before it hits the chat.",
    "Keeping the thread context together so the answer does not restart from zero.",
    "Making sure the final reply matches what you actually asked.",
    "Keeping the creator voice and the answer aligned.",
  ],
  streaming: [
    "Writing the answer now.",
    "Putting the final version into the chat.",
    "Sending the response through.",
  ],
  thinking: [
    "Finding the cleanest angle.",
    "Thinking through the next useful move.",
    "Working out the right response.",
  ],
};

const NICHE_STATUS_DETAIL_COPY = {
  fitness: {
    analyzing_image: [
      "Checking the visible form, setup, or body context.",
      "Looking at the image before turning it into fitness advice.",
      "Reading the visual without guessing beyond it.",
    ],
    checking_visual_scope: [
      "Checking whether this belongs in the training lane.",
      "Making sure the ask fits the creator's world.",
      "Separating fitness context from off-topic detail.",
    ],
    visual_reasoning: [
      "Turning the visual read into practical training context.",
      "Checking the image before calling the useful move.",
      "Keeping the read specific to what is visible.",
    ],
    formulating_response: [
      "Building the answer like a clean training block.",
      "Keeping the advice practical, not gym-noise heavy.",
      "Turning the idea into something you can actually use.",
    ],
    searching_knowledge: [
      "Looking for the training principle that matches this.",
      "Checking which creator example fits the session.",
      "Finding the fitness point that actually supports this.",
    ],
    reading_knowledge: [
      "Looking for the training principle that matches this.",
      "Checking saved fitness context first.",
      "Reading the creator material before making the call.",
    ],
    websearch: [
      "Checking public proof before using the claim.",
      "Verifying the claim before it goes into the answer.",
      "Looking for outside context only where it helps.",
    ],
  },
  business: {
    analyzing_image: [
      "Reading the visible details before making a business call.",
      "Checking what the image actually shows.",
      "Looking at the attachment before deciding the angle.",
    ],
    checking_visual_scope: [
      "Checking whether this belongs in the business lane.",
      "Making sure the creator does not fake expertise.",
      "Seeing if this should be redirected or answered.",
    ],
    visual_reasoning: [
      "Turning the visual into the business point that matters.",
      "Looking for the decision behind the image.",
      "Connecting the visual to the actual constraint.",
    ],
    formulating_response: [
      "Turning the insight into a useful business move.",
      "Making the answer practical enough to act on.",
      "Keeping the reply focused on the real constraint.",
    ],
    searching_knowledge: [
      "Looking for the closest proven business example.",
      "Finding the creator lesson that fits this ask.",
      "Checking saved business context before answering.",
    ],
    reading_knowledge: [
      "Looking for the closest proven business example.",
      "Reading the business material before making the point.",
      "Checking the saved framework that fits this.",
    ],
    websearch: [
      "Checking the public trail before making the call.",
      "Verifying the outside fact before using it.",
      "Looking for public proof only where the answer needs it.",
    ],
  },
  trading: {
    analyzing_image: [
      "Reading the chart structure and visible markings.",
      "Checking levels, trendlines, and visible context.",
      "Looking at the setup before making the read.",
    ],
    checking_visual_scope: [
      "Checking whether this is a trading read or a creator redirect.",
      "Making sure the ask matches the trading lane.",
      "Keeping the answer inside the creator's world.",
    ],
    visual_reasoning: [
      "Reading the setup before calling the move.",
      "Checking the visible confirmation and risk points.",
      "Turning the chart structure into a cleaner read.",
    ],
    formulating_response: [
      "Checking the setup before calling the move.",
      "Keeping the answer clear before making the read.",
      "Turning the signal into a cleaner response.",
    ],
    searching_knowledge: [
      "Looking for the closest market lesson.",
      "Checking which trading example fits the question.",
      "Finding the saved lesson that supports this.",
    ],
    reading_knowledge: [
      "Looking for the closest market lesson.",
      "Reading the saved market context first.",
      "Checking the creator material before answering.",
    ],
    websearch: [
      "Checking current public context before answering.",
      "Verifying the public signal before using it.",
      "Looking outside saved content only if it matters.",
    ],
  },
  creator: {
    analyzing_image: [
      "Reading the visual before turning it into a creator angle.",
      "Checking what the attachment shows.",
      "Looking for the audience or content signal in the image.",
    ],
    checking_visual_scope: [
      "Checking whether this fits the creator-content lane.",
      "Seeing if this needs a redirect or a real read.",
      "Making sure the response stays in the right world.",
    ],
    visual_reasoning: [
      "Connecting the image to the content or audience point.",
      "Turning the visual into a useful creator move.",
      "Checking the image before shaping the response.",
    ],
    formulating_response: [
      "Packaging the idea so it is useful, not generic.",
      "Turning the point into a cleaner creator move.",
      "Making the reply feel specific to the audience.",
    ],
    searching_knowledge: [
      "Looking for the strongest creator-content match.",
      "Finding the saved example that fits the audience play.",
      "Checking the creator material before attaching a source.",
    ],
    reading_knowledge: [
      "Looking for the strongest creator-content match.",
      "Reading saved creator context first.",
      "Checking the content before making the point.",
    ],
    websearch: [
      "Checking public signals before attaching a source.",
      "Verifying outside context before using it.",
      "Looking for public proof only where it helps the reply.",
    ],
  },
};

function isAssistantPendingMessage(message) {
  const normalizedStatus = String(message?.status || "").toLowerCase();
  return message?.role === "assistant" && !hasVisibleMessageText(message) && !TERMINAL_MESSAGE_STATUSES.has(normalizedStatus);
}

function isAssistantActiveTurn(message) {
  const normalizedStatus = String(message?.status || "").toLowerCase();
  return message?.role === "assistant" && Boolean(normalizedStatus) && !TERMINAL_MESSAGE_STATUSES.has(normalizedStatus);
}

function inferCreatorNiche(creatorName = "", styleFingerprint = {}) {
  const combined = [
    creatorName,
    styleFingerprint?.creator_category,
    styleFingerprint?.category,
    styleFingerprint?.niche,
    styleFingerprint?.domain,
    JSON.stringify(styleFingerprint?.value_model || {}),
  ].join(" ").toLowerCase();

  if (/gym|fitness|muscle|workout|body|sport|soccer|training|testosterone/.test(combined)) return "fitness";
  if (/business|sales|offer|revenue|agency|acquisition|hormozi|martell|startup|entrepreneur/.test(combined)) return "business";
  if (/trading|forex|crypto|stock|market|chart/.test(combined)) return "trading";
  if (/content|audience|creator|youtube|tiktok|instagram/.test(combined)) return "creator";
  return "general";
}

function hashStatusSeed(value = "") {
  const input = String(value || "");
  let hash = 0;
  for (let i = 0; i < input.length; i += 1) {
    hash = ((hash << 5) - hash + input.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

function pickStatusCopy(cycle, index, fallback = "") {
  if (!Array.isArray(cycle) || cycle.length === 0) return fallback;
  return cycle[Math.abs(index) % cycle.length] || fallback;
}

function mergeStatusCopy(...cycles) {
  const seen = new Set();
  const merged = [];
  cycles.flat().forEach((item) => {
    const text = String(item || "").trim();
    if (!text) return;
    const key = text.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    merged.push(text);
  });
  return merged;
}

const SAFE_PENDING_KINDS = new Set([
  "compose",
  "context",
  "route",
  "knowledge",
  "profile",
  "search",
  "source",
  "memory",
  "safety",
  "web",
  "analyze",
  "image",
  "polish",
]);

const SAFE_PENDING_VARIANTS = new Set(["thinking", "searching"]);
const TECHNICAL_PENDING_COPY_RE = /\b(?:gemini(?:\s+vision)?|gpt(?:[-\s]?\d\w*)?|openai|anthropic|claude|llm|large language model|ai model|vision model|backend|server|api|provider|router|pipeline|rag|retrieval|vector|embedding|embeddings|grounding|grounded|corpus|exa|assemblyai|whisper|apify|scraper|transcript|transcripts|ingested|identity layer)\b/i;

function normalizePendingKind(kind, fallback = "compose") {
  const normalized = String(kind || "").trim().toLowerCase();
  return SAFE_PENDING_KINDS.has(normalized) ? normalized : fallback;
}

function normalizePendingVariant(variant, fallback = "thinking") {
  const normalized = String(variant || "").trim().toLowerCase();
  return SAFE_PENDING_VARIANTS.has(normalized) ? normalized : fallback;
}

function getNeutralPendingFallback(status, kind, field = "detail") {
  const normalizedStatus = String(status || "").toLowerCase();
  const normalizedKind = normalizePendingKind(kind);
  const isImage = normalizedKind === "image" || /image|visual/.test(normalizedStatus);
  const isSearch = ["web", "search", "source", "knowledge"].includes(normalizedKind);

  if (field === "label") {
    if (isImage) return "Reading the image";
    if (normalizedKind === "safety") return "Handling this carefully";
    if (isSearch) return "Checking what matters";
    if (normalizedKind === "memory") return "Checking the thread";
    if (normalizedKind === "context") return "Reading the room";
    if (normalizedKind === "route") return "Choosing the lane";
    if (normalizedKind === "polish") return "Cleaning it up";
    return "Building the answer";
  }

  if (field === "ariaLabel") {
    return getNeutralPendingFallback(status, kind, "label").toLowerCase();
  }

  if (isImage) return "Looking at the attachment before writing the reply.";
  if (normalizedKind === "safety") return "Giving this a direct, careful response.";
  if (isSearch) return "Checking the strongest context before answering.";
  if (normalizedKind === "memory") return "Using this conversation so the reply does not restart from zero.";
  if (normalizedKind === "context") return "Looking at the conversation before answering.";
  if (normalizedKind === "route") return "Choosing the cleanest way to answer this.";
  if (normalizedKind === "polish") return "Making the final wording cleaner.";
  return "Keeping the response aligned with your question.";
}

function sanitizePendingCopy(value, fallback = "", meta = {}) {
  const text = String(value || "").trim();
  const safeFallback = fallback || getNeutralPendingFallback(meta.status, meta.kind, meta.field);
  if (!text) return safeFallback;
  if (TECHNICAL_PENDING_COPY_RE.test(text)) return safeFallback;
  return text;
}

function sanitizeOptionalPendingCopy(value, meta = {}) {
  const text = String(value || "").trim();
  if (!text) return "";
  return sanitizePendingCopy(text, getNeutralPendingFallback(meta.status, meta.kind, meta.field), meta);
}

function sanitizePendingCopyCycle(cycle, meta = {}) {
  return (Array.isArray(cycle) ? cycle : [cycle])
    .map((item) => sanitizeOptionalPendingCopy(item, meta))
    .filter(Boolean);
}

function getNicheStatusDetail(status, niche, index = 0) {
  const normalized = String(status || "thinking").toLowerCase();
  const nicheCopy = NICHE_STATUS_DETAIL_COPY[niche]?.[normalized];
  return pickStatusCopy(nicheCopy, index, "");
}

function normalizePendingStatus(status, options = {}) {
  const normalized = String(status || "").toLowerCase();
  const isStrictRag = Boolean(options.strictRag);
  const hasImages = Boolean(options.hasImages);

  if (isStrictRag) {
    if (normalized === "websearch" || normalized === "grounding" || normalized === "searching") {
      return hasImages ? "searching_visual_knowledge" : "strict_rag";
    }
    if (normalized === "checking_profiles") {
      return "checking_rag_mode";
    }
    if (normalized === "finding_resources") {
      return "retrieving_sources";
    }
  }

  if (hasImages) {
    if (normalized === "routing" || normalized === "gathering_context") {
      return "checking_visual_scope";
    }
    if (normalized === "searching_knowledge" || normalized === "reading_knowledge") {
      return "searching_visual_knowledge";
    }
    if (normalized === "working") {
      return "visual_reasoning";
    }
  }

  return normalized;
}

function getPendingStatusMeta(status, options = {}) {
  const normalized = normalizePendingStatus(status, options);
  const baseMeta = PENDING_STATUS_META[normalized] || {
    label: SEARCH_PENDING_STATUSES.has(normalized) ? "Searching" : "Finding the angle",
    variant: SEARCH_PENDING_STATUSES.has(normalized) ? "searching" : "thinking",
    kind: SEARCH_PENDING_STATUSES.has(normalized) ? "search" : "compose",
    ariaLabel: normalized === "typing" ? "typing" : "thinking",
  };
  const niche = inferCreatorNiche(options.creatorName, options.creatorStyleFingerprint);
  const tick = Math.abs(Number(options.tick || 0));
  const seed = hashStatusSeed(`${options.seed || ""}:${normalized}:${options.creatorName || ""}`);
  const backendMeta = options.statusMeta || {};
  const safeKind = normalizePendingKind(backendMeta.kind, baseMeta.kind);
  const safeVariant = normalizePendingVariant(backendMeta.variant, baseMeta.variant);
  const labelMeta = { status: normalized, kind: safeKind, field: "label" };
  const detailMeta = { status: normalized, kind: safeKind, field: "detail" };
  const ariaMeta = { status: normalized, kind: safeKind, field: "ariaLabel" };
  const baseLabel = sanitizePendingCopy(baseMeta.label, getNeutralPendingFallback(normalized, safeKind, "label"), labelMeta);
  const baseDetail = sanitizeOptionalPendingCopy(
    getNicheStatusDetail(normalized, niche, seed + tick + 2),
    detailMeta
  );
  const backendLabel = sanitizeOptionalPendingCopy(backendMeta.label, labelMeta);
  const backendDetail = sanitizeOptionalPendingCopy(backendMeta.detail, detailMeta);
  const labelCycle = mergeStatusCopy(
    sanitizePendingCopyCycle(STATUS_LABEL_COPY[normalized] || [baseLabel], labelMeta),
    backendLabel
  );
  const detailCycle = mergeStatusCopy(
    baseDetail,
    sanitizePendingCopyCycle(STATUS_DETAIL_COPY[normalized] || STATUS_DETAIL_COPY.thinking, detailMeta),
    backendDetail
  );
  const label = sanitizePendingCopy(
    pickStatusCopy(labelCycle, seed + tick, backendLabel || baseLabel),
    baseLabel,
    labelMeta
  );
  const detail = sanitizePendingCopy(
    pickStatusCopy(detailCycle, seed + tick + 1, baseDetail || backendDetail || ""),
    getNeutralPendingFallback(normalized, safeKind, "detail"),
    detailMeta
  );

  return {
    ...baseMeta,
    variant: safeVariant,
    kind: safeKind,
    ariaLabel: sanitizePendingCopy(backendMeta.ariaLabel || baseMeta.ariaLabel, label.toLowerCase(), ariaMeta),
    label,
    detail,
  };
}

function isGreetingLikeMessage(text = "") {
  const normalized = String(text || "").trim().toLowerCase();
  if (!normalized) return false;
  return new Set([
    "hi",
    "hello",
    "hey",
    "hey there",
    "hi there",
    "yo",
    "hiya",
    "howdy",
    "sup",
    "what's up",
    "whats up",
    "good morning",
    "good afternoon",
    "good evening",
  ]).has(normalized);
}

function buildEmptyAssistantFallback(question, userName) {
  const safeUserName = String(userName || "").trim();
  if (isGreetingLikeMessage(question)) {
    return safeUserName && safeUserName !== "You"
      ? `Hey ${safeUserName}, what's on your mind?`
      : "Hey, what's on your mind?";
  }

  return "The reply did not come through cleanly. Try sending that again.";
}

function buildFriendlyChatError(rawMessage) {
  const message = String(rawMessage || "Something went wrong.");
  if (/approve content to continue/i.test(message)) {
    return {
      message: "Changes were detected for this creator. Review and confirm the current approvals before chatting again.",
      needsApproval: true,
    };
  }
  if (/reply is already being generated|chat_turn_in_progress/i.test(message)) {
    return {
      message: "The last reply is still finishing. Give it a second before sending another message.",
      needsApproval: false,
    };
  }
  return {
    message: `Sorry, something went wrong: ${message}`,
    needsApproval: false,
  };
}

function inferPlatformFromUrl(url = "") {
  const domain = getDomainLabel(url);
  if (domain.includes("youtube.com") || domain.includes("youtu.be")) return "youtube";
  if (domain.includes("instagram.com")) return "instagram";
  if (domain.includes("tiktok.com")) return "tiktok";
  if (domain.includes("facebook.com")) return "facebook";
  if (domain.includes("twitter.com") || domain.includes("x.com")) return "twitter";
  return "web";
}

function cleanSourceSnippet(value = "") {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function normalizeSourceEntries(citations = [], cards = []) {
  const seen = new Set();
  const normalized = [];

  (citations || []).forEach((citation, idx) => {
    const url = normalizeSourceUrl(citation?.url || "", citation?.title || citation?.text || "");
    if (!url) return;
    const urlKey = url.toLowerCase();
    if (seen.has(urlKey)) return;
    seen.add(urlKey);
    const title = cleanCardTitle(citation?.title || citation?.text || "Source", url);
    normalized.push({
      id: citation?.id || `citation-${idx}`,
      url,
      title,
      snippet: cleanSourceSnippet(citation?.snippet || citation?.text || ""),
      domain: getDomainLabel(url),
      platform: citation?.platform || inferPlatformFromUrl(url),
      kind: "grounded",
    });
  });

  (cards || []).forEach((card, idx) => {
    const url = normalizeSourceUrl(card?.url || "", card?.title || card?.short_snippet || card?.subtitle || "");
    if (!url) return;
    const urlKey = url.toLowerCase();
    if (seen.has(urlKey)) return;
    seen.add(urlKey);
    const title = cleanCardTitle(
      card?.title || card?.short_snippet || card?.subtitle || "Source",
      url
    );
    normalized.push({
      id: card?.id || `card-${idx}`,
      url,
      title,
      snippet: cleanSourceSnippet(card?.short_snippet || card?.subtitle || ""),
      domain: getDomainLabel(url),
      platform: card?.platform || inferPlatformFromUrl(url),
      kind: "closest",
    });
  });

  return normalized
    .map((source, idx) => {
      return {
        ...source,
        id: source.id || `source-${idx}`,
      };
    })
    .filter(Boolean);
}

const MIN_IMAGE_ZOOM = 0.75;
const MAX_IMAGE_ZOOM = 3;
const IMAGE_ZOOM_STEP = 0.25;
const CHAT_IMAGE_ATTACHMENT_LIMIT = 1;

export function ChatPanel({
  creatorId,
  threadId, // New prop
  creatorDisplayName = "Creator",
  creatorStyleFingerprint = {},
  topK,
  maxDistance,
  messages,
  setMessages,
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
  const [localLoading, setLocalLoading] = useState(false);
  const [pendingFeedbackTick, setPendingFeedbackTick] = useState(0);
  const [selectedImages, setSelectedImages] = useState([]);
  const contentScrollRef = useRef(null);
  const scrollFrameRef = useRef(null);
  const lastAutoScrollAtRef = useRef(0);
  const fileInputRef = useRef(null);
  const chatImageInputRef = useRef(null);
  const sendLockRef = useRef(false);
  const [activeAvatarEdit, setActiveAvatarEdit] = useState(null);
  const [activeImage, setActiveImage] = useState(null);
  const [imageZoom, setImageZoom] = useState(1);
  const [attachmentError, setAttachmentError] = useState(null);
  const errorTimeoutRef = useRef(null);
  const displayCreatorName = formatCreatorName(creatorDisplayName);
  const displayUserName = String(userName || "").trim() || "You";

  const hasActiveAssistantTurn = messages.some(isAssistantActiveTurn);
  const loading = localLoading || hasActiveAssistantTurn;
  const normalizedSearchMode = String(searchMode || "hybrid").toLowerCase() === "ingested"
    ? "ingested_only"
    : String(searchMode || "hybrid").toLowerCase();
  const webSearchDisabled = normalizedSearchMode === "ingested_only";

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

    // Enforce a single image per chat turn so visual reasoning stays focused.
    const currentCount = selectedImages.length;
    const availableSlots = CHAT_IMAGE_ATTACHMENT_LIMIT - currentCount;

    if (availableSlots <= 0) {
      showAttachmentError("Only one image can be attached.");
      return;
    }

    const filesToAdd = sizedFiles.slice(0, availableSlots);
    if (sizedFiles.length > filesToAdd.length) {
      showAttachmentError("Only one image can be attached.");
    }

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
    if (!contentScrollRef.current) return undefined;
    const now = typeof performance !== "undefined" ? performance.now() : Date.now();
    if (loading && now - lastAutoScrollAtRef.current < 48) return undefined;
    lastAutoScrollAtRef.current = now;

    const schedule = typeof window !== "undefined" && window.requestAnimationFrame
      ? window.requestAnimationFrame.bind(window)
      : (callback) => setTimeout(callback, 16);

    if (scrollFrameRef.current == null) {
      scrollFrameRef.current = schedule(() => {
        scrollFrameRef.current = null;
        const scroller = contentScrollRef.current;
        if (scroller) {
          scroller.scrollTo({ top: scroller.scrollHeight, behavior: "auto" });
        }
      });
    }
    return undefined;
  }, [messages, loading]);

  useEffect(() => {
    return () => {
      if (scrollFrameRef.current != null) {
        const cancel = typeof window !== "undefined" && window.cancelAnimationFrame
          ? window.cancelAnimationFrame.bind(window)
          : clearTimeout;
        cancel(scrollFrameRef.current);
        scrollFrameRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!loading) {
      setPendingFeedbackTick(0);
      return undefined;
    }
    const interval = window.setInterval(() => {
      setPendingFeedbackTick((value) => value + 1);
    }, 1000);
    return () => window.clearInterval(interval);
  }, [loading]);

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
    if ((!q && selectedImages.length === 0) || loading || sendLockRef.current) return;

    sendLockRef.current = true;
    setLocalLoading(true);
    const attachmentsToSend = [...selectedImages];
    const hasImagesToSend = attachmentsToSend.length > 0;
    const userMessageId = Date.now();
    const assistantMessageId = userMessageId + 1;
    const previewImages = attachmentsToSend.map((img) => ({
      url: img.previewUrl,
      detail: "auto",
      preview: true,
    }));

    const userMessage = {
      id: userMessageId,
      role: "user",
      content: q,
      text: q,
      images: previewImages,
      ts: new Date().toISOString(),
    };

    setMessages((m) => [
      ...m,
      userMessage,
      {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        text: "",
        status: hasImagesToSend ? "preparing_image" : "gathering_context",
        turnHasImages: hasImagesToSend,
        turnSearchMode: normalizedSearchMode,
        turnStrictRag: webSearchDisabled,
        feedbackSeed: `${assistantMessageId}-${Math.floor(Math.random() * 1000000)}`,
        ts: new Date().toISOString(),
      },
    ]);

    setInput("");
    setSelectedImages([]);
    setError(null);
    setApprovalRequired(false);

    let imagesPayload = [];

    // Compress images before sending
    try {
      if (hasImagesToSend) {
        const compressedImages = await Promise.all(
          attachmentsToSend.map(img => compressChatImage(img.file))
        );
        imagesPayload = compressedImages.map(img => ({
          data_url: img.dataUrl,
          detail: "auto"
        }));
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === userMessageId ? { ...msg, images: imagesPayload } : msg
          )
        );
        window.setTimeout(() => {
          attachmentsToSend.forEach((img) => {
            if (img.previewUrl) URL.revokeObjectURL(img.previewUrl);
          });
        }, 1000);
      }
    } catch (err) {
      setError("Failed to process images: " + err.message);
      const friendlyMessage = "Sorry, I could not process that image. Try a smaller JPG, PNG, or WebP.";
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantMessageId
            ? { ...msg, content: friendlyMessage, text: friendlyMessage, status: "error" }
            : msg
        )
      );
      setLocalLoading(false);
      sendLockRef.current = false;
      return;
    }

    try {
      const history = messages
        .filter((m) => m.role !== "system-notice")
        .map((m) => ({
          role: m.role,
          content: m.content ?? m.text ?? "",
        }));

      const updateAssistantMessage = (updates = {}) => {
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantMessageId ? { ...msg, ...updates } : msg
          )
        );
      };

      await askStream({
        creator_id: creatorId,
        thread_id: threadId,
        question: q,
        top_k: topK,
        max_distance: maxDistance,
        messages: history,
        images: imagesPayload.length > 0 ? imagesPayload : undefined,
        onStatus: (status, statusMeta = {}) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId && !hasVisibleMessageText(msg)
                ? { ...msg, status, statusMeta }
                : msg
            )
          );
        },
        onToken: (token, meta = {}) => {
          const tokenText = String(token || "");
          if (!tokenText) return;
          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.id !== assistantMessageId) return msg;
              const currentText = String(msg.content ?? msg.text ?? "");
              const nextText = meta?.replace ? tokenText : `${currentText}${tokenText}`;
              return {
                ...msg,
                content: nextText,
                text: nextText,
                status: meta?.replace ? "wrapping_up" : "streaming",
              };
            })
          );
        },
        onComplete: (fullAnswer, meta = {}) => {
          const finalText = typeof meta.finalContent === "string" && meta.finalContent.length > 0
            ? meta.finalContent
            : fullAnswer;
          const safeFinalText = String(finalText || "").trim() || buildEmptyAssistantFallback(q, displayUserName);
          updateAssistantMessage({
            content: safeFinalText,
            text: safeFinalText,
            status: "done",
            cards: meta.cards || [],
            citations: meta.citations || [],
          });
          setLocalLoading(false);
          if (onInteraction) onInteraction();
        },
        onError: (e) => {
          const { message: friendlyMessage, needsApproval } = buildFriendlyChatError(e?.message);
          setApprovalRequired(needsApproval);
          setError(friendlyMessage);
          setLocalLoading(false);
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: friendlyMessage, text: friendlyMessage, status: "error" }
                : msg
            )
          );
        }
      });

    } catch (e) {
      const { message: friendlyMessage, needsApproval } = buildFriendlyChatError(e?.message);
      setApprovalRequired(needsApproval);
      setError(friendlyMessage);
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantMessageId
            ? { ...msg, content: friendlyMessage, text: friendlyMessage, status: "error" }
            : msg
        )
      );
      setLocalLoading(false);
    } finally {
      sendLockRef.current = false;
    }
  }



  return (
    <div className="gemini-layout">
      {/* Header */}
      <header className="gemini-header">
        <div className="gemini-title">
          <div
            className="header-avatar clickable"
            aria-label="Change bot avatar"
            onClick={() => handleAvatarClick("creator")}
          >
            {creatorAvatarUrl ? (
              <img src={creatorAvatarUrl} alt={creatorDisplayName} className="header-avatar-img" />
            ) : (
              <SparkleIcon />
            )}
          </div>
          <div className="title-copy">
            <span className="title-text">{formatCreatorName(creatorDisplayName)}</span>
            {webSearchDisabled ? (
              <span
                className="search-mode-badge search-mode-badge-off"
                aria-label="Saved sources only."
              >
                Saved
              </span>
            ) : null}
          </div>
        </div>
        <div className="gemini-actions" aria-label="Chat controls">
          <button type="button" onClick={() => setShowSettings(true)} aria-label="Chat settings" data-tooltip="Chat settings" className="action-icon-btn"><SettingsIcon /></button>
          <button type="button" onClick={onResetChat} aria-label="Reset chat" data-tooltip="Reset chat" className="action-icon-btn"><ResetIcon /></button>
          <button type="button" onClick={onChangePersona} aria-label="Persona" data-tooltip="Persona" className="action-icon-btn"><PersonaIcon /></button>
          <button type="button" onClick={onRescrape} aria-label="Edit creator" data-tooltip="Edit creator" className="action-icon-btn"><EditIcon /></button>
        </div>
      </header>

      <main className="chat-stage" aria-label="Conversation">
        {/* Content Area */}
        <div className="gemini-content" ref={contentScrollRef}>
          <div className="messages-stream">
            {messages.length === 0 ? (
              <div className="welcome-message">
                <SparkleIcon />
                <h1>I'm {formatCreatorName(creatorDisplayName)}</h1>
                <p>{buildCreatorWelcomeBody(creatorStyleFingerprint, creatorDisplayName)}</p>
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
              const normalizedStatus = String(m.status || "").toLowerCase();
              const isTypingMessage = isAssistantPendingMessage(m);
              const hasMessageText = hasVisibleMessageText(m);
              const pendingStatus = getPendingStatusMeta(m.status, {
                creatorName: displayCreatorName,
                creatorStyleFingerprint,
                seed: m.feedbackSeed || m.id || idx,
                tick: pendingFeedbackTick + idx,
                hasImages: Boolean(m.turnHasImages),
                strictRag: Boolean(m.turnStrictRag ?? webSearchDisabled),
                statusMeta: m.statusMeta,
              });
              return (
                <div key={m.id ?? idx} className={`msg-row msg-${m.role}${isTypingMessage ? " is-typing" : ""}`}>
                  <div
                    className="msg-avatar clickable"
                    aria-label={`Change ${m.role === "assistant" ? "bot" : "your"} avatar`}
                    onClick={() => handleAvatarClick(m.role === "assistant" ? "creator" : "user")}
                  >
                    {m.role === "assistant" ? (
                      creatorAvatarUrl ? <img src={creatorAvatarUrl} alt={displayCreatorName} className="avatar-img" /> : <SparkleIcon />
                    ) : (
                      userAvatarUrl ? <img src={userAvatarUrl} alt={displayUserName} className="avatar-img" /> : <UserIcon />
                    )}
                  </div>
                  <div className="msg-bubble">
                    <div className="msg-header" style={{ color: m.role === "assistant" ? (visualConfig?.creatorNameColor || "#1a73e8") : (visualConfig?.userNameColor || "#5f6368") }}>
                      <div className="msg-sender">
                        {m.role === "assistant" ? displayCreatorName : displayUserName}
                      </div>
                      {m.ts && (
                        <span className="msg-timestamp">{new Date(m.ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
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

                    {isTypingMessage && m.role === "assistant" && !hasMessageText && (
                      <div
                        className={`msg-pending-bubble is-${pendingStatus.variant} is-${pendingStatus.kind}`}
                        role="status"
                        aria-live="polite"
                        aria-label={`${displayCreatorName} ${pendingStatus.ariaLabel}`}
                      >
                        <span className="msg-pending-icon" aria-hidden="true">
                          <span className="msg-pending-icon-core"></span>
                        </span>
                        <span className="msg-pending-copy">
                          <span className="msg-pending-label">{pendingStatus.label}</span>
                          <span className="msg-pending-detail">{pendingStatus.detail}</span>
                        </span>
                        <span className="msg-pending-dots" aria-hidden="true">
                          <span></span>
                          <span></span>
                          <span></span>
                        </span>
                      </div>
                    )}

                    {/* Text Content / Thinking Indicator */}
                    {hasMessageText ? (
                      <div className="msg-text">
                        {(() => {
                          const rawText = m.content ?? m.text;
                          const text = m.role === "assistant"
                            ? formatMessageText(rawText, creatorDisplayName)
                            : rawText;
                          const isStreamingText = m.role === "assistant" && normalizedStatus === "streaming";
                          if (isStreamingText) {
                            return (
                              <div className="msg-content-wrapper">
                                <div className="msg-text-blocks is-streaming-text">
                                  {text}
                                </div>
                              </div>
                            );
                          }
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
                                } catch {
                                  // Ignore invalid card URLs and fall back to the generic card metadata.
                                }
                                return {
                                  id: card.id || `meta-${idx}`,
                                  url: card.url,
                                  domain,
                                  isVideo,
                                  videoId,
                                  platform,
                                  title: cleanCardTitle(card.title || 'External Resource', card.url),
                                  subtitle: card.subtitle || domain,
                                  short_snippet: card.short_snippet || "",
                                  thumbnail_url: card.thumbnail_url || "",
                                  resource_type: card.resource_type || (isVideo ? "video" : "article"),
                                  action_label: card.action_label || "Open",
                                };
                              })
                            : [];

                          const regex = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s)]+)|((?:www\.)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:\/[^\s)]+)?)/g;
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
                                const trailing = matchUrl.match(/[.,!?;:)]+$/);
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
                              } catch {
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
                                  title: linkTitle,
                                  subtitle: domain,
                                  short_snippet: "",
                                  thumbnail_url: "",
                                  resource_type: isVideo ? "video" : "article",
                                  action_label: "Open",
                                });

                                const inlineLabel = rawUrlMatch
                                  ? getInlineLinkLabel(matchUrl, linkTitle)
                                  : cleanCardTitle(match[1] || linkTitle, matchUrl);

                                if (!looksLikeJunkLinkLabel(inlineLabel)) {
                                  textParts.push(<span key={`text-link-${match.index}`} className="chat-inline-link">{inlineLabel}</span>);
                                }
                              } else {
                                textParts.push(<span key={match.index} className="chat-inline-link">{linkTitle || matchUrl}</span>);
                              }

                              lastIndex = regex.lastIndex;
                            }

                            if (lastIndex < text.length) {
                              textParts.push(text.substring(lastIndex));
                            }
                          } else {
                            displayText = stripInlineLinksFromMessageText(text, explicitCards);
                          }

                          const renderedCards = explicitCards.length > 0
                            ? explicitCards
                            : linkCards.filter((card, idx, arr) => {
                                const key = (card.url || '').toLowerCase();
                                return key && arr.findIndex((item) => (item.url || '').toLowerCase() === key) === idx;
                              });
                          const renderedSources = normalizeSourceEntries(m.citations, renderedCards);

                          return (
                            <div className="msg-content-wrapper">
                              <div className="msg-text-blocks">
                                {textParts.length > 0 ? textParts : displayText}
                              </div>
                              {renderedSources.length > 0 && (
                                <div className="msg-source-row">
                                  {renderedSources.map((source, idx) => (
                                    <a
                                      key={`${source.id}-${idx}`}
                                      href={source.url}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="chat-source-chip"
                                    >
                                      <span className="chat-source-chip-index">{idx + 1}</span>
                                      <span className="chat-source-chip-title">{source.title}</span>
                                      {String(source.title || "").toLowerCase() !== String(source.domain || "").toLowerCase() && (
                                        <span className="chat-source-chip-domain">{source.domain || source.platform}</span>
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

                    {/* Copy button for assistant messages */}
                    {m.role === "assistant" && hasMessageText && normalizedStatus === "done" && (
                      <button
                        className="msg-copy-btn"
                        aria-label="Copy message"
                        onClick={() => {
                          navigator.clipboard.writeText(m.content || m.text || "");
                          const btn = document.activeElement;
                          if (btn) { btn.textContent = "✓ Copied"; setTimeout(() => { btn.textContent = "Copy"; }, 1500); }
                        }}
                      >Copy</button>
                    )}

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

          </div>
        </div>

        {/* Input Area */}
        <div className="gemini-input-area">
          <div className={`input-container ${selectedImages.length > 0 ? "has-files" : ""}`}>

            {/* Attachment Error Toast */}
            <div className={`attachment-error-toast ${attachmentError ? 'visible' : ''}`}>
              {attachmentError}
            </div>

            <div className="input-pill">
              <button
                className="gemini-attach-btn"
                onClick={() => chatImageInputRef.current?.click()}
                disabled={loading}
                type="button"
                aria-label="Attach image"
              >
                <PlusIcon />
              </button>
              {selectedImages.length > 0 && (
                <div className="input-image-previews" aria-label="Attached images">
                  {selectedImages.map((img, idx) => (
                    <div key={img.id || idx} className="preview-chip">
                      <img src={img.previewUrl} alt="attachment" />
                      <button
                        className="preview-remove"
                        onClick={() => removeImage(idx)}
                        type="button"
                        aria-label="Remove image"
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
              <textarea
              className="gemini-input"
              rows={1}
              placeholder={
                messages.length > 0 && messages[messages.length - 1].role === "assistant" && messages[messages.length - 1].meta?.plan_obj?.mode === "CLARIFY"
                  ? (messages[messages.length - 1].meta.plan_obj.next_question || "Answer the question above...")
                  : `Ask ${formatCreatorName(creatorDisplayName)} anything...`
              }
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                // Auto-resize textarea
                e.target.style.height = "auto";
                e.target.style.height = Math.min(e.target.scrollHeight, 150) + "px";
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
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
                aria-label="Send message"
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
            hidden
          />
        </div>
      </main>

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
