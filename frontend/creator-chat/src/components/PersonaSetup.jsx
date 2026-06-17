import { useState, useEffect, useMemo, useCallback } from "react";
import { getFingerprintStatus, getQueueItems, getCreatorConfig } from "../api/client";
import { formatCreatorName } from "../utils/format";
import "./PersonaSetup.css";

function clampPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(0, Math.min(100, Math.round(numeric)));
}

function formatFingerprintStage(stage) {
  const labels = {
    preparing: "Preparing",
    research_cache: "Loading saved context",
    link_scan: "Checking public context",
    content_scan: "Reading approved content",
    voice_analysis: "Extracting voice and values",
    persona_agent: "Checking evidence gaps",
    dossier: "Checking evidence gaps",
    synthesis: "Building profile",
    finalizing: "Saving profile",
    complete: "Complete",
    idle: "Ready",
    error: "Error",
  };
  return labels[String(stage || "").toLowerCase()] || "Processing";
}

function formatStageCounter(progress) {
  const index = Number(progress?.stage_index);
  const total = Number(progress?.stage_total);
  if (!Number.isFinite(index) || !Number.isFinite(total) || total <= 0) {
    return "Live progress";
  }
  return `Step ${index} of ${total}`;
}

const EMPTY_TEXT_VALUES = new Set([
  "",
  "none",
  "n/a",
  "na",
  "unknown",
  "not specified",
  "not available",
  "profile in progress.",
  "yes",
  "no",
  "low",
  "medium",
  "high",
  "hybrid",
  "balanced",
  "varied",
]);

const GENERIC_PROFILE_PATTERNS = [
  /\bpublic profile synthesized from ingested content\b/i,
  /\bverified web research\b/i,
  /\bprofile generated from analyzed creator content\b/i,
  /\bfact profile generated from analyzed creator content\b/i,
  /\banalyzed creator\b/i,
];

const PRODUCT_PROFILE_NOISE_PATTERNS = [
  /\b(if you['’]?re new to my channel|welcome back to my channel|without further ado|hey guys)\b/i,
  /\b(my name is|attached the link below|link below|click below)\b/i,
  /\b(forbidden|banned|raw key names|pause markers|lexical markers|high signal words)\b/i,
  /\breplies should move through\b/i,
  /\buses\s+(dashes|semicolons|commas|ellipses|capitalization)\b/i,
  /\b(transcript|yt channel|youtube channel|you can google|stuff you can google|watch this|watch below)\b/i,
  /\b(how i got here|want to scale faster|business owners:|include exact numbers and metrics)\b/i,
  /:\s*(want to|how to|why|what|watch|business owners)\b/i,
  /^include\b/i,
];

function cleanText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "";
    return value >= 0 && value <= 1 ? `${Math.round(value * 100)}%` : String(value);
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value !== "string") return "";
  const clean = value.replace(/\s+/g, " ").trim();
  if (EMPTY_TEXT_VALUES.has(clean.toLowerCase())) return "";
  if (GENERIC_PROFILE_PATTERNS.some((pattern) => pattern.test(clean))) return "";
  if (PRODUCT_PROFILE_NOISE_PATTERNS.some((pattern) => pattern.test(clean))) return "";
  return clean;
}

function getCreatorNameFromConfig(config) {
  return cleanText(config?.name)
    || cleanText(config?.display_name)
    || cleanText(config?.creator?.name)
    || cleanText(config?.creator?.display_name)
    || cleanText(config?.profile?.name)
    || cleanText(config?.config?.name)
    || cleanText(config?.handle)
    || "";
}

function flattenSignals(value) {
  if (Array.isArray(value)) {
    return value.flatMap((item) => flattenSignals(item));
  }
  if (value && typeof value === "object") {
    return Object.values(value).flatMap((item) => flattenSignals(item));
  }
  return [value];
}

function compactSignal(value, maxLength = 150) {
  const clean = cleanText(value);
  if (!clean) return "";
  if (clean.length < 4) return "";
  if (/^\d+%$/.test(clean)) return "";
  if (/^(framework|story|analytical|anecdotal|measured|light)$/i.test(clean)) return "";
  const normalized = clean
    .replace(/\s*;\s*/g, ", ")
    .replace(/\s{2,}/g, " ")
    .trim();
  if (!normalized || normalized.length < 4) return "";
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength - 1).replace(/[\s,.;:]+$/g, "")}...`;
}

function uniqueSignals(values, limit = 5, maxLength = 150) {
  const seen = new Set();
  const out = [];
  flattenSignals(values).forEach((value) => {
    const clean = compactSignal(value, maxLength);
    if (!clean) return;
    const key = clean.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    out.push(clean);
  });
  return out.slice(0, limit);
}

function profileSignals(values, limit = 5, maxLength = 130) {
  return uniqueSignals(values, limit * 2, maxLength)
    .filter((value) => {
      const text = value.toLowerCase();
      if (PRODUCT_PROFILE_NOISE_PATTERNS.some((pattern) => pattern.test(value))) return false;
      if (/^["']?i\b/.test(text) && /[.!?]$/.test(text)) return false;
      if (/^(you|your|we|they)\b/i.test(value) && value.length < 46) return false;
      return true;
    })
    .slice(0, limit);
}

function joinSignals(values, limit = 3) {
  const items = profileSignals(values, limit, 95);
  if (!items.length) return "";
  if (items.length === 1) return lowercaseFirst(items[0]);
  if (items.length === 2) return `${lowercaseFirst(items[0])} and ${lowercaseFirst(items[1])}`;
  return `${items.slice(0, -1).map(lowercaseFirst).join(", ")}, and ${lowercaseFirst(items[items.length - 1])}`;
}

function sentenceFromSignal(displayName, kind, values, fallback = "") {
  const signal = joinSignals(values, 3);
  if (!signal) return fallback;
  const name = displayName || "This creator";
  if (kind === "values") {
    return `${name} consistently frames decisions around ${signal}.`;
  }
  if (kind === "beliefs") {
    return `${name}'s worldview is built around ${signal}.`;
  }
  if (kind === "teaching") {
    return `${name}'s teaching pattern centers on ${signal}, keeping complex ideas easier to apply.`;
  }
  if (kind === "voice") {
    return `${name}'s communication style is shaped by ${signal}, so replies should feel specific rather than generic.`;
  }
  if (kind === "conversation") {
    return `${name}'s chat behavior combines ${signal}, so replies feel like a natural continuation of the creator's content.`;
  }
  if (kind === "domain") {
    return `${name}'s strongest domain signal is ${signal}.`;
  }
  if (kind === "facts") {
    return `${name}'s public profile includes ${signal}.`;
  }
  if (kind === "boundary") {
    return `${name}'s profile needs verification around ${signal}, especially before stating exact public facts.`;
  }
  return "";
}

function profileSentenceCandidates(values, limit = 10) {
  return uniqueSignals(values, limit * 2, 260)
    .map((item) => ensureSentence(item))
    .filter((item) => {
      const text = item.toLowerCase();
      if (item.length < 28) return false;
      if (PRODUCT_PROFILE_NOISE_PATTERNS.some((pattern) => pattern.test(item))) return false;
      if (/^["']?(if|when|how|why|what|watch|click)\b/i.test(item)) return false;
      if (/\b(link below|source below|attached)\b/i.test(item)) return false;
      if (text.split(" ").length < 7) return false;
      return true;
    })
    .slice(0, limit);
}

function collectModeSignals(modeMatrix = {}) {
  const allowedKeys = [
    "opening_move",
    "energy",
    "question_style",
    "proof_style",
    "structure",
    "validation_style",
    "pivot_style",
    "intensity",
    "boundary_style",
    "story_shape",
    "lesson_drop",
    "trust_mechanism",
    "cta_style",
    "friction_style",
    "evidence_posture",
    "admission_style",
  ];
  return Object.values(modeMatrix || {}).flatMap((mode) => {
    if (!mode || typeof mode !== "object") return [];
    return allowedKeys.flatMap((key) => mode[key] || []);
  });
}

function buildProductAnalysis(fingerprint, displayName) {
  if (!fingerprint) {
    return { summary: "", profileBullets: [], cards: [], evidence: [], languageProfile: {} };
  }

  const style = fingerprint.style || {};
  const identity = fingerprint.identity || {};
  const persona = style.creator_persona || {};
  const productProfile = style.product_profile || {};
  const valueModel = style.value_model || {};
  const beliefGraph = style.belief_graph || {};
  const worldview = style.worldview || {};
  const domainMap = style.domain_map || {};
  const searchProfile = style.search_profile || {};
  const reasoning = style.reasoning_profile || {};
  const speech = style.speech_mechanics || {};
  const cadence = style.cadence_rules || {};
  const contentTruth = style.content_truth || {};
  const knowledge = style.knowledge_boundaries || {};
  const identitySignature = style.identity_signature || {};
  const languageProfile = style.language_profile || persona.language_profile || {};

  const explicitSummary = compactSignal(productProfile.summary, 280)
    || compactSignal(persona.source_coverage_summary, 240);

  const summary = explicitSummary
    || compactSignal(productProfile.value_summary, 240)
    || "Analysis complete. The profile now has the core creator signals needed for chat.";

  const explicitProfileBullets = profileSentenceCandidates(productProfile.profile_bullets, 10);
  const synthesizedProfileBullets = profileSentenceCandidates([
    `${displayName}'s current profile is synthesized from approved creator content and verified public research.`,
    sentenceFromSignal(displayName, "facts", [
      identity.verified_facts,
      identity.businesses,
      identity.products,
      identitySignature.public_role,
      identitySignature.mission_frame || identity.mission,
      contentTruth.businesses,
      contentTruth.products,
      contentTruth.milestones,
      contentTruth.quantified_claims,
    ]),
    sentenceFromSignal(displayName, "values", [
      productProfile.value_summary,
      productProfile.values,
      valueModel.core_values,
      valueModel.tradeoff_preferences,
      style.value_hierarchy,
      worldview.values,
      worldview.moral_hierarchy,
    ]),
    sentenceFromSignal(displayName, "beliefs", [
      beliefGraph.core_beliefs,
      beliefGraph.non_negotiables,
      beliefGraph.beliefs_they_protect,
      worldview.core_beliefs,
    ]),
    sentenceFromSignal(displayName, "teaching", [
      style.teaching_style,
      style.rhetorical_moves,
      reasoning.default_problem_solving_pattern,
      valueModel.decision_heuristics,
    ]),
    sentenceFromSignal(displayName, "voice", [
      persona.voice_summary,
      persona.cadence,
      persona.advice_style,
      persona.emotional_baseline,
      style.traits,
      speech.humor_profile,
      cadence.story_vs_list,
    ]),
    sentenceFromSignal(displayName, "conversation", [
      persona.response_rules,
      style.signature_response_moves,
      style.signature_moves,
      collectModeSignals(style.mode_matrix),
    ]),
    sentenceFromSignal(displayName, "domain", [
      searchProfile.primary_category,
      searchProfile.creator_lane,
      domainMap.creator_lane,
      domainMap.strong_topics,
      style.recurring_themes,
      identity.themes,
    ]),
    sentenceFromSignal(displayName, "boundary", [
      knowledge.must_verify_topics,
      knowledge.private_or_unknown,
      style.unknown_topic_policy?.never_infer,
    ]),
  ], 10);
  const profileBullets = uniqueSignals([
    explicitProfileBullets,
    synthesizedProfileBullets,
  ], 9, 280)
    .map((item) => ensureSentence(item))
    .filter(Boolean)
    .slice(0, 9);

  return {
    summary,
    profileBullets,
    cards: [],
    evidence: [],
    languageProfile,
  };
}

function lowercaseFirst(value) {
  const text = cleanText(value);
  if (!text) return "";
  return text.charAt(0).toLowerCase() + text.slice(1);
}

function ensureSentence(value) {
  const text = cleanText(value);
  if (!text) return "";
  return /[.!?。！？]$/.test(text) ? text : `${text}.`;
}

function getLanguageLabel(languageProfile = {}) {
  const language = cleanText(languageProfile.primary_language);
  const code = cleanText(languageProfile.primary_language_code);
  if (!language || language.toLowerCase() === "english") return "";
  return code ? `${language} · ${code}` : language;
}

export function PersonaSetup({ creatorId, creatorName, onContinue, loading, onGoToApprove }) {
  const [hasContent, setHasContent] = useState(false);
  const [contentLoading, setContentLoading] = useState(true);
  const [fingerprint, setFingerprint] = useState(null);
  const [status, setStatus] = useState("idle"); // idle, processing, error
  const [creatorStatus, setCreatorStatus] = useState(null);
  const [creatorConfig, setCreatorConfig] = useState(null);
  const [fingerprintProgress, setFingerprintProgress] = useState(null);
  const [recentTitles, setRecentTitles] = useState([]);
  const [tickerIndex, setTickerIndex] = useState(0);

  const fetchStatus = useCallback(async () => {
    if (!creatorId) return;
    try {
      const [fp, cfg] = await Promise.all([
        getFingerprintStatus(creatorId),
        getCreatorConfig(creatorId).catch(() => null),
      ]);

      setStatus(fp.status);
      setFingerprintProgress(fp.progress || null);
      if (cfg) {
        setCreatorConfig(cfg);
      }
      if (fp.has_fingerprint) {
        setFingerprint({
          style: fp.style,
          identity: fp.identity
        });
      }

      if (cfg?.status) {
        setCreatorStatus(cfg.status);
      }
    } catch (err) {
      console.error("Failed to load fingerprint:", err);
    }
  }, [creatorId]);

  useEffect(() => {
    if (!creatorId) return undefined;

    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) setContentLoading(true);
    });

    // 1. Initial check for content
    getQueueItems(creatorId)
      .then((data) => {
        if (cancelled) return;
        const items = data.items || [];
        const hasIngested = items && items.length > 0 && items.some(i =>
          ['ingested', 'approved', 'completed', 'ready'].includes(i.status) ||
          (i.item_status && ['ingested', 'approved', 'completed', 'ready'].includes(i.item_status))
        );
        setHasContent(hasIngested);
        // Capture a few recent titles for the live ticker
        const titles = items
          .map((i) => i.title || i.metadata?.title || "")
          .filter((t) => typeof t === "string" && t.trim() && !/^youtube video\s/i.test(t))
          .slice(0, 6);
        setRecentTitles(titles);
      })
      .finally(() => {
        if (!cancelled) setContentLoading(false);
      });

    // 2. Initial check for creator status (for gating)
    getCreatorConfig(creatorId)
      .then((data) => {
        if (cancelled) return;
        setCreatorConfig(data);
        if (data.status) {
          setCreatorStatus(data.status);
        }
      })
      .catch(() => { });

    // 2. Poll for fingerprint
    queueMicrotask(() => {
      if (!cancelled) fetchStatus();
    });
    const interval = setInterval(fetchStatus, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [creatorId, fetchStatus]);

  const displayCreatorName = useMemo(() => {
    const name = cleanText(creatorName)
      || getCreatorNameFromConfig(creatorConfig)
      || cleanText(fingerprint?.identity?.name)
      || "Creator";
    return formatCreatorName(name);
  }, [creatorName, creatorConfig, fingerprint]);
  const analysisModel = useMemo(
    () => buildProductAnalysis(fingerprint, displayCreatorName),
    [displayCreatorName, fingerprint]
  );
  const languageLabel = useMemo(
    () => getLanguageLabel(analysisModel.languageProfile),
    [analysisModel.languageProfile]
  );
  const showAnalysisResult = status !== "processing" && Boolean(fingerprint);
  const showFinishButton = Boolean(creatorId) && status !== "processing" && (Boolean(fingerprint) || Boolean(creatorStatus?.ready_to_chat));
  const showApprovalNeeded = !showAnalysisResult && !contentLoading && !hasContent && status !== "processing";
  const progressPercent = clampPercent(fingerprintProgress?.percent);
  const progressStage = fingerprintProgress?.stage_label || formatFingerprintStage(fingerprintProgress?.stage || status);
  const progressMessage = fingerprintProgress?.message || (
    status === "processing"
      ? "Analyzing approved content into a creator profile."
      : "Profile ready."
  );
  const progressDescription = fingerprintProgress?.stage_description || "Persona analysis is still running.";
  const progressFunLine = fingerprintProgress?.fun_line || "Turning approved content into a creator profile.";
  const progressStageCounter = formatStageCounter(fingerprintProgress);
  const progressStages = Array.isArray(fingerprintProgress?.stages) ? fingerprintProgress.stages : [];
  const stageIndex = Number(fingerprintProgress?.stage_index) || 1;
  const stageTotal = Number(fingerprintProgress?.stage_total) || progressStages.length || 1;

  // Rotating ticker: cycles every 4s through the fun line, the stage description,
  // and recent item titles ("Studying: …"). Keeps the user engaged while the
  // analysis runs without resorting to shimmer/animation noise.
  const tickerLines = useMemo(() => {
    const lines = [];
    if (progressFunLine) lines.push(progressFunLine);
    if (progressDescription && progressDescription !== progressFunLine) {
      lines.push(progressDescription);
    }
    recentTitles.forEach((t) => lines.push(`Studying: ${t}`));
    return lines.length ? lines : ["Listening for signal in the approved content."];
  }, [progressFunLine, progressDescription, recentTitles]);

  useEffect(() => {
    if (status !== "processing") return undefined;
    if (tickerLines.length <= 1) return undefined;
    const id = setInterval(() => {
      setTickerIndex((i) => (i + 1) % tickerLines.length);
    }, 4000);
    return () => clearInterval(id);
  }, [status, tickerLines.length]);

  const tickerLine = tickerLines[tickerIndex % tickerLines.length] || "";
  const stageNumberLabel = `${String(stageIndex).padStart(2, "0")} / ${String(stageTotal).padStart(2, "0")}`;

  return (
    <div className="persona-setup-card">
      <div className="persona-header">
        <div className="persona-kicker">Persona Analysis</div>
        <h2>Creator Intelligence</h2>
        <p className="persona-subtitle">
          {status === "processing"
            ? "Extracting values, voice, topics, and boundaries from approved content."
            : "The useful creator signals that will steer chat behavior."}
        </p>
      </div>

      <div className="persona-form read-only">
        {status === "processing" && (
          <div className="fp-card" role="status" aria-live="polite">
            <div className="fp-card-grid">
              <div className="fp-dial" aria-hidden="true">
                <svg className="fp-dial-svg" viewBox="0 0 120 120">
                  <circle className="fp-dial-track" cx="60" cy="60" r="52" />
                  <circle
                    className="fp-dial-progress"
                    cx="60"
                    cy="60"
                    r="52"
                    style={{
                      strokeDasharray: 2 * Math.PI * 52,
                      strokeDashoffset: 2 * Math.PI * 52 * (1 - progressPercent / 100),
                    }}
                  />
                  <circle className="fp-dial-scan" cx="60" cy="60" r="52" />
                </svg>
                <div className="fp-dial-center">
                  <div className="fp-dial-percent">{progressPercent}</div>
                  <div className="fp-dial-percent-mark">%</div>
                </div>
              </div>

              <div className="fp-meta">
                <div className="fp-station">
                  <span className="fp-station-num">{stageNumberLabel}</span>
                  <span className="fp-station-dot" aria-hidden="true" />
                  <span className="fp-station-label">{progressStage}</span>
                </div>
                <h3 className="fp-message">{progressMessage}</h3>
                <div className="fp-ticker" aria-live="polite">
                  <span className="fp-ticker-cursor" aria-hidden="true">&gt;</span>
                  <span key={tickerLine} className="fp-ticker-line">{tickerLine}</span>
                </div>
              </div>
            </div>

            <ol className="fp-tape" aria-label="Persona analysis stages">
              {progressStages.map((stage) => (
                <li
                  key={stage.key}
                  className={`fp-tape-row ${stage.state || "upcoming"}`}
                >
                  <span className="fp-tape-mark" aria-hidden="true" />
                  <span className="fp-tape-index">{String(stage.index).padStart(2, "0")}</span>
                  <span className="fp-tape-label">{stage.label}</span>
                </li>
              ))}
            </ol>

            <div className="fp-foot">
              <span>{progressStageCounter}</span>
              <span className="fp-foot-divider" aria-hidden="true" />
              <span>Updates automatically</span>
            </div>
          </div>
        )}

        {showAnalysisResult && (
          <section className="creator-current-profile" aria-label="Current creator profile">
            <div className="creator-current-profile-head">
              <span className="identity-kicker">Current Profile</span>
              {languageLabel ? (
                <span className="persona-language-pill">{languageLabel}</span>
              ) : null}
            </div>
            {analysisModel.profileBullets.length > 0 ? (
              <ul className="current-profile-list">
                {analysisModel.profileBullets.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : (
              <p className="current-profile-fallback">{analysisModel.summary}</p>
            )}
          </section>
        )}

        {showApprovalNeeded && (
          <section className="persona-empty-action">
            <span className="identity-kicker">Waiting on approved content</span>
            <h3>Approve content first</h3>
            <p>
              Persona needs approved source material.
            </p>
            {onGoToApprove && (
              <button type="button" className="go-to-approve-button" onClick={onGoToApprove}>
                Approve Content
              </button>
            )}
          </section>
        )}

        <div className="persona-cta-container">
          {showFinishButton ? (
            <button
              onClick={async () => {
                try {
                  await onContinue({ creatorStatus, fingerprintStatus: status });
                } catch (err) {
                  if (err.status && err.status === 409 && err.response?.data?.status) {
                    setCreatorStatus(err.response.data.status);
                  }
                }
              }}
              className="finish-btn"
              disabled={loading}
            >
              Finish & Chat
            </button>
          ) : null}

          {status !== "processing" && !creatorStatus?.ready_to_chat && creatorStatus?.block_reason && (
            <div className="persona-block-hint">
              {creatorStatus.block_reason}
              {creatorStatus.needs_reapproval && onGoToApprove && (
                <button type="button" className="go-to-approve-link" onClick={onGoToApprove}>
                  Go to Approve
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
