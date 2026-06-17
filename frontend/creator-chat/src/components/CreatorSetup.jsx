import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  getPlatforms,
  validatePlatformUrl,
  createCreatorWithConfig,
  updateCreator,
  getCreatorConfig,
  scrape,
} from "../api/client";
import { normalizeCreatorName } from "../utils/nameFormatter";
import { API_BASE_URL, API_CONNECTION_HELP } from "../config";
import "./CreatorSetup.css";

const TIME_MODES = [
  { value: "all", label: "All available" },
  { value: "last_days", label: "Last X days" },
  { value: "since", label: "Since date" },
];

const HIDDEN_PLATFORM_KEYS = new Set(["reddit", "custom", "youtube_shorts"]);

const READY_TO_SEARCH_MESSAGE = "Ready to search.";
const DEFAULT_SCRAPE_LIMITS = {
  plan_label: "Free",
  max_platforms_per_search: 2,
  max_items_per_platform: 100,
  max_items_per_search: 100,
  monthly_item_allowance: 100,
  monthly_items_used: 0,
  monthly_items_remaining: 100,
};

function withScheme(value) {
  const trimmed = String(value || "").trim();
  if (!trimmed) return "";
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

function isProfileUrlShapeValid(platformKey, value) {
  const raw = String(value || "").trim();
  if (!raw) return false;

  if (platformKey === "instagram" && /^@?[\w.]+$/.test(raw)) return true;
  if (platformKey === "tiktok" && /^@?[\w.]+$/.test(raw)) return true;
  if (platformKey === "twitter" && /^@?[\w]+$/.test(raw)) return true;

  try {
    const url = new URL(withScheme(raw));
    const host = url.hostname.toLowerCase();
    const parts = url.pathname.split("/").filter(Boolean);
    const first = (parts[0] || "").toLowerCase();

    if (platformKey === "instagram") {
      return host.endsWith("instagram.com") && parts.length === 1 && !["reel", "reels", "p", "tv", "stories", "explore", "accounts"].includes(first);
    }
    if (platformKey === "tiktok") {
      return host.endsWith("tiktok.com") && parts.length === 1 && first.startsWith("@");
    }
    if (platformKey === "twitter") {
      return (host.endsWith("twitter.com") || host.endsWith("x.com")) && parts.length === 1 && !["home", "explore", "search", "i", "settings"].includes(first);
    }
    if (platformKey === "linkedin") {
      return host.endsWith("linkedin.com") && parts.length === 2 && ["in", "company"].includes(first);
    }
    if (platformKey === "facebook") {
      return (host.endsWith("facebook.com") || host.endsWith("fb.com")) && parts.length >= 1 && !["watch", "reel", "share", "events", "groups", "marketplace", "gaming", "login", "checkpoint", "recover", "help", "settings"].includes(first);
    }
    if (platformKey === "reddit") {
      return host.endsWith("reddit.com") && parts.length === 2 && ["user", "u"].includes(first);
    }
  } catch {
    return false;
  }

  return false;
}

function coerceSetupValidation(platformKey, url, resultOrError) {
  if (isProfileUrlShapeValid(platformKey, url)) {
    return {
      valid: true,
      scrape_ready: true,
      normalized: resultOrError?.normalized || url,
      message: READY_TO_SEARCH_MESSAGE,
    };
  }
  return resultOrError;
}

function formatDateInputValue(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatCompactNumber(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(number);
}

export function CreatorSetup({
  onSaveConfig,
  onSearchStart,
  onSaveSuccess,
  loading,
  savedCreatorId,
  initialCreatorName = "",
  initialAvatarUrl = "",
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
    return (
      status.startsWith("ready to search") ||
      status.startsWith("valid public link") ||
      status.startsWith("valid format")
    );
  }, [testStatus]);
  const [showSearchConfirm, setShowSearchConfirm] = useState(false);
  const [searchSummary, setSearchSummary] = useState([]);
  const [savedConfigSignature, setSavedConfigSignature] = useState("");
  const [hasSavedInSession, setHasSavedInSession] = useState(false);
  const [pendingSearchAfterSave, setPendingSearchAfterSave] = useState(false);
  const [actionFeedback, setActionFeedback] = useState(null); // 'success' | 'error' | null
  const [openDatePickerFor, setOpenDatePickerFor] = useState(null);
  const [maxItemsDrafts, setMaxItemsDrafts] = useState({});
  const nameInputRef = useRef(null);
  const isExistingCreatorRef = useRef(Boolean(savedCreatorId));
  const platformUrlRefs = useRef(new Map());
  const preserveSaveGateRef = useRef(false);
  const datePopoverRef = useRef(null);
  const setPlatformUrlRef = useCallback((key, el) => {
    if (el) platformUrlRefs.current.set(key, el);
    else platformUrlRefs.current.delete(key);
  }, []);
  const visiblePlatforms = useMemo(
    () => platforms.filter((platform) => !HIDDEN_PLATFORM_KEYS.has(platform.key)),
    [platforms]
  );
  const today = useMemo(() => new Date(), []);
  const todayIso = useMemo(() => formatDateInputValue(today), [today]);
  const selectedPlatformDetails = useMemo(
    () => visiblePlatforms.filter((platform) => selected.has(platform.key)),
    [selected, visiblePlatforms]
  );
  const scrapeLimits = useMemo(() => {
    const fromApi = platforms.find((platform) => platform?.limits)?.limits;
    return { ...DEFAULT_SCRAPE_LIMITS, ...(fromApi || {}) };
  }, [platforms]);
  const monthlyItemAllowance = Number(
    scrapeLimits.monthly_item_allowance ||
    scrapeLimits.max_items_per_search ||
    DEFAULT_SCRAPE_LIMITS.monthly_item_allowance
  );
  const monthlyItemsUsed = Number(scrapeLimits.monthly_items_used || 0);
  const monthlyItemsRemaining = Math.max(
    0,
    Number(
      scrapeLimits.monthly_items_remaining ??
      Math.max(0, monthlyItemAllowance - monthlyItemsUsed)
    )
  );
  const itemBudgetForSearch = Math.max(
    0,
    Math.min(
      Number(scrapeLimits.max_items_per_search || monthlyItemAllowance || Infinity),
      monthlyItemsRemaining
    )
  );
  const getPlatformItemLimit = useCallback(
    (platform) => Number(platform?.max_items_limit || scrapeLimits.max_items_per_platform || itemBudgetForSearch || DEFAULT_SCRAPE_LIMITS.max_items_per_platform),
    [itemBudgetForSearch, scrapeLimits.max_items_per_platform]
  );
  const selectedRequestedItems = useMemo(
    () =>
      selectedPlatformDetails.reduce((sum, platform) => {
        const cfg = config[platform.key] || {};
        const fallback = Math.min(Number(platform.default_max_items || 10), getPlatformItemLimit(platform));
        return sum + Math.max(1, Number(cfg.maxItems || fallback) || 1);
      }, 0),
    [config, getPlatformItemLimit, selectedPlatformDetails]
  );
  const limitIssue = useMemo(() => {
    if (selectedPlatformDetails.length > Number(scrapeLimits.max_platforms_per_search || Infinity)) {
      return `${scrapeLimits.plan_label} allows ${scrapeLimits.max_platforms_per_search} sources per search.`;
    }
    if (itemBudgetForSearch <= 0) {
      return `${scrapeLimits.plan_label} has no scrape items left this month.`;
    }
    if (selectedRequestedItems > Number(scrapeLimits.max_items_per_search || Infinity)) {
      return `${scrapeLimits.plan_label} allows ${formatCompactNumber(scrapeLimits.max_items_per_search)} total items per search.`;
    }
    if (selectedRequestedItems > itemBudgetForSearch) {
      return `${formatCompactNumber(itemBudgetForSearch)} scrape items left this month. Reduce the allocation.`;
    }
    const overLimit = selectedPlatformDetails.find((platform) => {
      const max = getPlatformItemLimit(platform);
      return Number(config[platform.key]?.maxItems || platform.default_max_items || 10) > max;
    });
    if (overLimit) {
      return `${scrapeLimits.plan_label} allows ${getPlatformItemLimit(overLimit)} items per source.`;
    }
    return null;
  }, [config, getPlatformItemLimit, itemBudgetForSearch, scrapeLimits, selectedPlatformDetails, selectedRequestedItems]);

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
      if (HIDDEN_PLATFORM_KEYS.has(k)) continue;
      const c = config[k];
      const url = (c?.url || "").trim();
      if (!url) continue;
      const plat = platforms.find((p) => p.key === k);
      const maxItems = Math.min(c?.maxItems ?? plat?.default_max_items ?? 10, getPlatformItemLimit(plat));
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
        maxItems: Math.max(1, Number(maxItems) || 10),
      };
    }
    return platform_configs;
  }, [config, getPlatformItemLimit, platforms, selected]);

  const buildConfigSignature = useCallback(() => JSON.stringify(buildPlatformConfigs()), [buildPlatformConfigs]);

  const formatTimeFilterSummary = useCallback((timeFilter) => {
    const mode = timeFilter?.mode || "all";
    if (mode === "since" && timeFilter?.since) return `Since ${timeFilter.since}`;
    if (mode === "last_days" && timeFilter?.days) return `Last ${timeFilter.days} days`;
    return "All available";
  }, []);

  const formatDateLabel = useCallback((value) => {
    if (!value) return "Pick a date";
    const parsed = new Date(`${value}T00:00:00`);
    if (Number.isNaN(parsed.getTime())) return value;
    return new Intl.DateTimeFormat("en-US", {
      month: "long",
      day: "numeric",
      year: "numeric",
    }).format(parsed);
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


  const loadPlatforms = useCallback(() => {
    getPlatforms()
      .then((data) => {
        const nextPlatforms = Array.isArray(data)
          ? data.filter((platform) => !HIDDEN_PLATFORM_KEYS.has(platform.key))
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
    loadPlatforms();
    window.addEventListener("plan-preview-changed", loadPlatforms);
    return () => window.removeEventListener("plan-preview-changed", loadPlatforms);
  }, [loadPlatforms]);

  useEffect(() => {
    if (!savedCreatorId || platforms.length === 0) return;
    getCreatorConfig(savedCreatorId)
      .then(async (data) => {
        const pc = data.platform_configs || {};
        const next = new Set();
        const cf = {};
        const nextStatuses = {};
        const allowedKeys = new Set(visiblePlatforms.map((platform) => platform.key));
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
            const res = coerceSetupValidation(k, v.url, await validatePlatformUrl(k, v.url));
            nextStatuses[k] = res.valid ? READY_TO_SEARCH_MESSAGE : (res.error || "Link invalid");
            if (res.valid && res.normalized && res.normalized !== v.url) {
              cf[k] = { ...cf[k], url: res.normalized };
            }
          } catch (err) {
            const coerced = coerceSetupValidation(k, v.url, err);
            nextStatuses[k] = coerced?.valid ? (coerced.message || READY_TO_SEARCH_MESSAGE) : (err.message || "Link invalid");
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
        if (preserveSaveGateRef.current) {
          setHasSavedInSession(true);
          preserveSaveGateRef.current = false;
        } else {
          setHasSavedInSession(false);
        }
        setTestStatus({ ...nextStatuses });
      })
      .catch(() => { });
  }, [platforms, savedCreatorId, visiblePlatforms]);

  useEffect(() => {
    setSelected((current) => {
      const cleaned = new Set([...current].filter((key) => !HIDDEN_PLATFORM_KEYS.has(key)));
      return cleaned.size === current.size ? current : cleaned;
    });
    setConfig((current) => {
      let changed = false;
      const cleaned = {};
      for (const [key, value] of Object.entries(current)) {
        if (HIDDEN_PLATFORM_KEYS.has(key)) {
          changed = true;
          continue;
        }
        cleaned[key] = value;
      }
      return changed ? cleaned : current;
    });
    setTestStatus((current) => {
      let changed = false;
      const cleaned = {};
      for (const [key, value] of Object.entries(current)) {
        if (HIDDEN_PLATFORM_KEYS.has(key)) {
          changed = true;
          continue;
        }
        cleaned[key] = value;
      }
      return changed ? cleaned : current;
    });
    setActivePlatformKey((current) => (HIDDEN_PLATFORM_KEYS.has(current) ? null : current));
  }, []);

  useEffect(() => {
    // Reserved for future per-platform side-effects when selection changes.
  }, [selectedPlatformDetails]);

  useEffect(() => {
    if (!openDatePickerFor) return undefined;

    function handleClickOutside(event) {
      if (datePopoverRef.current && !datePopoverRef.current.contains(event.target)) {
        setOpenDatePickerFor(null);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [openDatePickerFor]);

  const togglePlatform = useCallback((key) => {
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
        if (n.size >= Number(scrapeLimits.max_platforms_per_search || Infinity)) {
          setError(`${scrapeLimits.plan_label} allows ${scrapeLimits.max_platforms_per_search} sources per search.`);
          return prev;
        }
        n.add(key);
        // Newly selected platform becomes the active one shown below.
        setActivePlatformKey(key);
      }
      return n;
    });
    if (!limitIssue) setError(null);
  }, [activePlatformKey, limitIssue, scrapeLimits.max_platforms_per_search, scrapeLimits.plan_label]);

  const updatePlatformConfig = (key, patch) => {
    const platform = platforms.find((item) => item.key === key);
    const nextPatch = { ...patch };
    if (Object.prototype.hasOwnProperty.call(nextPatch, "maxItems")) {
      const max = getPlatformItemLimit(platform);
      nextPatch.maxItems = Math.min(Math.max(1, Number(nextPatch.maxItems) || 1), max);
    }
    setConfig((prev) => ({
      ...prev,
      [key]: { ...(prev[key] || {}), ...nextPatch },
    }));
    setError(null);
    if (Object.prototype.hasOwnProperty.call(nextPatch, "url")) {
      setTestStatus((s) => ({ ...s, [key]: null }));
    }
  };

  const beginMaxItemsEdit = (key, value) => {
    setMaxItemsDrafts((prev) => ({ ...prev, [key]: String(value) }));
  };

  const updateMaxItemsDraft = (key, value) => {
    if (!/^\d*$/.test(value)) return;
    setMaxItemsDrafts((prev) => ({ ...prev, [key]: value }));
  };

  const clearMaxItemsDraft = (key) => {
    setMaxItemsDrafts((prev) => {
      if (!Object.prototype.hasOwnProperty.call(prev, key)) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const commitMaxItemsDraft = (key, max) => {
    const raw = maxItemsDrafts[key];
    if (raw == null) return;
    const nextValue = Math.min(Math.max(1, Number(raw) || 1), max);
    updatePlatformConfig(key, { maxItems: nextValue });
    clearMaxItemsDraft(key);
  };

  const valid = useCallback(() => {
    if (!creatorName.trim() || nameError) return false;
    if (selected.size === 0) return false;
    if (limitIssue) return false;
    for (const k of selected) {
      const c = config[k];
      if (!c || !(c.url || "").trim()) return false;
    }
    return true;
  }, [selected, config, creatorName, nameError, limitIssue]);

  const handleSave = async (e) => {
    e.preventDefault();
    if (!valid() || saveLoading) return false;

    if (duplicateCreator && !savedCreatorId) {
      onUseExistingCreator?.(duplicateCreator.id);
      return false;
    }

    // Final backend-parity check
    const norm = normalizeCreatorName(creatorName);
    if (!norm.isValid) {
      setNameError(norm.error);
      return false;
    }

    setError(null);
    setSaveLoading(true);
    try {
      const platform_configs = buildPlatformConfigs();
      preserveSaveGateRef.current = true;
      const creatorNamePayload = isExistingCreatorRef.current ? undefined : (creatorName.trim() || undefined);
      if (savedCreatorId) {
        const res = await updateCreator(savedCreatorId, {
          name: creatorNamePayload,
          profile_picture_url: creatorAvatarUrl.trim() || undefined,
          platform_configs,
        });
        onSaveConfig({ creatorId: savedCreatorId, name: res.name, handle: res.handle, profile_picture_url: res.profile_picture_url, status: res.status, visual_config: res.visual_config, isExisting: true });
      } else {
        const res = await createCreatorWithConfig({
          name: creatorNamePayload,
          profile_picture_url: creatorAvatarUrl.trim() || undefined,
          platform_configs,
        });
        onSaveConfig({ creatorId: res.id, name: res.name, handle: res.handle, profile_picture_url: res.profile_picture_url, status: res.status, visual_config: res.visual_config, isExisting: res.is_existing });
      }
      setSavedConfigSignature(JSON.stringify(platform_configs));
      setHasSavedInSession(true);
      onSaveSuccess?.();
      return true;
    } catch (err) {
      preserveSaveGateRef.current = false;
      setError(err.message || "Save failed");
      return false;
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

      let urlToValidate = value;

      try {
        const res = coerceSetupValidation(key, urlToValidate, await validatePlatformUrl(key, urlToValidate));
        if (!res.valid) {
          nextStatuses[key] = res.error || "Link invalid";
          invalidMessages.push(platform.label);
          continue;
        }
        const normalized = res.normalized || urlToValidate;
        nextStatuses[key] = READY_TO_SEARCH_MESSAGE;
        if (normalized !== value) {
          nextConfig[key] = { ...(nextConfig[key] || {}), url: normalized };
          changed = true;
        }
      } catch (err) {
        const coerced = coerceSetupValidation(key, urlToValidate, err);
        if (coerced?.valid) {
          nextStatuses[key] = coerced.message || READY_TO_SEARCH_MESSAGE;
        } else {
          nextStatuses[key] = err.message || "Link invalid";
          invalidMessages.push(platform.label);
        }
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
          const maxItems = Math.min(c?.maxItems ?? plat?.default_max_items ?? 10, getPlatformItemLimit(plat));
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
            maxItems: Math.max(1, Number(maxItems) || 10),
          };
        }
        return platformConfigs;
      })(),
    };
  }, [config, getPlatformItemLimit, platforms, selected, selectedPlatformDetails, testStatus]);

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
      if (limitIssue) {
        setError(limitIssue);
        return;
      }
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
          isExisting: true,
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

  useEffect(() => {
    if (!pendingSearchAfterSave || !savedCreatorId || saveLoading || scrapeLoading || loading) return undefined;
    setPendingSearchAfterSave(false);
    const timer = setTimeout(() => {
      handleScrape({ preventDefault: () => {} });
    }, 60);
    return () => clearTimeout(timer);
  }, [pendingSearchAfterSave, savedCreatorId, saveLoading, scrapeLoading, loading]); // eslint-disable-line react-hooks/exhaustive-deps

  const configuredPlatformCount = useMemo(
    () => selectedPlatformDetails.filter((platform) => String(config[platform.key]?.url || "").trim()).length,
    [config, selectedPlatformDetails]
  );
  const readyPlatformCount = useMemo(
    () =>
      selectedPlatformDetails.filter((platform) => {
        const value = String(config[platform.key]?.url || "").trim();
        if (!value) return false;
        return isLinkValidated(platform.key);
      }).length,
    [config, isLinkValidated, selectedPlatformDetails]
  );

  // -------- System-engineering: contextual next-action state machine --------
  // The primary button always reflects the single next required action.
  const nextAction = useMemo(() => {
    if (loading) return { kind: "loading", label: "Working...", reason: null };
    if (saveLoading || pendingSearchAfterSave) return { kind: "loading", label: "Preparing search...", reason: null };
    if (scrapeLoading) return { kind: "loading", label: "Searching\u2026", reason: null };
    if (!creatorName.trim() || nameError) return { kind: "blocked", label: "Add a creator name", reason: "name" };
    if (selected.size === 0) return { kind: "blocked", label: "Pick at least one source", reason: "sources" };
    if (limitIssue) return { kind: "blocked", label: "Reduce scrape limit", reason: "limits" };
    const missingUrl = selectedPlatformDetails.find((p) => !(config[p.key]?.url || "").trim());
    if (missingUrl) return { kind: "blocked", label: `Add a ${missingUrl.label} URL`, reason: missingUrl.key };
    if (!savedCreatorId) return { kind: "save-search", label: "Create & Search", reason: null };
    const dirty = buildConfigSignature() !== savedConfigSignature;
    if (!hasSavedInSession || dirty) return { kind: "save-search", label: "Save & Search", reason: null };
    return { kind: "search", label: "Search now", reason: null };
  }, [loading, saveLoading, pendingSearchAfterSave, scrapeLoading, creatorName, nameError, selected.size, selectedPlatformDetails, config, savedCreatorId, buildConfigSignature, savedConfigSignature, hasSavedInSession, limitIssue]);

  const triggerNextAction = useCallback(async () => {
    if (nextAction.kind === "blocked" || nextAction.kind === "loading") {
      // Fly the user to whatever's blocking them.
      if (nextAction.reason === "name") {
        nameInputRef.current?.focus();
      } else if (nextAction.reason === "limits") {
        setError(limitIssue);
      } else if (typeof nextAction.reason === "string" && platformUrlRefs.current.has(nextAction.reason)) {
        platformUrlRefs.current.get(nextAction.reason)?.focus();
      }
      return;
    }
    try {
      if (nextAction.kind === "save-search") {
        const saved = await handleSave({ preventDefault: () => {} });
        if (!saved) {
          setActionFeedback("error");
          return;
        }
        setActionFeedback("success");
        setPendingSearchAfterSave(true);
      } else {
        await handleScrape({ preventDefault: () => {} });
      }
    } catch {
      setActionFeedback("error");
    }
  }, [nextAction, limitIssue]); // eslint-disable-line react-hooks/exhaustive-deps

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
  }, [triggerNextAction, visiblePlatforms, showSearchConfirm, togglePlatform]);

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
          <p className="subtitle">Creator sources.</p>
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
                  if (isExistingCreatorRef.current) return;
                  setCreatorName(e.target.value);
                  if (!e.target.value.trim()) setNameError("Enter a creator name.");
                  else if (e.target.value.trim().length < 2) setNameError("Name is too short.");
                  else setNameError(null);
                  setNameHint(null);
                  setNameSuggestedAcronym(null);
                }}
                onBlur={() => {
                  if (isExistingCreatorRef.current) return;
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
                disabled={saveLoading || isExistingCreatorRef.current}
              />
              {isExistingCreatorRef.current && (
                <div className="validation-hint">Creator name is locked when editing an existing bot.</div>
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
              <kbd className="kbd-hint">1</kbd>&ndash;<kbd className="kbd-hint">{Math.min(8, visiblePlatforms.length)}</kbd> to toggle
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
                    <span className="platform-check-content">
                      <span className={`badge badge-${p.icon}`}>{p.label}</span>
                      {!implemented && <span className="coming-soon">Coming soon</span>}
                    </span>
                  </label>
                );
              })}
            </div>

            <div className={`scrape-limit-strip ${limitIssue ? "warning" : ""}`} aria-live="polite">
              <span>{scrapeLimits.plan_label}</span>
              <strong>{formatCompactNumber(selectedRequestedItems)} selected</strong>
              <span>{formatCompactNumber(monthlyItemsRemaining)} of {formatCompactNumber(monthlyItemAllowance)} left monthly</span>
              <span>{selectedPlatformDetails.length}/{scrapeLimits.max_platforms_per_search} sources</span>
              {limitIssue && <em>{limitIssue}</em>}
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
              const isVerified = (
                statusText.startsWith("ready to search") ||
                statusText.startsWith("valid public link") ||
                statusText.startsWith("valid format")
              );
              const isWarning = !isVerified && (
                statusText.startsWith("valid format") ||
                statusText.startsWith("valid platform match")
              );
              const statusClass = isVerified ? "ok" : (isWarning ? "warn" : "err");
              const maxItemsLimit = getPlatformItemLimit(platform);
              const maxItemsFallback = Math.min(Number(platform.default_max_items || 10), maxItemsLimit);
              const maxItemsValue = Math.min(
                Math.max(1, Number(cfg.maxItems ?? maxItemsFallback) || maxItemsFallback),
                maxItemsLimit
              );
              const otherRequestedItems = Math.max(0, selectedRequestedItems - maxItemsValue);
              const platformBudgetLimit = Math.max(
                1,
                Math.min(
                  maxItemsLimit,
                  Math.max(1, itemBudgetForSearch - otherRequestedItems)
                )
              );
              const sliderValue = Math.min(maxItemsValue, platformBudgetLimit);
              const remainingAfterPlatform = Math.max(0, itemBudgetForSearch - otherRequestedItems - sliderValue);
              const isMaxItemsEditing = Object.prototype.hasOwnProperty.call(maxItemsDrafts, platform.key);
              const maxItemsInputValue = isMaxItemsEditing ? maxItemsDrafts[platform.key] : String(sliderValue);
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
                        {platform.key !== "custom" && status && (
                          <div className="url-status-row">
                            <span className={`test-status ${statusClass}`}>{status}</span>
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
                              <div className="date-picker-shell" ref={openDatePickerFor === platform.key ? datePopoverRef : null}>
                                <button
                                  type="button"
                                  className={`date-picker-trigger ${openDatePickerFor === platform.key ? "open" : ""}`}
                                  onClick={() => setOpenDatePickerFor((current) => current === platform.key ? null : platform.key)}
                                  disabled={saveLoading}
                                >
                                  <span>{formatDateLabel(cfg.timeFilter?.since || "")}</span>
                                  <svg width="16" height="16" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                                    <path d="M6 2.75V5.25M14 2.75V5.25M3.75 7.25H16.25M5.5 4.25H14.5C15.6046 4.25 16.5 5.14543 16.5 6.25V14.5C16.5 15.6046 15.6046 16.5 14.5 16.5H5.5C4.39543 16.5 3.5 15.6046 3.5 14.5V6.25C3.5 5.14543 4.39543 4.25 5.5 4.25Z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                                  </svg>
                                </button>
                                {openDatePickerFor === platform.key && (
                                  <div className="date-picker-popover">
                                    <input
                                      type="date"
                                      className="date-picker-native"
                                      value={cfg.timeFilter?.since || ""}
                                      max={todayIso}
                                      onChange={(event) => {
                                        updatePlatformConfig(platform.key, {
                                          timeFilter: { ...(cfg.timeFilter || {}), since: event.target.value },
                                        });
                                      }}
                                      autoFocus
                                    />
                                    <div className="date-picker-popover-footer">
                                      <button
                                        type="button"
                                        className="date-picker-clear"
                                        onClick={() => {
                                          updatePlatformConfig(platform.key, {
                                            timeFilter: { ...(cfg.timeFilter || {}), since: "" },
                                          });
                                          setOpenDatePickerFor(null);
                                        }}
                                      >
                                        Clear
                                      </button>
                                      <button
                                        type="button"
                                        className="date-picker-today"
                                        onClick={() => {
                                          updatePlatformConfig(platform.key, {
                                            timeFilter: { ...(cfg.timeFilter || {}), since: todayIso },
                                          });
                                          setOpenDatePickerFor(null);
                                        }}
                                      >
                                        Today
                                      </button>
                                    </div>
                                  </div>
                                )}
                              </div>
                            </>
                          )}
                        </div>
                      )}
                      <div className="form-group">
                        <label>Max items</label>
                        <div className={`max-items-slider-card ${saveLoading ? "disabled" : ""}`}>
                          <div className="max-items-slider-head">
                            <div className="max-items-count">
                              <input
                                type="text"
                                className="max-items-value-input"
                                inputMode="numeric"
                                pattern="[0-9]*"
                                min={1}
                                max={platformBudgetLimit}
                                step={1}
                                value={maxItemsInputValue}
                                onFocus={(event) => {
                                  beginMaxItemsEdit(platform.key, sliderValue);
                                  requestAnimationFrame(() => event.target.select());
                                }}
                                onChange={(event) => updateMaxItemsDraft(platform.key, event.target.value)}
                                onBlur={() => commitMaxItemsDraft(platform.key, platformBudgetLimit)}
                                onKeyDown={(event) => {
                                  if (event.key === "Enter") {
                                    event.preventDefault();
                                    event.currentTarget.blur();
                                  }
                                  if (event.key === "Escape") {
                                    clearMaxItemsDraft(platform.key);
                                    event.currentTarget.blur();
                                  }
                                }}
                                disabled={saveLoading || itemBudgetForSearch <= 0}
                                aria-label={`${platform.label} max items exact value`}
                              />
                              <span className="max-items-unit">items</span>
                            </div>
                            <span className="max-items-remaining">
                              {formatCompactNumber(remainingAfterPlatform)} left
                            </span>
                          </div>
                          <input
                            type="range"
                            className="max-items-slider"
                            min={1}
                            max={platformBudgetLimit}
                            step={1}
                            value={sliderValue}
                            onChange={(event) => {
                              clearMaxItemsDraft(platform.key);
                              updatePlatformConfig(platform.key, { maxItems: Number(event.target.value) });
                            }}
                            disabled={saveLoading || itemBudgetForSearch <= 0}
                            aria-label={`${platform.label} max items`}
                          />
                          <div className="max-items-slider-scale" aria-hidden="true">
                            <span>1</span>
                            <span>{formatCompactNumber(platformBudgetLimit)}</span>
                          </div>
                        </div>
                        <div className="limit-helper">
                          Split your monthly scrape items across selected sources. This source can use up to {formatCompactNumber(platformBudgetLimit)} right now.
                        </div>
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
                <span className="search-confirm-eyebrow">Ready</span>
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
                    <span className={`badge badge-${item.key}`}>{item.label}</span>
                    <span className="search-confirm-url">{item.url}</span>
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
                  <span className="primary-action-label">Search</span>
                </button>
              </>
            ) : (
              <button
                type="button"
                className={`primary-action primary-action-${nextAction.kind} ${actionFeedback ? `primary-action-${actionFeedback}` : ""}`}
                onClick={triggerNextAction}
                aria-busy={nextAction.kind === "loading"}
                data-reason={nextAction.kind === "blocked" ? nextAction.label : undefined}
              >
                <span className="primary-action-spinner" aria-hidden="true" />
                <span className="primary-action-label">{nextAction.label}</span>
                {(nextAction.kind === "save-search" || nextAction.kind === "search") && (
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
