import { useState, useEffect } from "react";
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

export function PersonaSetup({ creatorId, onContinue, loading, onGoToApprove }) {
  const [hasContent, setHasContent] = useState(false);
  const [contentLoading, setContentLoading] = useState(true);
  const [fingerprint, setFingerprint] = useState(null);
  const [status, setStatus] = useState("idle"); // idle, processing, error
  const [creatorStatus, setCreatorStatus] = useState(null);
  const [fingerprintProgress, setFingerprintProgress] = useState(null);

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

  const getStatements = () => {
    if (!fingerprint) return [];

    const style = fingerprint.style || {};
    const identity = fingerprint.identity || {};
    const statements = [];
    const pushUnique = (value) => {
      if (!value || typeof value !== "string") return;
      const clean = value.trim();
      if (!clean) return;
      if (!statements.some((s) => s.toLowerCase() === clean.toLowerCase())) {
        statements.push(clean);
      }
    };

    const bio = identity.bio;
    const mission = identity.mission;
    if (bio && bio !== "Profile in progress.") pushUnique(bio);
    if (mission) pushUnique(mission);

    (style.summary || []).forEach(pushUnique);
    (style.traits || []).forEach(pushUnique);
    (style.recurring_themes || []).slice(0, 3).forEach((theme) => pushUnique(`Recurring theme: ${theme}`));
    (style.teaching_style || []).slice(0, 2).forEach((item) => pushUnique(`Teaching style: ${item}`));
    (style.signature_phrases || []).slice(0, 2).forEach((item) => pushUnique(`Signature phrase: ${item}`));
    (identity.verified_facts || []).slice(0, 3).forEach((fact) => pushUnique(`Verified fact: ${fact}`));
    (identity.businesses || []).slice(0, 2).forEach((item) => pushUnique(`Business history: ${item}`));
    (identity.products || []).slice(0, 2).forEach((item) => pushUnique(`Product or offering: ${item}`));
    (style.content_truth?.quantified_claims || []).slice(0, 2).forEach((item) => pushUnique(`Quantified claim: ${item}`));
    (style.evidence_snippets || []).slice(0, 2).forEach((item) => pushUnique(`Observed pattern: ${item}`));

    return statements.slice(0, 8);
  };

  const stats = getStatements();
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
          <div className="fingerprint-progress-card">
            <div className="fingerprint-progress-header">
              <div>
                <p className="fingerprint-progress-label">{progressStageCounter}</p>
                <h3 className="fingerprint-progress-title">{progressStage}</h3>
                <p className="loading-text">{progressMessage}</p>
              </div>
              <div className="fingerprint-progress-percent">{progressPercent}%</div>
            </div>
            <div className="progress-bar-container">
              <div className="progress-bar-fill" style={{ width: `${progressPercent}%` }}></div>
            </div>
            <div className="fingerprint-progress-stage-rail">
              {progressStages.map((stage) => (
                <div
                  key={stage.key}
                  className={`fingerprint-stage-chip ${stage.state || "upcoming"}`}
                  title={stage.description}
                >
                  <span className="fingerprint-stage-chip-index">{stage.index}</span>
                  <span className="fingerprint-stage-chip-label">{stage.label}</span>
                </div>
              ))}
            </div>
            <div className="fingerprint-progress-callouts">
              <div className="fingerprint-callout">
                <span className="fingerprint-callout-label">What&apos;s happening</span>
                <p>{progressDescription}</p>
              </div>
              <div className="fingerprint-callout playful">
                <span className="fingerprint-callout-label">Backstage</span>
                <p>{progressFunLine}</p>
              </div>
            </div>
            <div className="fingerprint-progress-meta">
              <span>{progressStageCounter}</span>
              <span>Auto-refreshing every 3 seconds</span>
            </div>
          </div>
        )}

        {stats.length > 0 ? (
          <div className="fingerprint-glass-card">
            <div className="fingerprint-section-header">
              <span>Current profile</span>
            </div>
            {stats.map((text, i) => (
              <div key={i} className="fingerprint-statement">
                <div className="statement-bullet"></div>
                <p className="statement-text">{text}</p>
              </div>
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
