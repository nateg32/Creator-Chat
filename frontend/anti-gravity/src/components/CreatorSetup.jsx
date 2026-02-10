import { useState, useEffect, useCallback } from "react";
import {
  getPlatforms,
  validatePlatformUrl,
  createCreatorWithConfig,
  updateCreator,
  getCreatorConfig,
  scrape,
  getScrapeItems,
  getSearchProgress,
} from "../api/client";
import { resizeImage } from "../utils/image";
import "./CreatorSetup.css";

const TIME_MODES = [
  { value: "all", label: "All available" },
  { value: "last_days", label: "Last X days" },
  { value: "since", label: "Since date" },
];

export function CreatorSetup({
  onSaveConfig,
  onSearchStart,
  onSaveSuccess,
  loading,
  savedCreatorId,
  initialCreatorName = "",
  initialHandle = "",
  initialAvatarUrl = "",
  userAvatarUrl = "",
  onUserAvatarChange,
}) {
  const [platforms, setPlatforms] = useState([]);
  const [creatorName, setCreatorName] = useState(initialCreatorName);
  const [creatorHandle, setCreatorHandle] = useState(initialHandle);
  const [creatorAvatarUrl, setCreatorAvatarUrl] = useState(initialAvatarUrl);
  const [selected, setSelected] = useState(new Set());
  const [config, setConfig] = useState({});
  const [error, setError] = useState(null);
  const [saveLoading, setSaveLoading] = useState(false);
  const [scrapeLoading, setScrapeLoading] = useState(false);
  const [testStatus, setTestStatus] = useState({});

  useEffect(() => {
    getPlatforms()
      .then((data) => {
        console.log("Platforms loaded:", data);
        if (Array.isArray(data) && data.length > 0) {
          setPlatforms(data);
        } else {
          setError("No platforms available. Please check backend connection.");
        }
      })
      .catch((e) => {
        console.error("Failed to load platforms:", e);
        setError(e.message || "Failed to load platforms. Make sure backend is running.");
      });
  }, []);

  useEffect(() => {
    if (!savedCreatorId) return;
    getCreatorConfig(savedCreatorId)
      .then((data) => {
        const pc = data.platform_configs || {};
        const next = new Set();
        const cf = {};
        for (const [k, v] of Object.entries(pc)) {
          if (v && v.enabled && v.url) {
            next.add(k);
            cf[k] = {
              url: v.url,
              timeFilter: v.timeFilter || { mode: "all" },
              maxItems: v.maxItems,
            };
          }
        }
        setSelected(next);
        setConfig(cf);
        setCreatorName(data.name || "");
        setCreatorHandle(data.handle || "");
        setCreatorAvatarUrl(data.profile_picture_url || "");
      })
      .catch(() => { });
  }, [savedCreatorId]);

  const togglePlatform = (key) => {
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });
    setError(null);
  };

  const updatePlatformConfig = (key, patch) => {
    setConfig((prev) => ({
      ...prev,
      [key]: { ...(prev[key] || {}), ...patch },
    }));
    setError(null);
    setTestStatus((s) => ({ ...s, [key]: null }));
  };

  const valid = useCallback(() => {
    if (selected.size === 0) return false;
    for (const k of selected) {
      const c = config[k];
      if (!c || !(c.url || "").trim()) return false;
    }
    return true;
  }, [selected, config]);

  const handleTestLink = async (key) => {
    const c = config[key];
    const url = (c?.url || "").trim();
    if (!url) {
      setTestStatus((s) => ({ ...s, [key]: "Enter a URL first" }));
      return;
    }
    setTestStatus((s) => ({ ...s, [key]: "Checking…" }));
    try {
      const res = await validatePlatformUrl(key, url);
      setTestStatus((s) => ({
        ...s,
        [key]: res.valid ? "Valid" : (res.error || "Invalid"),
      }));
    } catch (e) {
      setTestStatus((s) => ({ ...s, [key]: e.message || "Error" }));
    }
  };

  const handleAvatarUpload = async (e, type) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const base64 = await resizeImage(file);
      if (type === "creator") {
        setCreatorAvatarUrl(base64);
      } else {
        onUserAvatarChange(base64);
      }
    } catch (err) {
      setError("Failed to process image: " + err.message);
    }
  };

  const handleSave = async (e) => {
    e.preventDefault();
    if (!valid() || saveLoading) return;
    setError(null);
    setSaveLoading(true);
    try {
      const platform_configs = {};
      for (const k of selected) {
        const c = config[k];
        const url = (c?.url || "").trim();
        if (!url) continue;
        const plat = platforms.find((p) => p.key === k);
        const maxItems = c?.maxItems ?? plat?.default_max_items ?? 10;
        const mode = c?.timeFilter?.mode || "all";
        let timeFilter = { mode };
        if (mode === "since") {
          timeFilter.since = (c?.timeFilter?.since || "").trim() || null;
          timeFilter.days = undefined;
        } else if (mode === "last_days") {
          timeFilter.days = c?.timeFilter?.days ?? 30;
          timeFilter.since = undefined;
        } else {
          timeFilter.since = undefined;
          timeFilter.days = undefined;
        }
        platform_configs[k] = {
          enabled: true,
          url,
          timeFilter,
          maxItems: Math.min(Math.max(1, Number(maxItems) || 10), 50),
        };
      }
      if (savedCreatorId) {
        await updateCreator(savedCreatorId, {
          name: creatorName.trim() || undefined,
          handle: creatorHandle.trim() || undefined,
          profile_picture_url: creatorAvatarUrl.trim() || undefined,
          platform_configs,
        });
        onSaveConfig({ creatorId: savedCreatorId, name: creatorName, handle: creatorHandle, profile_picture_url: creatorAvatarUrl });
      } else {
        const res = await createCreatorWithConfig({
          name: creatorName.trim() || undefined,
          handle: creatorHandle.trim() || undefined,
          profile_picture_url: creatorAvatarUrl.trim() || undefined,
          platform_configs,
        });
        onSaveConfig({ creatorId: res.id, name: res.name, handle: res.handle, profile_picture_url: res.profile_picture_url });
      }
      onSaveSuccess?.();
    } catch (err) {
      setError(err.message || "Save failed");
    } finally {
      setSaveLoading(false);
    }
  };

  const handleScrape = async (e) => {
    e.preventDefault();
    const id = savedCreatorId;
    if (!id || scrapeLoading || loading) return;
    setError(null);
    setScrapeLoading(true);
    try {
      const result = await scrape({ creator_id: id });
      if (onSearchStart) {
        onSearchStart(result.scrape_id);
      }
    } catch (err) {
      setError(err.message || "Search failed");
      setScrapeLoading(false);
    }
  };

  const offset = 0; // cleanup unused vars dummy

  const canScrape = Boolean(savedCreatorId) && !scrapeLoading && !loading;

  return (
    <div className="creator-setup-card">
      <h2>Create a bot</h2>
      <p className="subtitle">Select platforms, add profile URLs, and set time filters. Save before searching.</p>

      {platforms.length === 0 && !error && (
        <div style={{ textAlign: "center", padding: "20px" }}>
          <div className="progress-spinner" style={{ margin: "0 auto 12px" }}></div>
          <p className="muted">Loading platforms…</p>
        </div>
      )}
      {error && (
        <div className="error-message" style={{ marginBottom: "16px" }}>
          {error}
          {(error.includes("Cannot connect") || error.includes("Network error") || error.includes("timeout") || error.includes("Failed to fetch")) && (
            <small style={{ marginTop: "8px", display: "block" }}>
              Make sure the backend is running on http://127.0.0.1:8000
            </small>
          )}
        </div>
      )}

      <form onSubmit={(e) => e.preventDefault()} className="setup-form">
        <div className="form-group">
          <label>Creator name (optional)</label>
          <input
            type="text"
            value={creatorName}
            onChange={(e) => setCreatorName(e.target.value)}
            placeholder="e.g. Dan Martell"
            disabled={saveLoading}
          />
        </div>
        <div className="form-group">
          <label>Handle (optional)</label>
          <input
            type="text"
            value={creatorHandle}
            onChange={(e) => setCreatorHandle(e.target.value)}
            placeholder="e.g. danmartell"
            disabled={saveLoading}
          />
        </div>

        <div className="form-group">
          <label>Platforms</label>
          <div className="platform-checkboxes">
            {platforms.map((p) => {
              const implemented = p.implemented !== false;
              return (
                <label key={p.key} className="platform-check">
                  <input
                    type="checkbox"
                    checked={selected.has(p.key)}
                    onChange={() => togglePlatform(p.key)}
                    disabled={saveLoading || !implemented}
                  />
                  <span className={`badge badge-${p.icon}`}>{p.label}</span>
                  {!implemented && <span className="coming-soon">Coming soon</span>}
                </label>
              );
            })}
          </div>
        </div>

        {platforms.filter((p) => selected.has(p.key)).map((p) => (
          <div key={p.key} className="platform-block">
            <h3 className="platform-block-title">{p.label}</h3>
            <div className="form-group">
              <label>{p.key === "custom" ? "Resource URLs (one per line)" : "Profile URL"}</label>
              <div className="url-row">
                {p.key === "custom" ? (
                  <textarea
                    value={config[p.key]?.url || ""}
                    onChange={(e) => updatePlatformConfig(p.key, { url: e.target.value })}
                    placeholder={p.placeholder}
                    disabled={saveLoading}
                    rows={6}
                    style={{ width: "100%", fontFamily: "monospace", resize: "vertical" }}
                  />
                ) : (
                  <input
                    type="text"
                    value={config[p.key]?.url || ""}
                    onChange={(e) => updatePlatformConfig(p.key, { url: e.target.value })}
                    placeholder={p.placeholder}
                    disabled={saveLoading}
                  />
                )}
                {p.key !== "custom" && (
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => handleTestLink(p.key)}
                    disabled={saveLoading}
                  >
                    Test link
                  </button>
                )}
              </div>
              {p.key !== "custom" && testStatus[p.key] && (
                <span className={`test-status ${testStatus[p.key] === "Valid" ? "ok" : "err"}`}>
                  {testStatus[p.key]}
                </span>
              )}
            </div>

            {p.key !== "custom" && (
              <div className="form-group">
                <label>Search from</label>
                <div className="time-mode-radios">
                  {TIME_MODES.map((m) => (
                    <label key={m.value}>
                      <input
                        type="radio"
                        name={`time-${p.key}`}
                        checked={(config[p.key]?.timeFilter?.mode || "all") === m.value}
                        onChange={() =>
                          updatePlatformConfig(p.key, {
                            timeFilter: { ...(config[p.key]?.timeFilter || {}), mode: m.value },
                          })
                        }
                        disabled={saveLoading}
                      />
                      {m.label}
                    </label>
                  ))}
                </div>
              </div>
            )}
            {(config[p.key]?.timeFilter?.mode === "last_days" || config[p.key]?.timeFilter?.mode === "since") && (
              <div className="form-group inline">
                {config[p.key]?.timeFilter?.mode === "last_days" && (
                  <>
                    <label>Days</label>
                    <select
                      value={config[p.key]?.timeFilter?.days ?? 30}
                      onChange={(e) =>
                        updatePlatformConfig(p.key, {
                          timeFilter: { ...(config[p.key]?.timeFilter || {}), days: Number(e.target.value) },
                        })
                      }
                      disabled={saveLoading}
                    >
                      {[7, 30, 90].map((d) => (
                        <option key={d} value={d}>{d}</option>
                      ))}
                    </select>
                  </>
                )}
                {config[p.key]?.timeFilter?.mode === "since" && (
                  <>
                    <label>Since</label>
                    <input
                      type="date"
                      value={config[p.key]?.timeFilter?.since || ""}
                      onChange={(e) =>
                        updatePlatformConfig(p.key, {
                          timeFilter: { ...(config[p.key]?.timeFilter || {}), since: e.target.value },
                        })
                      }
                      disabled={saveLoading}
                    />
                  </>
                )}
              </div>
            )}
            <div className="form-group">
              <label>Max items (default {p.default_max_items})</label>
              <input
                type="number"
                min={1}
                max={50}
                value={config[p.key]?.maxItems ?? p.default_max_items ?? 10}
                onChange={(e) => updatePlatformConfig(p.key, { maxItems: e.target.value })}
                disabled={saveLoading}
              />
            </div>
          </div>
        ))}

        <div className="button-row">
          <button
            type="button"
            className="primary-button"
            onClick={handleSave}
            disabled={!valid() || saveLoading || scrapeLoading}
          >
            {saveLoading ? "Saving…" : savedCreatorId ? "Update config" : "Save & Continue"}
          </button>

          <button
            type="button"
            className={`primary-button search-button ${scrapeLoading ? 'searching' : ''}`}
            onClick={handleScrape}
            disabled={!canScrape}
          >
            {scrapeLoading ? "Starting..." : "Search now"}
          </button>
        </div>
      </form>
    </div>
  );
}
