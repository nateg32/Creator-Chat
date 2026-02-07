import { useEffect, useState, useRef } from "react";
import { getScrapeProgress, getScrapeItems } from "../api/client";
import "./ScrapeProgress.css";

export function ScrapeProgress({ scrapeId, onComplete, onProgress, onError }) {
  const [progressData, setProgressData] = useState(null);
  const [displayProgress, setDisplayProgress] = useState(0);
  const [fetchingItems, setFetchingItems] = useState(false);
  const [error, setError] = useState(null);

  // Smoothly move displayProgress towards actual backend percentage or milestones
  useEffect(() => {
    const target = progressData?.percentage || 0;
    if (target > displayProgress) {
      // Slowly creep towards target to avoid jumps
      const interval = setInterval(() => {
        setDisplayProgress(prev => {
          if (prev >= target) {
            clearInterval(interval);
            return prev;
          }
          const diff = target - prev;
          const step = Math.max(0.1, diff * 0.05); // Move 5% of the distance each tick
          const next = Math.min(prev + step, target);
          if (onProgress) onProgress(next);
          return next;
        });
      }, 50);
      return () => clearInterval(interval);
    }
  }, [progressData?.percentage, displayProgress, onProgress]);

  // Polling logic
  useEffect(() => {
    if (!scrapeId) return;

    // Initial fetch jump
    setDisplayProgress(5);
    if (onProgress) onProgress(5);

    const pollInterval = setInterval(async () => {
      try {
        const data = await getScrapeProgress(scrapeId); // Changed from getSearchProgress to getScrapeProgress to match import
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
        // Don't stop polling on transient errors unless 404 persists (omitted for brevity)
      }
    }, 1500);

    return () => clearInterval(pollInterval);
  }, [scrapeId, onProgress, onError]); // Added onProgress, onError to dependency array

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

  const displayPct = Math.round(displayProgress);

  // Status message based on real data
  let statusMsg = "Initializing search agents...";
  if (fetchingItems) statusMsg = "Finalizing results...";
  else if (progressData?.current_platform) statusMsg = `Scanning ${progressData.current_platform}...`;
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
