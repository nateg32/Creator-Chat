import { useState, useEffect, useCallback, useMemo } from "react";
import {
  getPlatforms,
  validatePlatformUrl,
  createCreatorWithConfig,
  updateCreator,
  getCreatorConfig,
  scrape,
} from "../api/client";
import { resizeImage } from "../utils/image";
import { normalizeCreatorName } from "../utils/nameFormatter";
import "./CreatorSetup.css";

const TIME_MODES = [
  { value: "all", label: "All available" },
  { value: "last_days", label: "Last X days" },
  { value: "since", label: "Since date" },
];

function normalizeTikTokProfileUrl(url) {
  const value = String(url || "").trim();
  if (!value) return null;
  try {
    const parsed = new URL(value.startsWith("http") ? value : `https://${value}`);
    if (!parsed.hostname.toLowerCase().includes("tiktok.com")) return null;
    const first = (parsed.pathname || "").split("/").filter(Boolean)[0];
    if (!first || !first.startsWith("@")) return null;
    return `https://www.tiktok.com/${first}`;
  } catch {
    return null;
  }
}

export function CreatorSetup({
  onSaveConfig,
  onSearchStart,
  onSaveSuccess,
  loading,
  savedCreatorId,
  initialCreatorName = "",
  initialAvatarUrl = "",
  userAvatarUrl = "",
  onUserAvatarChange,
  existingCreators = [],
  onUseExistingCreator,
}) {
  const [platforms, setPlatforms] = useState([]);
  const [creatorName, setCreatorName] = useState(initialCreatorName);
  const [creatorAvatarUrl, setCreatorAvatarUrl] = useState(initialAvatarUrl);
  const [selected, setSelected] = useState(new Set());
  const [config, setConfig] = useState({});
  const [error, setError] = useState(null);
  const [saveLoading, setSaveLoading] = useState(false);
  const [scrapeLoading, setScrapeLoading] = useState(false);
  const [testStatus, setTestStatus] = useState({});
  const [activePlatformKey, setActivePlatformKey] = useState(null);

  const isLinkValidated = useCallback((key) => {
    const status = String(testStatus[key] || "").toLowerCase();
    return status.startsWith("valid public link");
  }, [testStatus]);
  const [showSearchConfirm, setShowSearchConfirm] = useState(false);
  const [searchSummary, setSearchSummary] = useState([]);
  const [savedConfigSignature, setSavedConfigSignature] = useState("");
  const visiblePlatforms = useMemo(
    () => platforms.filter((platform) => platform.key !== "reddit"),
    [platforms]
  );
  const selectedPlatformDetails = useMemo(
    () => visiblePlatforms.filter((platform) => selected.has(platform.key)),
    [selected, visiblePlatforms]
  );
  const activePlatform = useMemo(
    () => selectedPlatformDetails.find((platform) => platform.key === activePlatformKey) || selectedPlatformDetails[0] || null,
    [activePlatformKey, selectedPlatformDetails]
  );

  const duplicateCreator = useMemo(() => {
    const normalizedName = normalizeCreatorName(creatorName || "");
    const normalizedNameValue = normalizedName.isValid ? normalizedName.normalized.toLowerCase() : String(creatorName || "").trim().toLowerCase();

    return existingCreators.find((creator) => {
      if (savedCreatorId && creator.id === savedCreatorId) return false;
      const creatorNameValue = String(creator.name || creator.handle || "").trim().toLowerCase();
      if (normalizedNameValue && creatorNameValue && normalizedNameValue === creatorNameValue) return true;
      return false;
    }) || null;
  }, [creatorName, existingCreators, savedCreatorId]);

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

  const buildSearchSummary = useCallback(() => {
    const platform_configs = buildPlatformConfigs();
    const summary = [];

    for (const [key, entry] of Object.entries(platform_configs)) {
      const platform = platforms.find((p) => p.key === key);
      if (!platform) continue;

      if (key === "custom") {
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
        continue;
      }

      summary.push({
        key,
        label: platform.label,
        url: entry.url,
        maxItems: entry.maxItems,
        timeLabel: formatTimeFilterSummary(entry.timeFilter),
      });
    }

    return { summary, signature: JSON.stringify(platform_configs) };
  }, [buildPlatformConfigs, formatTimeFilterSummary, platforms]);


  useEffect(() => {
    getPlatforms()
      .then((data) => {
        console.log("Platforms loaded:", data);
        const nextPlatforms = Array.isArray(data)
          ? data.filter((platform) => platform.key !== "reddit")
          : [];
        if (nextPlatforms.length > 0) {
          setPlatforms(nextPlatforms);
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
    if (!savedCreatorId || platforms.length === 0) return;
    getCreatorConfig(savedCreatorId)
      .then(async (data) => {
        const pc = data.platform_configs || {};
        const next = new Set();
        const cf = {};
        const nextStatuses = {};
        const allowedKeys = new Set(platforms.map((platform) => platform.key));
        for (const [k, v] of Object.entries(pc)) {
          if (v && v.enabled && v.url && allowedKeys.has(k)) {
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
        setCreatorAvatarUrl(data.profile_picture_url || "");
        setTestStatus(nextStatuses);

        for (const [k, v] of Object.entries(cf)) {
          if (k === "custom") continue;
          try {
            const res = await validatePlatformUrl(k, v.url);
            nextStatuses[k] = res.valid ? (res.message || "Valid public link") : (res.error || "Link invalid");
            if (res.valid && res.normalized && res.normalized !== v.url) {
              cf[k] = { ...cf[k], url: res.normalized };
            }
          } catch (err) {
            nextStatuses[k] = err.message || "Link invalid";
          }
        }
        setConfig({ ...cf });
        setSavedConfigSignature(JSON.stringify(cf));
        setTestStatus({ ...nextStatuses });
      })
      .catch(() => { });
  }, [platforms, savedCreatorId]);

  useEffect(() => {
    if (!selectedPlatformDetails.length) {
      setActivePlatformKey(null);
      return;
    }
    if (!selectedPlatformDetails.some((platform) => platform.key === activePlatformKey)) {
      setActivePlatformKey(selectedPlatformDetails[0].key);
    }
  }, [activePlatformKey, selectedPlatformDetails]);

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

    let urlToValidate = url;
    if (key === "tiktok") {
      const normalizedTikTokUrl = normalizeTikTokProfileUrl(url);
      if (!normalizedTikTokUrl) {
        setTestStatus((s) => ({ ...s, [key]: "Enter a valid TikTok profile or video URL" }));
        return;
      }
      urlToValidate = normalizedTikTokUrl;
      if (normalizedTikTokUrl !== url) {
        updatePlatformConfig(key, { url: normalizedTikTokUrl });
      }
    }

    setTestStatus((s) => ({ ...s, [key]: key === "tiktok" ? "Checking TikTok account..." : "Checking public link..." }));
    try {
      const res = await validatePlatformUrl(key, urlToValidate);
      if (res.valid) {
        const normalized = res.normalized || urlToValidate;
        if (normalized !== urlToValidate) {
          updatePlatformConfig(key, { url: normalized });
        }
        const statusMessage = res.scrape_ready === false
          ? (res.message || "Valid format, but scraping stays locked until the link can be verified publicly.")
          : (res.message || "Valid public link");
        setTestStatus((s) => ({
          ...s,
          [key]: statusMessage,
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

    if (duplicateCreator && !savedCreatorId) {
      onUseExistingCreator?.(duplicateCreator.id);
      return;
    }

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
          profile_picture_url: creatorAvatarUrl.trim() || undefined,
          platform_configs,
        });
        onSaveConfig({ creatorId: savedCreatorId, name: res.name, handle: res.handle, profile_picture_url: res.profile_picture_url, status: res.status, visual_config: res.visual_config });
      } else {
        const res = await createCreatorWithConfig({
          name: creatorName.trim() || undefined,
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
    if (duplicateCreator && !savedCreatorId) {
      onUseExistingCreator?.(duplicateCreator.id);
      return;
    }
    setError(null);

    const currentSignature = buildConfigSignature();
    if (savedConfigSignature && currentSignature !== savedConfigSignature) {
      setError("Save config changes before searching so the scraper uses the latest links.");
      return;
    }

    try {
      const { summary } = buildSearchSummary();
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
  const configuredPlatformCount = useMemo(
    () => selectedPlatformDetails.filter((platform) => String(config[platform.key]?.url || "").trim()).length,
    [config, selectedPlatformDetails]
  );
  const readyPlatformCount = useMemo(
    () =>
      selectedPlatformDetails.filter((platform) => {
        const value = String(config[platform.key]?.url || "").trim();
        if (!value) return false;
        if (platform.key === "custom") {
          return value.split("\n").map((line) => line.trim()).filter(Boolean).length > 0;
        }
        return isLinkValidated(platform.key);
      }).length,
    [config, isLinkValidated, selectedPlatformDetails]
  );

  const canSaveConfig = valid() && allSelectedLinksReady() && !saveLoading && !scrapeLoading;
  const canScrape = Boolean(savedCreatorId) && allSelectedLinksReady() && !scrapeLoading && !loading;

  return (
    <div className="creator-setup-card">
      <div className="setup-hero">
        <div>
          <div className="setup-kicker">Setup</div>
          <h2>Create a bot</h2>
          <p className="subtitle">Name. Sources. Search.</p>
        </div>
        <div className="setup-summary">
          <div className="setup-summary-metric">
            <strong>{selectedPlatformDetails.length}</strong>
            <span>Platforms</span>
          </div>
          <div className="setup-summary-metric">
            <strong>{configuredPlatformCount}</strong>
            <span>Configured</span>
          </div>
          <div className="setup-summary-metric">
            <strong>{readyPlatformCount}</strong>
            <span>Ready</span>
          </div>
        </div>
      </div>

      {platforms.length === 0 && !error && (
        <div style={{ textAlign: "center", padding: "20px" }}>
          <div className="progress-spinner" style={{ margin: "0 auto 12px" }}></div>
          <p className="muted">Loading platforms...</p>
        </div>
      )}
      {duplicateCreator && !savedCreatorId && (
        <div className="warning-message" style={{ marginBottom: "16px" }}>
          {duplicateCreator.name || duplicateCreator.handle || "This creator"} already exists. Switch to edit mode instead of creating a duplicate.
          <button
            type="button"
            className="text-button"
            onClick={() => onUseExistingCreator?.(duplicateCreator.id)}
            style={{ marginLeft: "8px" }}
          >
            Edit existing creator
          </button>
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
        <section className="setup-section">
          <div className="setup-section-head">
            <div>
              <div className="setup-section-kicker">Identity</div>
              <h3>Creator profile</h3>
            </div>
          </div>
          <div className="setup-section-body">
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
                  Looks like an acronym -{" "}
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
          </div>
        </section>

        <section className="setup-section">
          <div className="setup-section-head">
            <div>
              <div className="setup-section-kicker">Sources</div>
              <h3>Select platforms</h3>
            </div>
          </div>
          <div className="setup-section-body">
            <div className="platform-checkboxes">
              {visiblePlatforms.map((p) => {
                const implemented = p.implemented !== false;
                const checked = selected.has(p.key);
                return (
                  <label key={p.key} className={`platform-check ${checked ? "selected" : ""} ${implemented ? "" : "disabled"}`}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => togglePlatform(p.key)}
                      disabled={saveLoading || !implemented}
                    />
                    <span className="platform-check-indicator" aria-hidden="true" />
                    <span className="platform-check-content">
                      <span className={`badge badge-${p.icon}`}>{p.label}</span>
                      {!implemented && <span className="coming-soon">Coming soon</span>}
                    </span>
                  </label>
                );
              })}
            </div>
          </div>
        </section>

        <section className="setup-section">
          <div className="setup-section-head">
            <div>
              <div className="setup-section-kicker">Configuration</div>
              <h3>{activePlatform ? activePlatform.label : "Configure sources"}</h3>
            </div>
          </div>
          <div className="setup-section-body setup-platform-list">
            {selectedPlatformDetails.length === 0 ? (
              <div className="setup-empty-state">
                Select a platform to continue.
              </div>
            ) : (
              <>
                <div className="platform-config-tabs" role="tablist" aria-label="Configured platform">
                  {selectedPlatformDetails.map((platform) => (
                    <button
                      key={platform.key}
                      type="button"
                      className={`platform-config-tab ${activePlatform?.key === platform.key ? "active" : ""}`}
                      onClick={() => setActivePlatformKey(platform.key)}
                    >
                      <span className={`badge badge-${platform.key === "youtube_shorts" ? "youtube" : platform.icon}`}>{platform.label}</span>
                    </button>
                  ))}
                </div>
                <div key={activePlatform.key} className="platform-block">
                  <div className="platform-block-header">
                    <div>
                      <div className="platform-block-eyebrow">Source</div>
                      <h3 className="platform-block-title">{activePlatform.label}</h3>
                    </div>
                    <span className={`badge badge-${activePlatform.key === "youtube_shorts" ? "youtube" : activePlatform.icon}`}>{activePlatform.label}</span>
                  </div>
                <div className="form-group">
                  <label>{activePlatform.key === "custom" ? "Resource URLs" : "Profile URL"}</label>
                  <div className="url-row">
                    {activePlatform.key === "custom" ? (
                      <textarea
                        value={config[activePlatform.key]?.url || ""}
                        onChange={(e) => updatePlatformConfig(activePlatform.key, { url: e.target.value })}
                        placeholder={activePlatform.placeholder}
                        disabled={saveLoading}
                        rows={6}
                        style={{ width: "100%", fontFamily: "monospace", resize: "vertical" }}
                      />
                    ) : (
                      <input
                        type="text"
                        value={config[activePlatform.key]?.url || ""}
                        onChange={(e) => updatePlatformConfig(activePlatform.key, { url: e.target.value })}
                        placeholder={activePlatform.placeholder}
                        disabled={saveLoading}
                      />
                    )}
                    {activePlatform.key !== "custom" && (
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={() => handleTestLink(activePlatform.key)}
                        disabled={saveLoading}
                      >
                        Verify
                      </button>
                    )}
                  </div>
                  {activePlatform.key === "tiktok" && (
                    <div className="validation-hint">Video links auto-convert to the creator profile.</div>
                  )}
                  {activePlatform.key !== "custom" && testStatus[activePlatform.key] && (() => {
                    const statusText = String(testStatus[activePlatform.key] || "").toLowerCase();
                    const isVerified = statusText.startsWith("valid public link");
                    const isWarning = !isVerified && (
                      statusText.startsWith("valid format") ||
                      statusText.startsWith("valid platform match") ||
                      statusText.includes("inconclusive") ||
                      statusText.includes("blocked live verification") ||
                      statusText.includes("scraping stays locked")
                    );
                    const statusClass = isVerified ? "ok" : (isWarning ? "warn" : "err");
                    return (
                      <span className={`test-status ${statusClass}`}>
                        {testStatus[activePlatform.key]}
                      </span>
                    );
                  })()}
                </div>

                {activePlatform.key !== "custom" && (
                  <div className="form-group">
                    <label>Search from</label>
                    <div className="time-mode-radios">
                      {TIME_MODES.map((m) => (
                        <label key={m.value}>
                          <input
                            type="radio"
                            name={`time-${activePlatform.key}`}
                            checked={(config[activePlatform.key]?.timeFilter?.mode || "all") === m.value}
                            onChange={() =>
                              updatePlatformConfig(activePlatform.key, {
                                timeFilter: { ...(config[activePlatform.key]?.timeFilter || {}), mode: m.value },
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
                {(config[activePlatform.key]?.timeFilter?.mode === "last_days" || config[activePlatform.key]?.timeFilter?.mode === "since") && (
                  <div className="form-group inline">
                    {config[activePlatform.key]?.timeFilter?.mode === "last_days" && (
                      <>
                        <label>Days</label>
                        <select
                          value={config[activePlatform.key]?.timeFilter?.days ?? 30}
                          onChange={(e) =>
                            updatePlatformConfig(activePlatform.key, {
                              timeFilter: { ...(config[activePlatform.key]?.timeFilter || {}), days: Number(e.target.value) },
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
                    {config[activePlatform.key]?.timeFilter?.mode === "since" && (
                      <>
                        <label>Since</label>
                        <input
                          type="date"
                          value={config[activePlatform.key]?.timeFilter?.since || ""}
                          onChange={(e) =>
                            updatePlatformConfig(activePlatform.key, {
                              timeFilter: { ...(config[activePlatform.key]?.timeFilter || {}), since: e.target.value },
                            })
                          }
                          disabled={saveLoading}
                        />
                      </>
                    )}
                  </div>
                )}
                <div className="form-group">
                  <label>Max items</label>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={config[activePlatform.key]?.maxItems ?? activePlatform.default_max_items ?? 10}
                    onChange={(e) => updatePlatformConfig(activePlatform.key, { maxItems: e.target.value })}
                    disabled={saveLoading}
                  />
                </div>
                </div>
              </>
            )}
          </div>
        </section>

        <div className="setup-footer">
          <div className="button-row">
            <button
              type="button"
              className="primary-button"
              onClick={handleSave}
              disabled={!canSaveConfig}
            >
              {saveLoading ? "Saving..." : savedCreatorId ? "Update config" : "Save & Continue"}
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
