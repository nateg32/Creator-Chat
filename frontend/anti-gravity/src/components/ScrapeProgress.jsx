import { useEffect, useState } from "react";
import { getScrapeProgress } from "../api/client";
import "./ScrapeProgress.css";

export function ScrapeProgress({ scrapeId, onComplete, onError }) {
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!scrapeId) return;

    let consecutive404s = 0;
    const max404Retries = 6; // Retry 404 for ~3 seconds before giving up

    const pollInterval = setInterval(async () => {
      try {
        const data = await getScrapeProgress(scrapeId);
        consecutive404s = 0; // Reset on success
        setProgress(data);
        setError(null);

        if (data.status === "completed" || data.status === "error") {
          clearInterval(pollInterval);
          if (data.status === "completed" && onComplete) {
            onComplete(data);
          } else if (data.status === "error" && onError) {
            const errMsg = data.error || (data.platform_summary && Object.values(data.platform_summary).find(s => s?.error)?.error) || `Searching failed (Status: ${data.status}, Err: ${data.error})`;
            onError(errMsg);
          }
        }
      } catch (err) {
        const is404 = err.message && (err.message.includes("404") || err.message.includes("Search run not found"));
        if (is404 && consecutive404s < max404Retries) {
          consecutive404s += 1;
          // Don't report yet - retry (progress may not be ready)
        } else {
          setError(err.message);
          clearInterval(pollInterval);
          if (onError) {
            onError(err.message);
          }
        }
      }
    }, 500); // Poll every 500ms

    return () => clearInterval(pollInterval);
  }, [scrapeId, onComplete, onError]);

  if (!progress) {
    return (
      <div className="scrape-progress">
        <div className="progress-spinner"></div>
        <p>Initializing search...</p>
      </div>
    );
  }

  const percentage = progress.percentage || 0;
  const currentPlatform = progress.current_platform_label || progress.current_platform || "Unknown";

  return (
    <div className="scrape-progress">
      <div className="progress-header">
        <h3>Search in progress</h3>
        <div className="progress-percentage">{percentage}%</div>
      </div>

      <div className="progress-bar-container">
        <div
          className="progress-bar"
          style={{ width: `${percentage}%` }}
        ></div>
      </div>

      <div className="progress-details">
        <div className="progress-item">
          <span className="label">Current platform:</span>
          <span className="value">{currentPlatform}</span>
        </div>
        <div className="progress-item">
          <span className="label">Progress:</span>
          <span className="value">
            {progress.completed} of {progress.total} platforms
          </span>
        </div>
        {progress.items_found > 0 && (
          <div className="progress-item">
            <span className="label">Items found:</span>
            <span className="value">{progress.items_found}</span>
          </div>
        )}
        {progress.platform_summary && Object.keys(progress.platform_summary).length > 0 && (
          <div className="progress-item" style={{ marginTop: "12px", paddingTop: "12px", borderTop: "1px solid #e5e7eb" }}>
            <span className="label" style={{ marginBottom: "8px", display: "block" }}>Platform status:</span>
            <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              {Object.entries(progress.platform_summary).map(([key, info]) => {
                const statusIcon = info.status === "success" ? "✓" : info.status === "error" ? "⚠" : "·";
                const statusColor = info.status === "success" ? "#10b981" : info.status === "error" ? "#ef4444" : "#6b7280";
                return (
                  <div key={key} style={{ fontSize: "12px", display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: statusColor }}>
                      {statusIcon} {info.label}
                    </span>
                    <span style={{ color: "#6b7280" }}>
                      {info.items_found} items
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {progress.status === "error" && progress.error && (
        <div className="progress-error">
          <strong>Error:</strong> {progress.error}
        </div>
      )}

      {error && (
        <div className="progress-error">
          <strong>Error:</strong> {error}
        </div>
      )}
    </div>
  );
}
