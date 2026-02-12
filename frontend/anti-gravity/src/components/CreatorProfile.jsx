import { useState, useEffect } from "react";
import { getCreatorStats } from "../api/client";
import { ScrapeStatusPanel } from "./ScrapeStatusPanel";

export function CreatorProfile({ creatorId, creator, onScrape, onApprove, onChat }) {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (creatorId) {
      loadStats();
    }
  }, [creatorId]);

  async function loadStats() {
    try {
      setLoading(true);
      const data = await getCreatorStats(creatorId);
      setStats(data);
    } catch (e) {
      console.error("Failed to load creator stats:", e);
    } finally {
      setLoading(false);
    }
  }

  if (!creator) {
    return (
      <div className="creator-profile">
        <div className="profile-empty">Select a creator to view profile</div>
      </div>
    );
  }

  return (
    <div className="creator-profile">
      <div className="profile-header">
        <h2>{creator.name}</h2>
        {creator.handle && <p className="profile-handle">@{creator.handle}</p>}
      </div>

      {creator.platforms && creator.platforms.length > 0 && (
        <div className="profile-platforms">
          <strong>Platforms:</strong> {creator.platforms.join(", ")}
        </div>
      )}

      {loading ? (
        <div className="profile-loading">Loading stats...</div>
      ) : stats ? (
        <div className="profile-stats">
          <div className="stat-item">
            <div className="stat-label">Last Search</div>
            <div className="stat-value">
              {stats.last_scrape_time
                ? new Date(stats.last_scrape_time).toLocaleDateString()
                : "Never"}
            </div>
          </div>
          <div className="stat-item">
            <div className="stat-label">Items Ingested</div>
            <div className="stat-value">{stats.items_ingested}</div>
          </div>
          <div className="stat-item">
            <div className="stat-label">Total Chunks</div>
            <div className="stat-value">{stats.total_chunks}</div>
          </div>
        </div>
      ) : null}

      {/* Advanced Scrape Status Panel */}
      <ScrapeStatusPanel creatorId={creatorId} />


      <div className="profile-actions">
        <button onClick={onScrape} className="action-button scrape-button">
          Search
        </button>
        <button onClick={onApprove} className="action-button approve-button">
          Approve
        </button>
        <button onClick={onChat} className="action-button chat-button">
          Chat
        </button>
      </div>
    </div>
  );
}
