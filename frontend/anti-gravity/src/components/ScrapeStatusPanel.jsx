import { useState, useEffect } from "react";
import { getScrapeRuns, startScrapeRun } from "../api/client";
import "./ScrapeStatusPanel.css";
import { useFeedback } from "./feedback/FeedbackProvider";

export function ScrapeStatusPanel({ creatorId }) {
    const { toast } = useFeedback();
    const [runs, setRuns] = useState([]);
    const [loading, setLoading] = useState(false);
    const [triggering, setTriggering] = useState(false);

    useEffect(() => {
        if (creatorId) fetchRuns();
    }, [creatorId]);

    async function fetchRuns() {
        try {
            setLoading(true);
            const data = await getScrapeRuns(creatorId);
            setRuns(data.runs || []);
        } catch (e) {
            console.error("Failed to load scrape runs", e);
        } finally {
            setLoading(false);
        }
    }

    async function handleRun() {
        setTriggering(true);
        try {
            await startScrapeRun(creatorId);
            // Wait for backend to start, then refresh
            setTimeout(fetchRuns, 2000);
        } catch (e) {
            toast.error("Failed to start scrape: " + e.message);
        } finally {
            setTriggering(false);
        }
    }

    if (!creatorId) return null;

    const latestRun = runs.length > 0 ? runs[0] : null;

    return (
        <div className="scrape-status-panel">
            <div className="status-header">
                <h3>Sync Status</h3>
                <button
                    onClick={handleRun}
                    disabled={triggering || (latestRun && latestRun.status === 'RUNNING')}
                    className="sync-button"
                >
                    {triggering ? "Starting..." : "Sync Now"}
                </button>
            </div>

            {loading && !runs.length ? (
                <div className="status-body">Loading history...</div>
            ) : latestRun ? (
                <div className="status-body">
                    <div className="run-card">
                        <div className="run-info">
                            <div className="run-main">
                                <span className={`status-badge ${latestRun.status.toLowerCase()}`}>
                                    {latestRun.status}
                                </span>
                                <span className="run-time">
                                    {new Date(latestRun.started_at).toLocaleString()}
                                </span>
                                <span className="platform-tag">{latestRun.platform_key}</span>
                            </div>
                            {latestRun.status === 'RUNNING' && (
                                <div className="run-progress">
                                    Syncing...
                                </div>
                            )}
                            {latestRun.error_message && (
                                <div className="run-error">Error: {latestRun.error_message}</div>
                            )}
                        </div>

                        <div className="run-metrics">
                            <div className="metric">
                                <span className="val">{latestRun.items_fetched}</span>
                                <span className="lbl">Fetched</span>
                            </div>
                            <div className="metric">
                                <span className="val">{latestRun.items_new}</span>
                                <span className="lbl">New</span>
                            </div>
                            <div className="metric">
                                <span className="val">{latestRun.items_deduped}</span>
                                <span className="lbl">Deduped</span>
                            </div>
                            <div className="metric">
                                <span className="val">{latestRun.jobs_enqueued}</span>
                                <span className="lbl">Queued</span>
                            </div>
                        </div>
                    </div>

                    {runs.length > 1 && (
                        <div className="recent-runs-summary">
                            <small>Previous runs: {runs.slice(1, 4).map(r =>
                                <span key={r.id} title={new Date(r.started_at).toLocaleString()} className={`dot ${r.status.toLowerCase()}`}></span>
                            )}</small>
                        </div>
                    )}
                </div>
            ) : (
                <div className="status-body empty">
                    No sync history yet.
                </div>
            )}
        </div>
    );
}
