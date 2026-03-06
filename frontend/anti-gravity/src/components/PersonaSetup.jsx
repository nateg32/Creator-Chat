import { useState, useEffect } from "react";
import { getFingerprintStatus, getQueueItems, getCreatorConfig } from "../api/client";
import "./PersonaSetup.css";

export function PersonaSetup({ creatorId, onContinue, loading, onGoToApprove }) {
  const [hasContent, setHasContent] = useState(false);
  const [contentLoading, setContentLoading] = useState(true);
  const [fingerprint, setFingerprint] = useState(null);
  const [status, setStatus] = useState("idle"); // idle, processing, error
  const [creatorStatus, setCreatorStatus] = useState(null);
  const [pollInterval, setPollInterval] = useState(null);

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
      setPollInterval(interval);
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
    const traits = fingerprint.style?.traits || [];
    const bio = fingerprint.identity?.bio;
    const mission = fingerprint.identity?.mission;

    let statements = [...traits];
    if (bio && bio !== "Profile in progress.") {
      // Limit bio length for the card
      const cleanBio = bio.length > 120 ? bio.slice(0, 120) + "..." : bio;
      statements.unshift(cleanBio);
    }

    return statements.slice(0, 5); // Max 5 for visual clarity
  };

  const stats = getStatements();

  return (
    <div className="persona-setup-card">
      <div className="persona-header">
        <h2>Style Fingerprint</h2>
        <p className="persona-subtitle">
          {status === "processing"
            ? "Analyzing content to build unique identity..."
            : "Data-driven identity generated from public records and content."}
        </p>
      </div>

      <div className="persona-form read-only">
        {status === "processing" && (
          <div className="fingerprint-loading">
            <div className="progress-bar-container">
              <div className="progress-bar-fill animate"></div>
            </div>
            <p className="loading-text">Extracting voice patterns & verified facts...</p>
          </div>
        )}

        {stats.length > 0 ? (
          <div className="fingerprint-glass-card">
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
          <div className="identity-badge"> Verified Identity Layer Active </div>
        )}

        <div className="disclaimer-text">
          This profile is generated based on publicly available information and ingested content.
        </div>

        <div className="persona-cta-container">
          <button
            onClick={async (e) => {
              if (creatorStatus?.ready_to_chat) {
                try {
                  await onContinue(e);
                } catch (err) {
                  if (err.status && err.status === 409 && err.response?.data?.status) {
                    setCreatorStatus(err.response.data.status);
                  }
                }
              }
            }}
            className="finish-btn"
            disabled={loading || (status === "processing" && stats.length === 0) || !creatorStatus?.ready_to_chat}
            aria-disabled={!creatorStatus?.ready_to_chat ? "true" : undefined}
          >
            {status === "processing" && stats.length === 0 ? "Building Profile..." : "Finish & Chat"}
          </button>

          {!creatorStatus?.ready_to_chat && creatorStatus?.block_reason && (
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
