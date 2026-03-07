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
import { normalizeCreatorName } from "../utils/nameFormatter";
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

  const isLinkValidated = useCallback((key) => {
    const status = String(testStatus[key] || "").toLowerCase();
    return status.startsWith("valid public link");
  }, [testStatus]);
  const [showSearchConfirm, setShowSearchConfirm] = useState(false);
  const [searchSummary, setSearchSummary] = useState([]);
  const [savedConfigSignature, setSavedConfigSignature] = useState("");

  const [nameError, setNameError] = useState(null);
  const [nameHint, setNameHint] = useState(null);
  const [nameOriginalBeforeFormat, setNameOriginalBeforeFormat] = useState(null);
  const [nameSuggestedAcronym, setNameSuggestedAcronym] = useState(null);

  const buildPlatformConfigs = useCallback(() => {
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
    return platform_configs;
  }, [config, platforms, selected]);

  const buildConfigSignature = useCallback(() => JSON.stringify(buildPlatformConfigs()), [buildPlatformConfigs]);

  const formatTimeFilterSummary = useCallback((timeFilter) => {
    const mode = timeFilter?.mode || "all";
    if (mode === "since" && timeFilter?.since) return `Since ${timeFilter.since}`;
    if (mode === "last_days" && timeFilter?.days) return `Last ${timeFilter.days} days`;
    return "All available";
  }, []);

  const validateSelectedLinksForSearch = useCallback(async () => {
    const platform_configs = buildPlatformConfigs();
    const nextConfig = { ...config };
    const summary = [];

    for (const [key, entry] of Object.entries(platform_configs)) {
      const platform = platforms.find((p) => p.key === key);
      if (!platform) continue;

      if (key !== "custom") {
        const res = await validatePlatformUrl(key, entry.url);
        if (!res.valid) {
          setTestStatus((s) => ({ ...s, [key]: res.error || "Invalid link" }));
          throw new Error(`${platform.label}: ${res.error || "Invalid link"}`);
        }
        const normalized = res.normalized || entry.url;
        nextConfig[key] = { ...(nextConfig[key] || {}), url: normalized };
        setTestStatus((s) => ({ ...s, [key]: "Valid public link" }));
        summary.push({
          key,
          label: platform.label,
          url: normalized,
          maxItems: entry.maxItems,
          timeLabel: formatTimeFilterSummary(entry.timeFilter),
        });
      } else {
        const lines = (entry.url || "").split('\n').map((line) => line.trim()).filter(Boolean);
        if (!lines.length) {
          throw new Error('Custom Links: add at least one link');
        }
        summary.push({
          key,
          label: platform.label,
          url: `${lines.length} custom link${lines.length === 1 ? "" : "s"}`,
          maxItems: entry.maxItems,
          timeLabel: "Manual links",
        });
      }
    }

    setConfig(nextConfig);
    return { summary, signature: JSON.stringify(platform_configs) };
  }, [buildPlatformConfigs, config, formatTimeFilterSummary, platforms]);


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
      .then(async (data) => {
        const pc = data.platform_configs || {};
        const next = new Set();
        const cf = {};
        const nextStatuses = {};
        for (const [k, v] of Object.entries(pc)) {
          if (v && v.enabled && v.url) {
            next.add(k);
            cf[k] = {
              url: v.url,
              timeFilter: v.timeFilter || { mode: "all" },
              maxItems: v.maxItems,
            };
            if (k === "custom") {
              nextStatuses[k] = "Valid format";
            }
          }
        }
        setSelected(next);
        setConfig(cf);
        setCreatorName(data.name || "");
        setCreatorHandle(data.handle || "");
        setCreatorAvatarUrl(data.profile_picture_url || "");
        setSavedConfigSignature(JSON.stringify(pc || {}));
        setTestStatus(nextStatuses);

        for (const [k, v] of Object.entries(cf)) {
          if (k === "custom") continue;
          try {
            const res = await validatePlatformUrl(k, v.url);
            nextStatuses[k] = res.valid ? "Valid public link" : (res.error || "Link invalid");
            if (res.valid && res.normalized && res.normalized !== v.url) {
              cf[k] = { ...cf[k], url: res.normalized };
            }
          } catch (err) {
            nextStatuses[k] = err.message || "Link invalid";
          }
        }
        setConfig({ ...cf });
        setTestStatus({ ...nextStatuses });
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
    if (Object.prototype.hasOwnProperty.call(patch, "url")) {
      setTestStatus((s) => ({ ...s, [key]: null }));
    }
  };

  const valid = useCallback(() => {
    if (!creatorName.trim() || nameError) return false;
    if (selected.size === 0) return false;
    for (const k of selected) {
      const c = config[k];
      if (!c || !(c.url || "").trim()) return false;
    }
    return true;
  }, [selected, config, creatorName, nameError]);

  const handleTestLink = async (key) => {
    const c = config[key];
    const url = (c?.url || "").trim();
    if (!url) {
      setTestStatus((s) => ({ ...s, [key]: "Enter a URL first" }));
      return;
    }
    setTestStatus((s) => ({ ...s, [key]: "Checking public link..." }));
    try {
      const res = await validatePlatformUrl(key, url);
      if (res.valid) {
        const normalized = res.normalized || url;
        if (normalized !== url) {
          updatePlatformConfig(key, { url: normalized });
        }
        setTestStatus((s) => ({
          ...s,
          [key]: "Valid public link",
        }));
        return;
      }
      setTestStatus((s) => ({
        ...s,
        [key]: res.error || "Link invalid",
      }));
    } catch (e) {
      setTestStatus((s) => ({ ...s, [key]: e.message || "Link invalid" }));
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

    // Final backend-parity check
    const norm = normalizeCreatorName(creatorName);
    if (!norm.isValid) {
      setNameError(norm.error);
      return;
    }

    setError(null);
    setSaveLoading(true);
    try {
      const platform_configs = buildPlatformConfigs();
      if (savedCreatorId) {
        const res = await updateCreator(savedCreatorId, {
          name: creatorName.trim() || undefined,
          handle: creatorHandle.trim() || undefined,
          profile_picture_url: creatorAvatarUrl.trim() || undefined,
          platform_configs,
        });
        onSaveConfig({ creatorId: savedCreatorId, name: res.name, handle: res.handle, profile_picture_url: res.profile_picture_url, status: res.status, visual_config: res.visual_config });
      } else {
        const res = await createCreatorWithConfig({
          name: creatorName.trim() || undefined,
          handle: creatorHandle.trim() || undefined,
          profile_picture_url: creatorAvatarUrl.trim() || undefined,
          platform_configs,
        });
        onSaveConfig({ creatorId: res.id, name: res.name, handle: res.handle, profile_picture_url: res.profile_picture_url, status: res.status, visual_config: res.visual_config });
      }
      setSavedConfigSignature(JSON.stringify(platform_configs));
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

    const currentSignature = buildConfigSignature();
    if (savedConfigSignature && currentSignature !== savedConfigSignature) {
      setError("Save config changes before searching so the scraper uses the latest links.");
      return;
    }

    try {
      const { summary } = await validateSelectedLinksForSearch();
      setSearchSummary(summary);
      setShowSearchConfirm(true);
    } catch (err) {
      setError(err.message || "Search failed");
    }
  };

  const handleConfirmScrape = async () => {
    const id = savedCreatorId;
    if (!id || scrapeLoading || loading) return;
    setError(null);
    setScrapeLoading(true);
    setShowSearchConfirm(false);
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

  const allSelectedLinksReady = useCallback(() => {
    if (selected.size === 0) return false;
    for (const key of selected) {
      const value = (config[key]?.url || "").trim();
      if (!value) return false;
      if (key === "custom") {
        const lines = value.split("\n").map((line) => line.trim()).filter(Boolean);
        if (!lines.length) return false;
        continue;
      }
      if (!isLinkValidated(key)) return false;
    }
    return true;
  }, [config, isLinkValidated, selected]);

  const canSaveConfig = valid() && allSelectedLinksReady() && !saveLoading && !scrapeLoading;
  const canScrape = Boolean(savedCreatorId) && allSelectedLinksReady() && !scrapeLoading && !loading;

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
          <label>Creator name</label>
          <input
            type="text"
            className={nameError ? "input-error" : ""}
            value={creatorName}
            onChange={(e) => {
              setCreatorName(e.target.value);
              if (!e.target.value.trim()) setNameError("Enter a creator name.");
              else if (e.target.value.trim().length < 2) setNameError("Name is too short.");
              else setNameError(null);
              setNameHint(null);
              setNameSuggestedAcronym(null);
            }}
            onBlur={() => {
              const res = normalizeCreatorName(creatorName);
              if (!res.isValid) {
                setNameError(res.error);
              } else {
                setNameError(null);
                if (res.flags.changed) {
                  if (!nameOriginalBeforeFormat) {
                    setNameOriginalBeforeFormat(creatorName);
                  }
                  setCreatorName(res.normalized);
                  setNameHint(`Formatted to: ${res.normalized}`);
                }
                if (res.flags.likelyAcronym && res.suggested) {
                  setNameSuggestedAcronym(res.suggested);
                }
              }
            }}
            placeholder="e.g. Dan Martell"
            disabled={saveLoading}
          />
          {nameError && <div className="validation-error">{nameError}</div>}
          {nameHint && (
            <div className="validation-hint">
              {nameHint}{" "}
              <button
                type="button"
                className="text-button"
                onClick={() => {
                  setCreatorName(nameOriginalBeforeFormat);
                  setNameOriginalBeforeFormat(null);
                  setNameHint(null);
                  setNameSuggestedAcronym(null);
                  setNameError(null);
                }}
              >
                Undo
              </button>
            </div>
          )}
          {nameSuggestedAcronym && (
            <div className="validation-suggestion">
              Looks like an acronym —{" "}
              <button
                type="button"
                className="text-button"
                onClick={() => {
                  setCreatorName(nameSuggestedAcronym);
                  setNameSuggestedAcronym(null);
                  setNameHint(null);
                }}
              >
                Use {nameSuggestedAcronym}
              </button>
            </div>
          )}
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
                <span className={`test-status ${String(testStatus[p.key]).toLowerCase().startsWith("valid") ? "ok" : "err"}`}>
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
            disabled={!canSaveConfig}
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

      {showSearchConfirm && (
        <div className="creator-setup-modal-overlay" onClick={() => setShowSearchConfirm(false)}>
          <div className="creator-setup-modal" onClick={(e) => e.stopPropagation()}>
            <div className="creator-setup-modal-header">
              <h3>Confirm search</h3>
              <p>Review the exact sources that will be scraped before continuing.</p>
            </div>
            <div className="creator-setup-modal-body">
              {searchSummary.map((item) => (
                <div key={item.key} className="search-summary-card">
                  <div className="search-summary-head">
                    <span className={`badge badge-${item.key === "youtube_shorts" ? "youtube" : item.key}`}>{item.label}</span>
                    <span className="search-summary-items">Up to {item.maxItems} items</span>
                  </div>
                  <div className="search-summary-url">{item.url}</div>
                  <div className="search-summary-time">{item.timeLabel}</div>
                </div>
              ))}
            </div>
            <div className="creator-setup-modal-footer">
              <button type="button" className="secondary-button" onClick={() => setShowSearchConfirm(false)}>
                Cancel
              </button>
              <button type="button" className="primary-button" onClick={handleConfirmScrape}>
                Confirm & Search
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
