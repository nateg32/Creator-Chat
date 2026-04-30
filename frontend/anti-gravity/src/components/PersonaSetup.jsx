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
