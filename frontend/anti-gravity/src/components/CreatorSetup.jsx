import { useState, useEffect, useCallback, useMemo, useRef } from "react";
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
import { API_BASE_URL, API_CONNECTION_HELP } from "../config";
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
  const [activePlatformKey, setActivePlatformKey] = useState(null);
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
  const [actionFeedback, setActionFeedback] = useState(null); // 'success' | 'error' | null
  const nameInputRef = useRef(null);
  const platformUrlRefs = useRef(new Map());
  const setPlatformUrlRef = useCallback((key, el) => {
    if (el) platformUrlRefs.current.set(key, el);
    else platformUrlRefs.current.delete(key);
  }, []);
  const visiblePlatforms = useMemo(
    () => platforms.filter((platform) => platform.key !== "reddit"),
    [platforms]
  );
  const selectedPlatformDetails = useMemo(
    () => visiblePlatforms.filter((platform) => selected.has(platform.key)),
    [selected, visiblePlatforms]
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

  const buildSearchSummary = useCallback((platformConfigOverride = null) => {
    const platform_configs = platformConfigOverride || buildPlatformConfigs();
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
        // Default the active tab to the first selected platform when loading an existing creator.
        setActivePlatformKey((prev) => (prev && next.has(prev) ? prev : ([...next][0] || null)));
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
        setSavedConfigSignature(JSON.stringify(
          Object.fromEntries(
            Object.entries(cf).map(([key, value]) => [
              key,
              {
                enabled: true,
                url: value.url,
                timeFilter: value.timeFilter || { mode: "all" },
                maxItems: value.maxItems,
              },
            ])
          )
        ));
        setTestStatus({ ...nextStatuses });
      })
      .catch(() => { });
  }, [platforms, savedCreatorId]);

  useEffect(() => {
    // Reserved for future per-platform side-effects when selection changes.
  }, [selectedPlatformDetails]);

  const togglePlatform = (key) => {
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(key)) {
        n.delete(key);
        // If we just removed the active one, fall back to another remaining selection.
        if (activePlatformKey === key) {
          const fallback = [...n][n.size - 1] || null;
          setActivePlatformKey(fallback);
        }
      } else {
        n.add(key);
        // Newly selected platform becomes the active one shown below.
        setActivePlatformKey(key);
      }
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
    setTestStatus((s) => ({ ...s, [key]: "Checking public link..." }));
    try {
      const res = await validatePlatformUrl(key, urlToValidate);
      if (res.valid) {
        const normalized = res.normalized || urlToValidate;
        if (normalized !== url) {
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

  const validateSelectedPlatforms = useCallback(async () => {
    const nextStatuses = { ...testStatus };
    const nextConfig = { ...config };
    let changed = false;
    const invalidMessages = [];

    for (const platform of selectedPlatformDetails) {
      const key = platform.key;
      const value = String(config[key]?.url || "").trim();
      if (!value) {
        nextStatuses[key] = "Enter a URL first";
        invalidMessages.push(platform.label);
        continue;
      }
      if (key === "custom") {
        const lines = value.split("\n").map((line) => line.trim()).filter(Boolean);
        nextStatuses[key] = lines.length ? "Ready" : "Enter at least one link";
        if (!lines.length) invalidMessages.push(platform.label);
        continue;
      }

      let urlToValidate = value;

      try {
        const res = await validatePlatformUrl(key, urlToValidate);
        if (!res.valid) {
          nextStatuses[key] = res.error || "Link invalid";
          invalidMessages.push(platform.label);
          continue;
        }
        const normalized = res.normalized || urlToValidate;
        nextStatuses[key] = res.scrape_ready === false
          ? (res.message || "Valid format, but scraping stays locked until the link can be verified publicly.")
          : (res.message || "Valid public link");
        if (normalized !== value) {
          nextConfig[key] = { ...(nextConfig[key] || {}), url: normalized };
          changed = true;
        }
      } catch (err) {
        nextStatuses[key] = err.message || "Link invalid";
        invalidMessages.push(platform.label);
      }
    }

    if (changed) {
      setConfig(nextConfig);
    }
    setTestStatus(nextStatuses);

    return {
      valid: invalidMessages.length === 0,
      invalidMessages,
      normalizedPlatformConfigs: (() => {
        const platformConfigs = {};
        for (const key of selected) {
          const c = nextConfig[key];
          const url = String(c?.url || "").trim();
          if (!url) continue;
          const plat = platforms.find((item) => item.key === key);
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
          platformConfigs[key] = {
            enabled: true,
            url,
            timeFilter,
            maxItems: Math.min(Math.max(1, Number(maxItems) || 10), 50),
          };
        }
        return platformConfigs;
      })(),
    };
  }, [config, platforms, selected, selectedPlatformDetails, testStatus]);

  const handleScrape = async (e) => {
    e.preventDefault();
    const id = savedCreatorId;
    if (!id || scrapeLoading || loading) return;
    if (duplicateCreator && !savedCreatorId) {
      onUseExistingCreator?.(duplicateCreator.id);
      return;
    }
    setError(null);

    try {
      const validation = await validateSelectedPlatforms();
      if (!validation.valid) {
        setError(`Fix the source links for ${validation.invalidMessages.join(", ")} before searching.`);
        return;
      }

      const normalizedSignature = JSON.stringify(validation.normalizedPlatformConfigs);
      if (normalizedSignature !== savedConfigSignature) {
        const res = await updateCreator(id, {
          name: creatorName.trim() || undefined,
          profile_picture_url: creatorAvatarUrl.trim() || undefined,
          platform_configs: validation.normalizedPlatformConfigs,
        });
        onSaveConfig({
          creatorId: id,
          name: res.name,
          handle: res.handle,
          profile_picture_url: res.profile_picture_url,
          status: res.status,
          visual_config: res.visual_config,
        });
        setSavedConfigSignature(normalizedSignature);
      }

      const { summary } = buildSearchSummary(validation.normalizedPlatformConfigs);
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

  // -------- System-engineering: contextual next-action state machine --------
  // The primary button always reflects the single next required action.
  const nextAction = useMemo(() => {
    if (saveLoading) return { kind: "loading", label: "Saving\u2026", reason: null };
    if (scrapeLoading) return { kind: "loading", label: "Searching\u2026", reason: null };
    if (!creatorName.trim() || nameError) return { kind: "blocked", label: "Add a creator name", reason: "name" };
    if (selected.size === 0) return { kind: "blocked", label: "Pick at least one source", reason: "sources" };
    const missingUrl = selectedPlatformDetails.find((p) => !(config[p.key]?.url || "").trim());
    if (missingUrl) return { kind: "blocked", label: `Add a ${missingUrl.label} URL`, reason: missingUrl.key };
    if (!savedCreatorId) return { kind: "save", label: "Save bot", reason: null };
    const dirty = buildConfigSignature() !== savedConfigSignature;
    if (dirty) return { kind: "update-and-search", label: "Update & search", reason: null };
    return { kind: "search", label: "Search now", reason: null };
  }, [saveLoading, scrapeLoading, creatorName, nameError, selected.size, selectedPlatformDetails, config, savedCreatorId, buildConfigSignature, savedConfigSignature]);

  const triggerNextAction = useCallback(async () => {
    if (nextAction.kind === "blocked" || nextAction.kind === "loading") {
      // Fly the user to whatever's blocking them.
      if (nextAction.reason === "name") {
        nameInputRef.current?.focus();
      } else if (typeof nextAction.reason === "string" && platformUrlRefs.current.has(nextAction.reason)) {
        platformUrlRefs.current.get(nextAction.reason)?.focus();
      }
      return;
    }
    try {
      if (nextAction.kind === "save") {
        await handleSave({ preventDefault: () => {} });
        setActionFeedback("success");
      } else {
        await handleScrape({ preventDefault: () => {} });
      }
    } catch {
      setActionFeedback("error");
    }
  }, [nextAction]); // eslint-disable-line react-hooks/exhaustive-deps

  // Clear feedback flash after the animation completes.
  useEffect(() => {
    if (!actionFeedback) return;
    const t = setTimeout(() => setActionFeedback(null), 700);
    return () => clearTimeout(t);
  }, [actionFeedback]);

  // -------- Keyboard shortcuts (system-engineering: command surface) --------
  useEffect(() => {
    function onKeyDown(e) {
      const target = e.target;
      const inEditable = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable);
      const meta = e.metaKey || e.ctrlKey;

      // Cmd/Ctrl+K: focus name input
      if (meta && e.key.toLowerCase() === "k") {
        e.preventDefault();
        nameInputRef.current?.focus();
        nameInputRef.current?.select();
        return;
      }
      // Cmd/Ctrl+Enter: trigger primary action
      if (meta && e.key === "Enter") {
        e.preventDefault();
        triggerNextAction();
        return;
      }
      // 1-8: toggle nth visible platform (only when not typing)
      if (!inEditable && !meta && /^[1-8]$/.test(e.key)) {
        const idx = parseInt(e.key, 10) - 1;
        const target = visiblePlatforms[idx];
        if (target && target.implemented !== false) {
          e.preventDefault();
          togglePlatform(target.key);
        }
      }
      // Esc: close search confirm modal
      if (e.key === "Escape" && showSearchConfirm) {
        setShowSearchConfirm(false);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [triggerNextAction, visiblePlatforms, showSearchConfirm]);

  // Auto-focus the active platform's URL input when it changes (e.g., new tile clicked).
  useEffect(() => {
    if (!activePlatformKey) return;
    if (!(config[activePlatformKey]?.url || "").trim()) {
      const el = platformUrlRefs.current.get(activePlatformKey);
      const t = setTimeout(() => el?.focus(), 60);
      return () => clearTimeout(t);
    }
  }, [activePlatformKey]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="creator-setup-card">
      <div className="setup-hero">
        <div className="setup-hero-text">
          <div className="setup-kicker">
            <span className="setup-kicker-dot" aria-hidden="true" />
            Setup
          </div>
          <h2>Create a bot</h2>
          <p className="subtitle">Name. Sources. Search.</p>
        </div>
      </div>

      <ol className="setup-progress" aria-label="Setup progress">
        {[
          { key: "name", label: "Name", done: creatorName.trim().length >= 2 && !nameError },
          { key: "sources", label: "Sources", done: selectedPlatformDetails.length > 0 },
          { key: "configure", label: "Configure", done: configuredPlatformCount > 0 && configuredPlatformCount === selectedPlatformDetails.length },
          { key: "ready", label: "Ready", done: readyPlatformCount > 0 && readyPlatformCount === selectedPlatformDetails.length },
        ].map((step, idx, arr) => {
          const prevDone = idx === 0 ? true : arr[idx - 1].done;
          const state = step.done ? "done" : prevDone ? "active" : "idle";
          return (
            <li key={step.key} className={`setup-progress-step ${state}`}>
              <span className="setup-progress-marker" aria-hidden="true">
                {step.done ? "\u2713" : idx + 1}
              </span>
              <span className="setup-progress-label">{step.label}</span>
            </li>
          );
        })}
      </ol>

      {platforms.length === 0 && !error && (
        <div style={{ textAlign: "center", padding: "20px" }}>
          <div className="progress-spinner" style={{ margin: "0 auto 12px" }}></div>
          <p className="muted">Loading platforms...</p>
        </div>
      )}
      {duplicateCreator && !savedCreatorId && (
        <div className="duplicate-creator-banner">
          <span className="duplicate-creator-message">
            <strong>{duplicateCreator.name || duplicateCreator.handle || "This creator"}</strong> already exists.
          </span>
          <button
            type="button"
            className="link-button"
            onClick={() => onUseExistingCreator?.(duplicateCreator.id)}
          >
            Edit existing
          </button>
        </div>
      )}

      {error && (
        <div className="error-message" style={{ marginBottom: "16px" }}>
          {error}
          {(error.includes("Cannot connect") || error.includes("Network error") || error.includes("timeout") || error.includes("Failed to fetch")) && (
            <small style={{ marginTop: "8px", display: "block" }}>
              API target: {API_BASE_URL}. {API_CONNECTION_HELP}
            </small>
          )}
        </div>
      )}

      <form onSubmit={(e) => e.preventDefault()} className="setup-form">
        <section className="setup-section">
          <div className="setup-section-head">
            <div>
              <div className="setup-section-kicker">Identity</div>
              <h3>Profile</h3>
            </div>
          </div>
          <div className="setup-section-body">
            <div className="form-group">
              <label>Creator name</label>
              <input
                type="text"
                ref={nameInputRef}
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
              <h3>Platforms</h3>
            </div>
            <div className="setup-section-meta">
              <kbd className="kbd-hint">1</kbd>&ndash;<kbd className="kbd-hint">8</kbd> to toggle
            </div>
          </div>
          <div className="setup-section-body">
            <div className="platform-checkboxes">
              {visiblePlatforms.map((p, idx) => {
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
                    <span className="platform-check-content">
                      <span className={`badge badge-${p.icon}`}>{p.label}</span>
                      {!implemented && <span className="coming-soon">Coming soon</span>}
                      {implemented && idx < 8 && <kbd className="kbd-hint kbd-hint-tile">{idx + 1}</kbd>}
                    </span>
                  </label>
                );
              })}
            </div>

            {selectedPlatformDetails.length > 0 && (
              <div className="setup-platform-tabs" role="tablist" aria-label="Selected platforms">
                {selectedPlatformDetails.map((platform) => {
                  const isActive = activePlatformKey === platform.key;
                  const hasUrl = !!String(config[platform.key]?.url || "").trim();
                  const status = String(testStatus[platform.key] || "").toLowerCase();
                  const verified = status.startsWith("valid public link");
                  return (
                    <button
                      key={platform.key}
                      type="button"
                      role="tab"
                      aria-selected={isActive}
                      className={`platform-tab ${isActive ? "active" : ""} ${verified ? "verified" : hasUrl ? "filled" : ""}`}
                      onClick={() => setActivePlatformKey(platform.key)}
                    >
                      <span className={`badge badge-${platform.icon}`}>{platform.label}</span>
                      {verified && <span className="platform-tab-dot" aria-hidden="true">✓</span>}
                    </button>
                  );
                })}
              </div>
            )}

            {selectedPlatformDetails.length > 0 && activePlatformKey && (() => {
              const platform = selectedPlatformDetails.find((p) => p.key === activePlatformKey) || selectedPlatformDetails[0];
              const cfg = config[platform.key] || {};
              const status = testStatus[platform.key];
              const statusText = String(status || "").toLowerCase();
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
              <div className="setup-platform-list">
                  <div key={platform.key} className="platform-block">
                      <div className="platform-block-header">
                        <div>
                          <div className="platform-block-eyebrow">Source</div>
                          <h3 className="platform-block-title">{platform.label}</h3>
                        </div>
                        <button
                          type="button"
                          className="platform-block-remove"
                          onClick={() => togglePlatform(platform.key)}
                          aria-label={`Remove ${platform.label}`}
                          title="Remove this source"
                          disabled={saveLoading}
                        >
                          &times;
                        </button>
                      </div>
                      <div className="form-group">
                        <label>{platform.key === "custom" ? "Resource URLs" : "Profile URL"}</label>
                        <div className="url-row">
                          {platform.key === "custom" ? (
                            <textarea
                              ref={(el) => setPlatformUrlRef(platform.key, el)}
                              value={cfg.url || ""}
                              onChange={(e) => updatePlatformConfig(platform.key, { url: e.target.value })}
                              placeholder={platform.placeholder}
                              disabled={saveLoading}
                              rows={6}
                              style={{ width: "100%", fontFamily: "monospace", resize: "vertical" }}
                            />
                          ) : (
                            <input
                              type="text"
                              ref={(el) => setPlatformUrlRef(platform.key, el)}
                              value={cfg.url || ""}
                              onChange={(e) => updatePlatformConfig(platform.key, { url: e.target.value })}
                              placeholder={platform.placeholder}
                              disabled={saveLoading}
                            />
                          )}
                        </div>
                        {platform.key === "tiktok" && (
                          <div className="validation-hint">Video links auto-convert to the creator profile.</div>
                        )}
                        {platform.key !== "custom" && (
                          <div className="url-row-actions">
                            <button
                              type="button"
                              className="link-button"
                              onClick={() => handleTestLink(platform.key)}
                              disabled={saveLoading || !(cfg.url || "").trim()}
                            >
                              Verify link
                            </button>
                            {status && <span className={`test-status ${statusClass}`}>{status}</span>}
                          </div>
                        )}
                      </div>

                      {platform.key !== "custom" && (
                        <div className="form-group">
                          <label>Search from</label>
                          <div className="time-mode-radios">
                            {TIME_MODES.map((m) => (
                              <label key={m.value}>
                                <input
                                  type="radio"
                                  name={`time-mode-${platform.key}`}
                                  value={m.value}
                                  checked={(cfg.timeFilter?.mode || "all") === m.value}
                                  onChange={() =>
                                    updatePlatformConfig(platform.key, {
                                      timeFilter: { ...(cfg.timeFilter || {}), mode: m.value },
                                    })
                                  }
                                  disabled={saveLoading}
                                />
                                <span className="time-mode-label-text">{m.label}</span>
                              </label>
                            ))}
                          </div>
                        </div>
                      )}
                      {(cfg.timeFilter?.mode === "last_days" || cfg.timeFilter?.mode === "since") && (
                        <div className="form-group inline">
                          {cfg.timeFilter?.mode === "last_days" && (
                            <>
                              <label>Days</label>
                              <select
                                value={cfg.timeFilter?.days ?? 30}
                                onChange={(e) =>
                                  updatePlatformConfig(platform.key, {
                                    timeFilter: { ...(cfg.timeFilter || {}), days: Number(e.target.value) },
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
                          {cfg.timeFilter?.mode === "since" && (
                            <>
                              <label>Since</label>
                              <input
                                type="date"
                                value={cfg.timeFilter?.since || ""}
                                onChange={(e) =>
                                  updatePlatformConfig(platform.key, {
                                    timeFilter: { ...(cfg.timeFilter || {}), since: e.target.value },
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
                          value={cfg.maxItems ?? platform.default_max_items ?? 10}
                          onChange={(e) => updatePlatformConfig(platform.key, { maxItems: e.target.value })}
                          disabled={saveLoading}
                        />
                      </div>
                    </div>
              </div>
              );
            })()}
          </div>
        </section>

        <div className="setup-footer">
          {showSearchConfirm && (
            <div className="search-confirm-inline" role="region" aria-label="Search summary">
              <div className="search-confirm-head">
                <span className="search-confirm-eyebrow">Ready to scrape</span>
                <button
                  type="button"
                  className="search-confirm-cancel"
                  onClick={() => setShowSearchConfirm(false)}
                  aria-label="Cancel"
                >
                  &times;
                </button>
              </div>
              <ul className="search-confirm-list">
                {searchSummary.map((item) => (
                  <li key={item.key} className="search-confirm-item">
                    <span className={`badge badge-${item.key === "youtube_shorts" ? "youtube" : item.key}`}>{item.label}</span>
                    <span className="search-confirm-url" title={item.url}>{item.url}</span>
                    <span className="search-confirm-meta">{item.timeLabel} &middot; {item.maxItems}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="setup-action-row">
            {showSearchConfirm ? (
              <>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => setShowSearchConfirm(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="primary-action primary-action-search"
                  onClick={handleConfirmScrape}
                  aria-busy={scrapeLoading}
                >
                  <span className="primary-action-spinner" aria-hidden="true" />
                  <span className="primary-action-label">Confirm &amp; Search</span>
                </button>
              </>
            ) : (
              <button
                type="button"
                className={`primary-action primary-action-${nextAction.kind} ${actionFeedback ? `primary-action-${actionFeedback}` : ""}`}
                onClick={triggerNextAction}
                aria-busy={nextAction.kind === "loading"}
                data-reason={nextAction.kind === "blocked" ? nextAction.label : undefined}
                title={nextAction.kind === "blocked" ? nextAction.label : "\u2318 Enter"}
              >
                <span className="primary-action-spinner" aria-hidden="true" />
                <span className="primary-action-label">{nextAction.label}</span>
                {(nextAction.kind === "save" || nextAction.kind === "search" || nextAction.kind === "update-and-search") && (
                  <kbd className="kbd-hint kbd-hint-on-dark">&#8984;&#8629;</kbd>
                )}
              </button>
            )}
          </div>
        </div>
      </form>

    </div>
  );
}
