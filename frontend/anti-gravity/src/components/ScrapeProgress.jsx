import { useEffect, useState, useRef } from "react";
import { getJobProgress, getScrapeItems } from "../api/client";
import "./ScrapeProgress.css";

const STAGE_CAPS = {
  initializing: 4.8,
  scraping: 79.5,
  transcripts: 89.5,
  finalizing: 95.0
};

export function ScrapeProgress({ scrapeId, onComplete, onProgress, onError }) {
  const [percent, setPercent] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [isFinishing, setIsFinishing] = useState(false);

  // Refs for animation state to avoid stale closures in timeouts
  const stateRef = useRef({
    backendPercent: 0,
    lastBackendUpdateAt: Date.now(),
    stage: "initializing",
    isComplete: false
  });

  // Stable refs for callbacks — prevents infinite effect loops when parent
  // passes inline arrow functions that change reference every render
  const onProgressRef = useRef(onProgress);
  const onCompleteRef = useRef(onComplete);
  const onErrorRef = useRef(onError);
  useEffect(() => { onProgressRef.current = onProgress; }, [onProgress]);
  useEffect(() => { onCompleteRef.current = onComplete; }, [onComplete]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);

  // Polling Effect — only depends on scrapeId (stable value)
  useEffect(() => {
    if (!scrapeId) return;

    // Initial jump to show responsiveness
    setPercent(2);
    let timeoutId;
    let pollCount = 0;

    const poll = async () => {
      if (stateRef.current.isComplete) return;

      try {
        const res = await getJobProgress(scrapeId);

        if (res.status === "completed") {
          stateRef.current.isComplete = true;
          setPercent(100);
          if (onProgressRef.current) onProgressRef.current(100);
          handleCompletion(res);
          return; // Stop polling
        }

        if (res.status === "failed" || res.status === "error") {
          stateRef.current.isComplete = true;
          setError(res.error_log || "Search failed");
          if (onErrorRef.current) onErrorRef.current(res.error_log || "Search failed");
          return;
        }

        const newBackendPercent = res.progress_percent || 0;
        // Parse message for stage hints if needed, or default
        const currentStage = res.message?.toLowerCase().includes("transcript") ? "transcripts" : "scraping";

        // Update Ref state
        stateRef.current.stage = currentStage;

        // If backend advanced, snap to it and reset stall timer
        if (newBackendPercent > stateRef.current.backendPercent) {
          stateRef.current.backendPercent = newBackendPercent;
          stateRef.current.lastBackendUpdateAt = Date.now();

          // Snap UI
          setPercent(newBackendPercent);
          if (onProgressRef.current) onProgressRef.current(newBackendPercent);
        }

        setData(res);
      } catch (err) {
        console.error("Poll error", err);
      }

      if (!stateRef.current.isComplete) {
        pollCount++;
        let delay = 1000;
        if (pollCount > 10) delay = 5000;
        else if (pollCount > 6) delay = 3000;
        else if (pollCount > 4) delay = 2000;
        else if (pollCount > 2) delay = 1500;

        timeoutId = setTimeout(poll, delay);
      }
    };

    poll(); // Immediate first check

    return () => clearTimeout(timeoutId);
  }, [scrapeId]);

  // Creep Animation Loop — no callback dependencies, uses refs
  useEffect(() => {
    let timeoutId;

    const loop = () => {
      if (stateRef.current.isComplete) return;

      const now = Date.now();
      const { backendPercent, lastBackendUpdateAt, stage } = stateRef.current;

      // Calculate headroom based on stall time
      const stallTime = now - lastBackendUpdateAt;
      let allowedHeadroom = 0.2;
      if (stallTime > 15000) allowedHeadroom = 1.8;
      else if (stallTime > 6000) allowedHeadroom = 1.2;
      else if (stallTime > 2000) allowedHeadroom = 0.6;

      // Determine cap
      const stageCap = STAGE_CAPS[stage] || 95.0;
      // Never creep beyond stage cap OR backend + headroom
      const creepLimit = Math.min(stageCap - 0.6, backendPercent + allowedHeadroom);

      setPercent(prev => {
        if (prev >= creepLimit) return prev; // Cap reached, hold steady
        if (prev >= 100) return 100;

        // Random step size
        const isFinalizing = stage === "finalizing";
        const minStep = isFinalizing ? 0.02 : 0.04;
        const maxStep = isFinalizing ? 0.07 : 0.12;
        const step = minStep + Math.random() * (maxStep - minStep);

        const next = Math.min(prev + step, creepLimit);

        // Update parent via ref
        if (onProgressRef.current) onProgressRef.current(next);

        return next;
      });

      // Schedule next pulse (jittered)
      const delay = 900 + Math.random() * 500; // 900-1400ms
      timeoutId = setTimeout(loop, delay);
    };

    loop();
    return () => clearTimeout(timeoutId);
  }, []);

  const handleCompletion = async (data) => {
    if (isFinishing) return;
    setIsFinishing(true);
    try {
      const itemsResult = await getScrapeItems(scrapeId);
      if (onComplete) {
        // Wait slightly for 100% animation to finish visually
        setTimeout(() => {
          onComplete({ ...data, items: itemsResult.items, platform_statuses: itemsResult.platform_statuses });
        }, 800);
      }
    } catch (err) {
      setError("Search completed, but failed to load results: " + err.message);
      if (onError) onError(err.message);
    }
  };

  if (error) {
    const errorStr = String(error).toLowerCase();
    const isDbError = errorStr.includes("violates") || errorStr.includes("sql") || errorStr.includes("exception") || errorStr.includes("constraint");
    const displayError = isDbError ? "Search couldn't save some items. Please try again." : error;

    return (
      <div className="scrape-progress-container error">
        <div className="error-icon">⚠️</div>
        <h3>Search Failed</h3>
        <p>{displayError}</p>
        <button onClick={() => window.location.reload()} className="secondary-button">Try Again</button>
      </div>
    );
  }

  // Determine message
  let displayMsg = "Preparing search...";
  const stage = data?.stage || stateRef.current.stage;

  if (isFinishing || stage === "finalizing") displayMsg = "Finalizing...";
  else if (stage === "scraping") {
    if (data?.message) displayMsg = data.message;
    else displayMsg = "Collecting content...";
  }
  else if (stage === "transcripts") displayMsg = "Understanding context...";

  return (
    <div className="scrape-progress-container">
      <div className="progress-content">
        <h2>{isFinishing ? "Finalizing..." : "Searching..."}</h2>
        <p className="status-message">{displayMsg}</p>

        <div className="progress-bar-container large">
          <div
            className="progress-bar-fill"
            style={{
              width: `${Math.min(100, percent)}%`,
              transition: 'width 420ms cubic-bezier(0.22, 1, 0.36, 1)'
            }}
          ></div>
        </div>

        <div className="progress-stats">
          <span className="percentage">{Math.round(percent)}%</span>
        </div>
      </div>
    </div>
  );
}
