import { useEffect, useState, useRef } from "react";
import { getScrapeProgress, getScrapeItems } from "../api/client";
import "./ScrapeProgress.css";

export function ScrapeProgress({ scrapeId, onComplete, onProgress, onError }) {
  const [progressData, setProgressData] = useState(null);
  const [displayProgress, setDisplayProgress] = useState(0);
  const [fetchingItems, setFetchingItems] = useState(false);
  const [error, setError] = useState(null);

  // Smoothly move displayProgress
  useEffect(() => {
    // If we have an error or complete, just sync to target immediately or let the other logic handle it
    const target = progressData?.percentage || 0;
    const isRunning = progressData?.status === "running";

    const interval = setInterval(() => {
      setDisplayProgress(prev => {
        // Case 1: We are behind the target (catch up)
        if (prev < target) {
          const diff = target - prev;
          // Move faster if far behind, slower if close
          const step = Math.max(0.5, diff * 0.1);
          const next = Math.min(prev + step, target);
          if (onProgress) onProgress(next);
          return next;
        }

        // Case 2: We are at or ahead of target, but still running (Ghost Progress)
        // This prevents the "stuck" feeling. We creep up to 95% slowly.
        if (isRunning && prev < 95) {
          // Very slow creep: 0.05% per 50ms = 1% per second
          const next = prev + 0.05;
          if (onProgress) onProgress(next);
          return next;
        }

        return prev;
      });
    }, 50);
    return () => clearInterval(interval);
  }, [progressData, onProgress]);

  // Polling logic
  useEffect(() => {
    if (!scrapeId) return;

    // Initial fetch jump
    if (displayProgress === 0) {
      setDisplayProgress(5);
      if (onProgress) onProgress(5);
    }

    const pollInterval = setInterval(async () => {
      try {
        const data = await getScrapeProgress(scrapeId);
        setProgressData(data); // Store raw data

        if (data.status === "completed") {
          clearInterval(pollInterval);
          setDisplayProgress(100);
          if (onProgress) onProgress(100);
          handleCompletion(data);
        } else if (data.status === "failed" || data.status === "error") {
          clearInterval(pollInterval);
          const errMsg = data.error || "Search failed";
          setError(errMsg);
          if (onError) onError(errMsg);
        }
      } catch (err) {
        console.error("Poll error:", err);
      }
    }, 800); // Faster polling (800ms)

    return () => clearInterval(pollInterval);
  }, [scrapeId, onProgress, onError]);

  const handleCompletion = async (data) => {
    if (fetchingItems) return;
    setFetchingItems(true);
    try {
      // Fetch the actual items
      const itemsResult = await getScrapeItems(scrapeId);
      if (onComplete) {
        // Pass combined result
        onComplete({ ...data, items: itemsResult.items, platform_statuses: itemsResult.platform_statuses });
      }
    } catch (err) {
      setError("Search completed, but failed to load results: " + err.message);
      if (onError) onError(err.message);
    } finally {
      setFetchingItems(false);
    }
  };

  const displayPct = Math.min(100, Math.round(displayProgress));

  // Status message based on real data
  let statusMsg = "Initializing search agents...";
  if (fetchingItems) statusMsg = "Finalizing results...";
  else if (progressData?.current_platform) {
    statusMsg = `Scanning ${progressData.current_platform}...`;
    if (progressData.items_found > 0) {
      statusMsg += ` (${progressData.items_found} items found)`;
    }
  }
  else if (progressData?.status === "running") statusMsg = "Searching for content...";

  if (error) {
    return (
      <div className="scrape-progress-container error">
        <div className="error-icon">⚠️</div>
        <h3>Search Failed</h3>
        <p>{error}</p>
        <button onClick={() => window.location.reload()} className="secondary-button">Try Again</button>
      </div>
    );
  }

  return (
    <div className="scrape-progress-container">
      <div className="progress-content">
        <h2>{fetchingItems ? "Finalizing..." : "Searching..."}</h2>
        <p className="status-message">{statusMsg}</p>

        <div className="progress-bar-container large">
          <div
            className="progress-bar-fill"
            style={{ width: `${displayPct}%`, transition: 'width 0.1s linear' }}
          ></div>
        </div>

        <div className="progress-stats">
          <span className="percentage">{displayPct}%</span>
        </div>

        {progressData && progressData.items_found > 0 && (
          <div className="items-found-tag">
            Found {progressData.items_found} items so far
          </div>
        )}
      </div>
    </div>
  );
}
