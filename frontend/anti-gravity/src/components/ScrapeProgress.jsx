import { useEffect, useState, useRef } from "react";
import { getSearchProgress, getScrapeItems } from "../api/client";
import "./ScrapeProgress.css";

const STAGE_CAPS = {
  initializing: 4.8,
  search: 79.5,
  scraping: 79.5,
  transcripts: 89.5,
  finalizing: 95.0,
  done: 100.0,
};

export function ScrapeProgress({ scrapeId, onComplete, onProgress, onError }) {
  const [percent, setPercent] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [isFinishing, setIsFinishing] = useState(false);

  const stateRef = useRef({
    backendPercent: 0,
    lastBackendUpdateAt: Date.now(),
    stage: "initializing",
    isComplete: false,
  });

  const onProgressRef = useRef(onProgress);
  const onCompleteRef = useRef(onComplete);
  const onErrorRef = useRef(onError);
  useEffect(() => { onProgressRef.current = onProgress; }, [onProgress]);
  useEffect(() => { onCompleteRef.current = onComplete; }, [onComplete]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);

  useEffect(() => {
    if (!scrapeId) return;

    setPercent(2);
    let timeoutId;
    let pollCount = 0;

    const poll = async () => {
      if (stateRef.current.isComplete) return;

      try {
        const res = await getSearchProgress(scrapeId);
        setData(res);

        const backendPercent = Number(res.percent ?? res.percentage ?? 0);
        const rawStage = String(res.stage || res.phase || "search").toLowerCase();
        const phase = (rawStage === "scrape" || rawStage === "scraping") ? "search" : rawStage;
        const status = String(res.status || "running").toLowerCase();

        const isDone = phase === "done" || backendPercent >= 100 || status === "completed";
        const isFailed = status === "failed" || status === "error" || !!res.error;

        if (isFailed) {
          stateRef.current.isComplete = true;
          const msg = res.error || res.message || "Search failed";
          setError(msg);
          if (onErrorRef.current) onErrorRef.current(msg);
          return;
        }

        stateRef.current.stage = phase;

        if (backendPercent > stateRef.current.backendPercent) {
          stateRef.current.backendPercent = backendPercent;
          stateRef.current.lastBackendUpdateAt = Date.now();
          setPercent(backendPercent);
          if (onProgressRef.current) onProgressRef.current(backendPercent);
        }

        if (isDone) {
          stateRef.current.isComplete = true;
          setPercent(100);
          if (onProgressRef.current) onProgressRef.current(100);
          await handleCompletion(res);
          return;
        }
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

    poll();
    return () => clearTimeout(timeoutId);
  }, [scrapeId]);

  useEffect(() => {
    let timeoutId;

    const loop = () => {
      if (stateRef.current.isComplete) return;

      const now = Date.now();
      const { backendPercent, lastBackendUpdateAt, stage } = stateRef.current;

      const stallTime = now - lastBackendUpdateAt;
      let allowedHeadroom = 0.2;
      if (stallTime > 15000) allowedHeadroom = 1.8;
      else if (stallTime > 6000) allowedHeadroom = 1.2;
      else if (stallTime > 2000) allowedHeadroom = 0.6;

      const stageCap = STAGE_CAPS[stage] || 95.0;
      const creepLimit = Math.min(stageCap - 0.6, backendPercent + allowedHeadroom);

      setPercent((prev) => {
        if (prev >= creepLimit) return prev;
        if (prev >= 100) return 100;

        const isFinalizing = stage === "finalizing";
        const minStep = isFinalizing ? 0.02 : 0.04;
        const maxStep = isFinalizing ? 0.07 : 0.12;
        const step = minStep + Math.random() * (maxStep - minStep);
        const next = Math.min(prev + step, creepLimit);

        if (onProgressRef.current) onProgressRef.current(next);
        return next;
      });

      const delay = 900 + Math.random() * 500;
      timeoutId = setTimeout(loop, delay);
    };

    loop();
    return () => clearTimeout(timeoutId);
  }, []);

  const handleCompletion = async (progressData) => {
    if (isFinishing) return;
    setIsFinishing(true);

    try {
      const itemsResult = await getScrapeItems(scrapeId);
      if (onCompleteRef.current) {
        setTimeout(() => {
          onCompleteRef.current({
            ...progressData,
            items: itemsResult.items,
            platform_statuses: itemsResult.platform_statuses,
          });
        }, 800);
      }
    } catch (err) {
      const msg = "Search completed, but failed to load results: " + err.message;
      setError(msg);
      if (onErrorRef.current) onErrorRef.current(msg);
    }
  };

  if (error) {
    const errorStr = String(error).toLowerCase();
    const isDbError = errorStr.includes("violates") || errorStr.includes("sql") || errorStr.includes("exception") || errorStr.includes("constraint");
    const displayError = isDbError ? "Search could not save some items. Please try again." : error;

    return (
      <div className="scrape-progress-container error">
        <div className="error-icon">!</div>
        <h3>Search Failed</h3>
        <p>{displayError}</p>
        <button onClick={() => window.location.reload()} className="secondary-button">Try Again</button>
      </div>
    );
  }

  let displayMsg = "Preparing search...";
  const rawStage = String(data?.stage || data?.phase || stateRef.current.stage || "search").toLowerCase();
  const stage = (rawStage === "scrape" || rawStage === "scraping") ? "search" : rawStage;

  if (data?.message) displayMsg = data.message;
  else if (isFinishing || stage === "finalizing") displayMsg = "Finalizing...";
  else if (stage === "search") displayMsg = "Searching sources...";
  else if (stage === "transcripts") displayMsg = "Processing transcripts...";
  else if (stage === "done") displayMsg = "Completed.";

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
              transition: "width 420ms cubic-bezier(0.22, 1, 0.36, 1)",
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
