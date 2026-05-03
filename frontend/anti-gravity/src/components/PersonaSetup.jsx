import { useState, useEffect, useMemo } from "react";
import { getFingerprintStatus, getQueueItems, getCreatorConfig } from "../api/client";
import "./PersonaSetup.css";

function clampPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(0, Math.min(100, Math.round(numeric)));
}

function formatFingerprintStage(stage) {
  const labels = {
    preparing: "Preparing",
    research_cache: "Loading cached research",
    link_scan: "Scanning source links",
    voice_analysis: "Analyzing voice patterns",
    dossier: "Expanding public profile",
    synthesis: "Synthesizing fingerprint",
    finalizing: "Finalizing profile",
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
]);

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
  return clean;
}

function labelize(key = "") {
  const clean = String(key || "")
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .trim();
  if (!clean) return "Detail";
  return clean.charAt(0).toUpperCase() + clean.slice(1);
}

function uniqueStrings(values, limit = 8) {
  const seen = new Set();
  const out = [];
  values.forEach((value) => {
    const clean = cleanText(value);
    if (!clean) return;
    const key = clean.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    out.push(clean);
  });
  return out.slice(0, limit);
}

function objectToLines(value, limit = 8) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return uniqueStrings(
    Object.entries(value).flatMap(([key, raw]) => {
      if (Array.isArray(raw)) {
        const parts = uniqueStrings(raw, 4);
        return parts.length ? `${labelize(key)}: ${parts.join(", ")}` : [];
      }
      if (raw && typeof raw === "object") {
        const nested = objectToLines(raw, 3);
        return nested.length ? `${labelize(key)}: ${nested.join("; ")}` : [];
      }
      const clean = cleanText(raw);
      return clean ? `${labelize(key)}: ${clean}` : [];
    }),
    limit
  );
}

function collectLines(value, limit = 8) {
  if (Array.isArray(value)) {
    return uniqueStrings(
      value.flatMap((item) => {
        if (item && typeof item === "object") return objectToLines(item, 3);
        return item;
      }),
      limit
    );
  }
  if (value && typeof value === "object") return objectToLines(value, limit);
  return uniqueStrings([value], limit);
}

function addDetail(items, label, value, limit = 6) {
  const values = collectLines(value, limit);
  if (values.length) items.push({ label, values });
}

function buildProfileHighlights(fingerprint) {
  if (!fingerprint) return [];
  const style = fingerprint.style || {};
  const identity = fingerprint.identity || {};
  const identitySignature = style.identity_signature || {};
  const highlights = [];
  const add = (value) => {
    const clean = cleanText(value);
    if (!clean) return;
    if (!highlights.some((item) => item.toLowerCase() === clean.toLowerCase())) {
      highlights.push(clean);
    }
  };

  add(identity.bio);
  add(identity.mission);
  add(identitySignature.self_concept);
  add(identitySignature.mission_frame);
  (style.summary || []).forEach(add);
  (style.traits || []).slice(0, 3).forEach(add);
  (style.evidence_snippets || []).slice(0, 2).forEach((item) => add(`Observed pattern: ${item}`));
  return highlights.slice(0, 6);
}

function buildFingerprintSections(fingerprint) {
  if (!fingerprint) return [];
  const style = fingerprint.style || {};
  const identity = fingerprint.identity || {};
  const linguistic = style.linguistic_dna || {};
  const speech = style.speech_mechanics || {};
  const cadence = style.cadence_rules || {};
  const voicePatterns = style.voice_patterns || {};
  const sentenceStructure = voicePatterns.sentence_structure || {};
  const rhythm = voicePatterns.rhythm || {};
  const voiceMoves = voicePatterns.rhetorical_moves || {};
  const interaction = voicePatterns.interaction_style || {};
  const lexical = style.lexical_rules || {};
  const worldview = style.worldview || {};
  const beliefs = style.belief_graph || {};
  const values = style.value_model || {};
  const reasoning = style.reasoning_profile || {};
  const modeMatrix = style.mode_matrix || {};
  const antiPersona = style.anti_persona || {};
  const contrastive = style.contrastive_identity || {};
  const disambiguation = style.disambiguation_markers || {};
  const contentTruth = style.content_truth || {};
  const scoring = style.scoring || {};

  const sections = [];
  const pushSection = (section) => {
    if (section.items.length) sections.push(section);
  };

  const identityItems = [];
  addDetail(identityItems, "Self-concept", style.identity_signature?.self_concept);
  addDetail(identityItems, "Mission frame", style.identity_signature?.mission_frame || identity.mission);
  addDetail(identityItems, "Audience model", style.identity_signature?.audience_model);
  addDetail(identityItems, "Power position", style.identity_signature?.power_position);
  addDetail(identityItems, "Public role", style.identity_signature?.public_role);
  addDetail(identityItems, "Private boundary style", style.identity_signature?.private_boundary_style);
  addDetail(identityItems, "Verified facts", identity.verified_facts, 4);
  pushSection({
    eyebrow: "Identity",
    title: "Who The Bot Thinks This Creator Is",
    subtitle: "This is the role, audience relationship, and public boundary model the runtime will lean on.",
    items: identityItems,
  });

  const voiceItems = [];
  addDetail(voiceItems, "Trait map", style.traits, 8);
  addDetail(voiceItems, "Sentence structure", sentenceStructure.pattern_description || linguistic.sentence_structure || speech.sentence_shape || cadence.sentence_shape);
  addDetail(voiceItems, "Energy", linguistic.energy);
  addDetail(voiceItems, "Pacing", rhythm.pacing);
  addDetail(voiceItems, "Question frequency", sentenceStructure.question_frequency || speech.question_density || cadence.question_rate);
  addDetail(voiceItems, "Imperative density", speech.imperative_density || cadence.imperative_rate);
  addDetail(voiceItems, "Pause and punctuation", [...collectLines(rhythm.pause_markers, 4), ...collectLines(speech.punctuation_rules, 4), ...collectLines(cadence.pause_markers, 4)], 8);
  addDetail(voiceItems, "Audience address", interaction.audience_address);
  addDetail(voiceItems, "Disagreement style", interaction.disagreement_style);
  addDetail(voiceItems, "Uncertainty style", interaction.uncertainty_style);
  pushSection({
    eyebrow: "Voice",
    title: "Voice, Cadence, And Delivery",
    subtitle: "Checks whether the model captured how the creator actually sounds, not just what they talk about.",
    items: voiceItems,
  });

  const valueItems = [];
  addDetail(valueItems, "Core beliefs", beliefs.core_beliefs || worldview.core_beliefs, 8);
  addDetail(valueItems, "Core values", values.core_values || worldview.values, 8);
  addDetail(valueItems, "Value hierarchy", beliefs.value_hierarchy || style.value_hierarchy, 8);
  addDetail(valueItems, "Non-negotiables", beliefs.non_negotiables, 6);
  addDetail(valueItems, "Ideas they reject", values.rejections || beliefs.beliefs_they_attack || worldview.conceptual_enemies, 8);
  addDetail(valueItems, "Ideas they protect", beliefs.beliefs_they_protect, 6);
  addDetail(valueItems, "Decision rules", values.decision_heuristics, 8);
  addDetail(valueItems, "Tradeoffs", values.tradeoff_preferences, 6);
  pushSection({
    eyebrow: "Values",
    title: "Values, Beliefs, And Decision Logic",
    subtitle: "The underlying worldview that should shape answers even when the exact topic changes.",
    items: valueItems,
  });

  const teachingItems = [];
  addDetail(teachingItems, "Teaching style", style.teaching_style, 8);
  addDetail(teachingItems, "Rhetorical moves", style.rhetorical_moves || style.signature_response_moves, 8);
  addDetail(teachingItems, "Signature moves", style.signature_moves, 8);
  addDetail(teachingItems, "Problem-solving pattern", reasoning.default_problem_solving_pattern, 6);
  addDetail(teachingItems, "Proof style", reasoning.proof_style || linguistic.evidence_style || modeMatrix.teaching?.proof_style);
  addDetail(teachingItems, "Framework vs story", reasoning.framework_vs_story || cadence.story_vs_list || voiceMoves.story_vs_list);
  addDetail(teachingItems, "Premise challenge rate", reasoning.premise_challenge_rate);
  addDetail(teachingItems, "Action bias", reasoning.action_bias);
  addDetail(teachingItems, "Mode-specific teaching", modeMatrix.teaching, 6);
  addDetail(teachingItems, "Comfort mode", modeMatrix.comfort, 5);
  pushSection({
    eyebrow: "Method",
    title: "Teaching And Reasoning Pattern",
    subtitle: "How replies should be structured when the user asks for help, advice, challenge, or comfort.",
    items: teachingItems,
  });

  const languageItems = [];
  addDetail(languageItems, "Signature phrases", [...collectLines(style.signature_phrases, 8), ...collectLines(lexical.signature_phrases, 8)], 12);
  addDetail(languageItems, "High-signal words", lexical.high_signal_words || style.lexicon, 14);
  addDetail(languageItems, "Openings", speech.signature_openings || voiceMoves.signature_openings, 8);
  addDetail(languageItems, "Landings", speech.signature_landings || voiceMoves.signature_landings, 8);
  addDetail(languageItems, "Analogy families", speech.analogy_domains || style.analogy_families, 8);
  addDetail(languageItems, "Golden teaching examples", style.golden_examples?.teaching || style.golden_replies?.teaching, 4);
  addDetail(languageItems, "Golden comfort examples", style.golden_examples?.comfort || style.golden_replies?.comfort, 3);
  addDetail(languageItems, "Golden boundary examples", style.golden_examples?.boundary || style.golden_replies?.boundary, 3);
  pushSection({
    eyebrow: "Language",
    title: "Exact Language And Reusable Lines",
    subtitle: "Literal phrases, openings, closings, and example replies that keep the bot from drifting generic.",
    items: languageItems,
  });

  const guardrailItems = [];
  addDetail(guardrailItems, "Never sound like", antiPersona.sounds_like_someone_else_if || contrastive.anti_persona, 8);
  addDetail(guardrailItems, "Forbidden generic lines", antiPersona.forbidden_generic_coach_lines || voicePatterns.greeting_signals?.forbidden_generic_frames, 8);
  addDetail(guardrailItems, "Banned frames", lexical.banned_frames, 8);
  addDetail(guardrailItems, "Forbidden emotional postures", antiPersona.forbidden_emotional_postures, 8);
  addDetail(guardrailItems, "Must show", disambiguation.must_show || contrastive.must_show, 8);
  addDetail(guardrailItems, "Must avoid", disambiguation.must_avoid || contrastive.must_avoid, 8);
  addDetail(guardrailItems, "Confusion risks", contrastive.confusion_risks || antiPersona.confusable_with, 6);
  pushSection({
    eyebrow: "Guardrails",
    title: "Anti-Generic And Anti-Impersonation Rules",
    subtitle: "What the runtime should avoid so the creator does not collapse into a safe, generic AI voice.",
    items: guardrailItems,
  });

  const evidenceItems = [];
  addDetail(evidenceItems, "Evidence snippets", style.evidence_snippets, 8);
  addDetail(evidenceItems, "Content milestones", contentTruth.milestones, 5);
  addDetail(evidenceItems, "Products and businesses", [...collectLines(contentTruth.products, 5), ...collectLines(contentTruth.businesses, 5), ...collectLines(identity.products, 5), ...collectLines(identity.businesses, 5)], 10);
  addDetail(evidenceItems, "Quantified claims", contentTruth.quantified_claims, 6);
  addDetail(evidenceItems, "Strong topics", style.domain_map?.strong_topics, 8);
  addDetail(evidenceItems, "Weak or unsafe topics", [...collectLines(style.domain_map?.weak_topics, 5), ...collectLines(style.domain_map?.unsafe_topics, 5)], 8);
  addDetail(evidenceItems, "Confidence scoring", scoring, 6);
  pushSection({
    eyebrow: "Evidence",
    title: "Grounding And Confidence",
    subtitle: "The observed content signals and confidence markers behind the fingerprint.",
    items: evidenceItems,
  });

  return sections;
}

export function PersonaSetup({ creatorId, onContinue, loading, onGoToApprove }) {
  const [hasContent, setHasContent] = useState(false);
  const [contentLoading, setContentLoading] = useState(true);
  const [fingerprint, setFingerprint] = useState(null);
  const [status, setStatus] = useState("idle"); // idle, processing, error
  const [creatorStatus, setCreatorStatus] = useState(null);
  const [fingerprintProgress, setFingerprintProgress] = useState(null);
  const [recentTitles, setRecentTitles] = useState([]);
  const [tickerIndex, setTickerIndex] = useState(0);

  useEffect(() => {
    if (creatorId) {
      setContentLoading(true);

      // 1. Initial check for content
      getQueueItems(creatorId)
        .then((data) => {
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
        .finally(() => setContentLoading(false));

      // 2. Initial check for creator status (for gating)
      getCreatorConfig(creatorId)
        .then((data) => {
          if (data.status) {
            setCreatorStatus(data.status);
          }
        })
        .catch(() => { });

      // 2. Poll for fingerprint
      fetchStatus();
      const interval = setInterval(fetchStatus, 3000);
      return () => clearInterval(interval);
    }
  }, [creatorId]);

  const fetchStatus = async () => {
    if (!creatorId) return;
    try {
      const [fp, cfg] = await Promise.all([
        getFingerprintStatus(creatorId),
        getCreatorConfig(creatorId).catch(() => null),
      ]);

      setStatus(fp.status);
      setFingerprintProgress(fp.progress || null);
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
  };

  const profileHighlights = useMemo(() => buildProfileHighlights(fingerprint), [fingerprint]);
  const fingerprintSections = useMemo(() => buildFingerprintSections(fingerprint), [fingerprint]);
  const showFinishButton = Boolean(creatorId) && status !== "processing" && (Boolean(fingerprint) || !hasContent);
  const progressPercent = clampPercent(fingerprintProgress?.percent);
  const progressStage = fingerprintProgress?.stage_label || formatFingerprintStage(fingerprintProgress?.stage || status);
  const progressMessage = fingerprintProgress?.message || (
    status === "processing"
      ? "Analyzing approved content and public-source identity signals."
      : "Fingerprint ready."
  );
  const progressDescription = fingerprintProgress?.stage_description || "Fingerprint generation is moving through the pipeline.";
  const progressFunLine = fingerprintProgress?.fun_line || "The engine is turning approved content into a creator operating system.";
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
    return lines.length ? lines : ["Listening for signal in the approved corpus."];
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
        <div className="persona-kicker">Fingerprint</div>
        <h2>Style Fingerprint</h2>
        <p className="persona-subtitle">
          {status === "processing"
            ? "Building a voice and identity model from approved content."
            : "A distilled profile generated from approved content and public records."}
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
                  <span className="fp-ticker-cursor" aria-hidden="true">▸</span>
                  <span key={tickerLine} className="fp-ticker-line">{tickerLine}</span>
                </div>
              </div>
            </div>

            <ol className="fp-tape" aria-label="Fingerprint stages">
              {progressStages.map((stage) => (
                <li
                  key={stage.key}
                  className={`fp-tape-row ${stage.state || "upcoming"}`}
                  title={stage.description}
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
              <span>Refreshing every 3s</span>
            </div>
          </div>
        )}

        {fingerprintSections.length > 0 ? (
          <div className="fingerprint-analysis-stack">
            {profileHighlights.length > 0 && (
              <section className="fingerprint-analysis-section fingerprint-overview-section">
                <div className="fingerprint-section-heading">
                  <span>Current profile</span>
                  <h3>Runtime Voice Model</h3>
                  <p>
                    A readable view of the fingerprint that will steer voice, values, language, and boundaries.
                  </p>
                </div>
                <div className="fingerprint-highlight-list">
                  {profileHighlights.map((text, i) => (
                    <div key={i} className="fingerprint-statement">
                      <div className="statement-bullet"></div>
                      <p className="statement-text">{text}</p>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {fingerprintSections.map((section) => (
              <section key={section.title} className="fingerprint-analysis-section">
                <div className="fingerprint-section-heading">
                  <span>{section.eyebrow}</span>
                  <h3>{section.title}</h3>
                  {section.subtitle && <p>{section.subtitle}</p>}
                </div>
                <div className="fingerprint-detail-grid">
                  {section.items.map((item) => (
                    <div key={`${section.title}-${item.label}`} className="fingerprint-detail-item">
                      <div className="fingerprint-detail-label">{item.label}</div>
                      {item.values.length === 1 ? (
                        <p className="fingerprint-detail-text">{item.values[0]}</p>
                      ) : (
                        <ul className="fingerprint-detail-list">
                          {item.values.map((value) => (
                            <li key={value}>{value}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>
        ) : !contentLoading && status !== "processing" && (
          <p className="muted-notice">No content analyzed yet. Approve some content to build the fingerprint.</p>
        )}

        {fingerprint?.identity?.is_verified && (
          <div className="identity-badge">Verified identity layer active</div>
        )}

        <div className="disclaimer-text">
          This profile is generated based on publicly available information and ingested content.
        </div>

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
