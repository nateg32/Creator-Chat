import { useReducer, useState, useMemo, useEffect, useRef, Component } from "react";
import { useCallback } from "react";
import { Stepper } from "./components/Stepper";
import { WorkflowNav } from "./components/WorkflowNav";
import { WorkflowStaleBanner } from "./components/WorkflowStaleBanner";
import { WorkflowGuide } from "./components/WorkflowGuide";
import { CreatorSetup } from "./components/CreatorSetup";
import { ScrapeProgress } from "./components/ScrapeProgress";
import { ApprovalGate } from "./components/ApprovalGate";
import { PersonaSetup } from "./components/PersonaSetup";
import { ChatPanel } from "./components/ChatPanel";
import { SourcesPanel } from "./components/SourcesPanel";
import { ChatSidebar } from "./components/ChatSidebar";
import { NewChatModal } from "./components/NewChatModal";
import { UserSettingsModal } from "./components/UserSettingsModal";
import { Login } from "./components/Login";
import { useFeedback } from "./components/feedback/useFeedback";
import { useWorkflow } from "./hooks/useWorkflow";
import { buildCreatorStarterMessage } from "./utils/creatorWelcome";
import {
  approveIngestCommit,
  getJobProgress,
  getSearchProgress,
  savePersona,
  getScrapeItems,
  health,
  listCreators,
  getQueueItems,
  updateCreator,
  getUserSettings,
  updateUserSettings,
  getSession,
  createThread,
  listThreads,
  getThreadMessages,
  deleteThread,
  getLastActiveThread,
  getCreatorConfig,
  getCreatorWorkflow,
  updateThread,
  deleteCreator,
  logout,
} from "./api/client";
import "./App.css";

// Error Boundary to prevent blank-page crashes
class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  componentDidCatch(error, errorInfo) {
    console.error("[ErrorBoundary] Caught:", error, errorInfo);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 48, textAlign: "center", fontFamily: "Inter, sans-serif" }}>
          <h2 style={{ color: "#c5221f" }}>Something went wrong</h2>
          <p style={{ color: "#5f6368", marginBottom: 24 }}>{this.state.error?.message || "An unexpected error occurred."}</p>
          <button
            onClick={() => window.location.reload()}
            style={{ padding: "10px 24px", background: "#1a73e8", color: "#fff", border: "none", borderRadius: 8, cursor: "pointer", fontSize: 14 }}
          >
            Reload Page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const STEPS = [
  { label: "Setup", key: "setup" },
  { label: "Search", key: "search" },
  { label: "Approve", key: "approve" },
  { label: "Persona", key: "persona" },
  { label: "Chat", key: "chat" },
];

function isSearchRunId(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(value || ""));
}

const MOBILE_CHAT_BREAKPOINT = 760;
const BACKEND_HEALTH_POLL_MS = 60000;
const BACKEND_PROBE_GRACE_MS = 120000;
const BACKEND_PROBE_FAILURES_BEFORE_WARNING = 3;

function isCompactChatViewport() {
  return typeof window !== "undefined" && window.matchMedia(`(max-width: ${MOBILE_CHAT_BREAKPOINT}px)`).matches;
}

// Unique ID generator
let chatIdCounter = 0;
const generateChatId = () => `chat_${Date.now()}_${chatIdCounter++}`;
const TERMINAL_CHAT_MESSAGE_STATUSES = new Set(["done", "error"]);

function isChatMessageInFlight(message) {
  const status = String(message?.status || "").toLowerCase();
  return message?.role === "assistant" && Boolean(status) && !TERMINAL_CHAT_MESSAGE_STATUSES.has(status);
}

function chatHasActiveTurn(chat) {
  return Boolean(chat?.messages?.some(isChatMessageInFlight));
}

function maskFreshSearchWorkflowCounts(workflowState, shouldMask) {
  if (!shouldMask || !workflowState?.state) return workflowState;

  const maskedSteps = (workflowState.steps || []).map((step) => {
    if (step.key === "search") {
      return {
        ...step,
        status: "locked",
        ready: false,
        stale: false,
        count: null,
        blocked_reason: "Search starts from Setup after sources are saved.",
      };
    }
    if (step.key === "approve") {
      return {
        ...step,
        status: "locked",
        ready: false,
        stale: false,
        count: null,
        blocked_reason: "Run a new source search before reviewing content.",
      };
    }
    return step;
  });

  return {
    ...workflowState,
    currentStep: "setup",
    state: {
      ...workflowState.state,
      current_step: "setup",
      latest_search_id: null,
      steps: maskedSteps,
    },
    steps: maskedSteps,
    stepsByKey: Object.fromEntries(maskedSteps.map((step) => [step.key, step])),
  };
}

function normalizeApprovalItem(item) {
  const metadata = typeof item?.metadata === "string"
    ? (() => {
      try {
        return JSON.parse(item.metadata);
      } catch {
        return {};
      }
    })()
    : (item?.metadata || {});
  const safeMetadata = metadata && typeof metadata === "object" ? metadata : {};
  const platform = item?.platform || safeMetadata.platform || item?.source || "unknown";
  const sourceUrl = item?.source_url || item?.url || safeMetadata.source_url || safeMetadata.canonical_url || "";
  const itemId = item?.item_id || item?.queue_id || item?.id || "";
  const normalizedStatus = item?.item_status || item?.status || item?.review_status || "pending";

  return {
    ...item,
    item_id: itemId ? String(itemId) : "",
    queue_id: itemId ? String(itemId) : "",
    title: item?.title || safeMetadata.title || "Untitled content",
    source_url: sourceUrl,
    url: sourceUrl,
    caption: item?.caption || item?.preview || safeMetadata.title || "",
    preview: item?.preview || item?.caption || "",
    status: normalizedStatus,
    item_status: normalizedStatus,
    transcript_status: item?.transcript_status || safeMetadata.transcript_status || "missing",
    platform,
    creator_handle: item?.creator_handle || safeMetadata.creator_handle || safeMetadata.channelName || safeMetadata.authorMeta?.name || "",
    metadata: { ...safeMetadata, platform },
  };
}

function normalizeApprovalItems(items) {
  if (!Array.isArray(items)) return [];
  return items
    .map((item) => normalizeApprovalItem(item))
    .filter((item) => !item.hidden_from_review);
}

function buildApprovalItemsSignature(items) {
  return JSON.stringify(
    (items || []).map((item) => ([
      item.item_id || item.queue_id || "",
      item.item_status || item.status || "",
      item.transcript_status || "",
      item.title || "",
      item.caption || "",
      item.source_url || item.url || "",
    ]))
  );
}

function AppBootScreen({ heading = "Opening chats", subtitle = "Loading your creators and last conversation." }) {
  return (
    <div className="app-shell app-boot-shell">
      <div className="chat-boot-state app-boot-state" aria-live="polite" aria-busy="true">
        <div className="chat-boot-mark" aria-hidden="true"></div>
        <div>
          <h2>{heading}</h2>
          <p>{subtitle}</p>
        </div>
      </div>
    </div>
  );
}

function wizardReducer(state, action) {
  switch (action.type) {
    case "SET_STEP":
      return { ...state, currentStep: action.step };
    case "SET_CREATOR_INFO":
      return {
        ...state,
        creatorName: action.creatorName,
        creatorUrl: action.url,
        platform: action.platform,
        handle: action.handle,
        source: action.source,
        creatorAvatarUrl: action.creatorAvatarUrl,
        visualConfig: action.visualConfig || {},
      };
    case "SET_CREATOR_ID":
      return {
        ...state,
        creatorId: action.creatorId,
      };
    case "SET_SCRAPE_ID":
      return { ...state, scrapeId: action.scrapeId };
    case "SET_SCRAPED_ITEMS":
      return { ...state, scrapedItems: action.items };
    case "SET_PLATFORM_STATUSES":
      return { ...state, platformStatuses: action.platformStatuses };
    case "SET_DECISIONS":
      return { ...state, decisions: action.decisions };
    case "SET_PERSONA":
      return { ...state, persona: action.persona };
    case "SET_LOADING":
      return { ...state, loading: action.loading };
    case "SET_PROGRESS":
      return { ...state, progress: action.progress };
    case "SET_ERROR":
      return { ...state, error: action.error };
    case "SET_IS_DRAFT":
      return { ...state, isDraft: action.isDraft };
    case "RESET":
      return {
        currentStep: 1,
        creatorName: "",
        creatorUrl: "",
        platform: "",
        handle: "",
        source: "",
        scrapeId: null,
        scrapedItems: [],
        platformStatuses: null,
        decisions: {},
        persona: "",
        creatorAvatarUrl: "",
        visualConfig: {},
        loading: false,
        progress: null,
        error: null,
        isDraft: true, // Default to draft/temporary unless specified
      };
    default:
      return state;
  }
}

function AppInner() {
  // Workflow state (for creator setup/ingestion)
  const [state, dispatch] = useReducer(wizardReducer, {
    currentStep: 5, // Default to Chat
    creatorId: null,
    creatorName: "",
    creatorUrl: "",
    platform: "",
    handle: "",
    source: "",
    scrapeId: null,
    scrapedItems: [],
    platformStatuses: null,
    decisions: {},
    persona: "",
    creatorAvatarUrl: "",
    visualConfig: {},
    loading: false,
    progress: null,
    error: null,
    isDraft: true,
  });

  const [userAvatarUrl, setUserAvatarUrl] = useState(() => {
    return localStorage.getItem("userAvatarUrl") || "";
  });



  const [userSettings, setUserSettings] = useState(null);
  const [showUserSettings, setShowUserSettings] = useState(false);
  const [authStatus, setAuthStatus] = useState("checking");
  const isAuthenticated = authStatus === "authenticated";

  const clearAuthenticatedState = useCallback(() => {
    setAuthStatus("unauthenticated");
    setShowUserSettings(false);
    setUserSettings(null);
    setUserAvatarUrl("");
    setExistingCreators([]);
    setCreatorsLoaded(false);
    setSessionRestorePending(true);
    setThreadsByCreator({});
    setArchivedThreadsByCreator({});
    setChats([]);
    setActiveChatId(null);
  }, []);

  useEffect(() => {
    const handleAuthRequired = () => {
      clearAuthenticatedState();
    };

    window.addEventListener("auth-required", handleAuthRequired);
    return () => window.removeEventListener("auth-required", handleAuthRequired);
  }, [clearAuthenticatedState]);

  useEffect(() => {
    let cancelled = false;

    getSession()
      .then((session) => {
        if (cancelled) return;
        setAuthStatus(session?.valid ? "authenticated" : "unauthenticated");
      })
      .catch((err) => {
        console.error("Error checking session:", err);
        if (!cancelled) {
          setAuthStatus("unauthenticated");
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!isAuthenticated) return;
    getUserSettings()
      .then(settings => {
        setUserSettings(settings);
        if (settings.profile_picture_url) {
          setUserAvatarUrl(settings.profile_picture_url);
        }
      })
      .catch(err => console.error("Error loading user settings:", err));
  }, [isAuthenticated]);

  async function handleUpdateUserSettings(newSettings) {
    try {
      const updated = await updateUserSettings(newSettings);
      setUserSettings(updated);
      const newAvatar = updated.profile_picture_url || "";
      setUserAvatarUrl(newAvatar);
      showToast("User settings saved", "success");
    } catch (err) {
      console.error(err);
      showToast("Failed to save settings: " + err.message, "error");
      throw err;
    }
  }

  async function handleLogout() {
    try {
      await logout();
    } catch (err) {
      console.error("Failed to logout:", err);
      showToast("Failed to log out: " + err.message, "error");
      throw err;
    }

    clearAuthenticatedState();
  }

  useEffect(() => {
    localStorage.setItem("userAvatarUrl", userAvatarUrl);
  }, [userAvatarUrl]);

  // ... (useState declarations) ...
  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatId] = useState(null);
  const [showNewChatModal, setShowNewChatModal] = useState(false);
  const [existingCreators, setExistingCreators] = useState([]);
  const [creatorsLoaded, setCreatorsLoaded] = useState(false);
  const [sessionRestorePending, setSessionRestorePending] = useState(true);
  const [threadsByCreator, setThreadsByCreator] = useState({});
  const [archivedThreadsByCreator, setArchivedThreadsByCreator] = useState({});
  const [activeCreatorId, setActiveCreatorId] = useState(null); // Track active creator for sidebar expansion
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const activeChat = useMemo(() => chats.find((c) => c.id === activeChatId), [chats, activeChatId]);
  const activeChatTurnInFlight = chatHasActiveTurn(activeChat);

  // ... (Other state) ...
  const [topK] = useState(5);
  const [maxDistance] = useState(1.15);
  const [showDebug] = useState(false);
  const [debugAsk] = useState(false);
  const feedback = useFeedback();
  const [backendConnected, setBackendConnected] = useState(null);
  const [searchProgress, setSearchProgress] = useState(0);
  const [workflowOrigin, setWorkflowOrigin] = useState({ fromChat: false, threadId: null, creatorId: null });
  const [workflowHasDetectedChanges, setWorkflowHasDetectedChanges] = useState(false);
  const [workflowRequiresApproval, setWorkflowRequiresApproval] = useState(false);
  const scrapedItemsRef = useRef(state.scrapedItems);
  const completedSearchIdsRef = useRef(new Set());
  const approvalItemsLoadedForSearchRef = useRef("");
  const backendProbeFailuresRef = useRef(0);
  const backendProbeInFlightRef = useRef(false);
  const backendLastSuccessAtRef = useRef(Date.now());
  const chatSwipeRef = useRef(null);
  const workflowCreatorId = state.creatorId && state.creatorId > 0 ? state.creatorId : null;
  const workflowState = useWorkflow(workflowCreatorId, {
    searchId: state.scrapeId,
    pollIntervalMs: state.currentStep === 5 ? 15000 : 3500,
  });
  const currentStepKey = STEPS[state.currentStep - 1]?.key;
  const workflowViewState = useMemo(
    () => maskFreshSearchWorkflowCounts(
      workflowState,
      state.currentStep === 1 && !state.scrapeId && state.scrapedItems.length === 0
    ),
    [state.currentStep, state.scrapeId, state.scrapedItems.length, workflowState]
  );

  const markBackendConnected = useCallback(() => {
    backendProbeFailuresRef.current = 0;
    backendLastSuccessAtRef.current = Date.now();
    setBackendConnected(true);
  }, []);

  const markBackendProbeFailure = useCallback(() => {
    const recentlyHealthy = Date.now() - backendLastSuccessAtRef.current < BACKEND_PROBE_GRACE_MS;
    if (recentlyHealthy) {
      setBackendConnected(null);
      return;
    }
    backendProbeFailuresRef.current += 1;
    setBackendConnected(backendProbeFailuresRef.current >= BACKEND_PROBE_FAILURES_BEFORE_WARNING ? false : null);
  }, []);

  const isBackendConnectionError = useCallback((error) => {
    const message = String(error?.message || "");
    return (
      message.includes("Cannot connect to backend at") ||
      message.includes("Request timeout:") ||
      message.includes("Failed to fetch") ||
      message.includes("NetworkError")
    );
  }, []);

  useEffect(() => {
    function handleBackendStatus(event) {
      if (event?.detail?.status === "reachable") {
        markBackendConnected();
      }
    }

    window.addEventListener("backend-status", handleBackendStatus);
    return () => window.removeEventListener("backend-status", handleBackendStatus);
  }, [markBackendConnected]);

  useEffect(() => {
    scrapedItemsRef.current = state.scrapedItems;
  }, [state.scrapedItems]);

  const workflowSessionActive = useMemo(() => {
    if ([1, 2, 3, 4].includes(state.currentStep)) return true;
    return Boolean(
      state.scrapeId ||
      state.scrapedItems.length ||
      workflowRequiresApproval ||
      workflowHasDetectedChanges ||
      workflowOrigin.creatorId ||
      (state.loading && state.creatorId)
    );
  }, [
    state.currentStep,
    state.scrapeId,
    state.scrapedItems.length,
    state.loading,
    state.creatorId,
    workflowRequiresApproval,
    workflowHasDetectedChanges,
    workflowOrigin.creatorId,
  ]);

  const showToast = useCallback((message, type = "success") => {
    if (type === "error") feedback.toast.error(message);
    else if (type === "info") feedback.toast.info(message);
    else feedback.toast.success(message);
  }, [feedback]);

  const searchTaskRunning = useMemo(() => {
    const workflowStatus = String(workflowState.stepsByKey?.search?.status || "").toLowerCase();
    const scrapeId = state.scrapeId ? String(state.scrapeId) : "";
    const alreadyCompleted = scrapeId && completedSearchIdsRef.current.has(scrapeId);
    const progressActive = Number(searchProgress) > 0 && Number(searchProgress) < 100;
    return Boolean(
      isSearchRunId(scrapeId) &&
      !alreadyCompleted &&
      (workflowStatus === "active" || progressActive || state.currentStep === 2)
    );
  }, [searchProgress, state.currentStep, state.scrapeId, workflowState.stepsByKey]);

  const personaTaskRunning = useMemo(() => {
    const workflowStatus = String(workflowState.stepsByKey?.persona?.status || "").toLowerCase();
    return workflowStatus === "active";
  }, [workflowState.stepsByKey]);

  const chatTurnRunning = useMemo(() => chats.some(chatHasActiveTurn), [chats]);

  const hasActiveBackgroundWork = Boolean(
    chatTurnRunning ||
    state.loading ||
    state.progress ||
    searchTaskRunning ||
    personaTaskRunning
  );

  useEffect(() => {
    if (!hasActiveBackgroundWork) return undefined;
    const handleBeforeUnload = (event) => {
      event.preventDefault();
      event.returnValue = "";
      return "";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [hasActiveBackgroundWork]);

  async function syncWorkflowApprovalStatus(creatorId) {
    if (!creatorId) return null;
    try {
      const data = await getCreatorConfig(creatorId);
      const needsReapproval = Boolean(data?.status?.needs_reapproval);
      setWorkflowHasDetectedChanges(needsReapproval);
      setWorkflowRequiresApproval(needsReapproval);
      return data?.status || null;
    } catch (error) {
      console.error("Failed to sync creator approval status:", error);
      return null;
    }
  }

  function resetWorkflowSession({
    step = 1,
    creatorId = null,
    creatorName = "",
    handle = "",
    creatorAvatarUrl = "",
    visualConfig = {},
    isDraft = false,
    fromChat = false,
    threadId = null,
  } = {}) {
    dispatch({ type: "RESET" });
    dispatch({
      type: "SET_CREATOR_INFO",
      creatorName,
      handle,
      creatorAvatarUrl,
      visualConfig,
      url: "",
      platform: "",
      source: "",
    });
    dispatch({ type: "SET_CREATOR_ID", creatorId });
    dispatch({ type: "SET_IS_DRAFT", isDraft });
    dispatch({ type: "SET_STEP", step });
    setWorkflowOrigin({ fromChat, threadId, creatorId });
    setWorkflowHasDetectedChanges(false);
    setWorkflowRequiresApproval(false);
    setSearchProgress(0);
    approvalItemsLoadedForSearchRef.current = "";
  }

  function clearWorkflowTransientState() {
    dispatch({ type: "SET_SCRAPE_ID", scrapeId: null });
    dispatch({ type: "SET_SCRAPED_ITEMS", items: [] });
    dispatch({ type: "SET_PLATFORM_STATUSES", platformStatuses: null });
    dispatch({ type: "SET_PROGRESS", progress: null });
    dispatch({ type: "SET_LOADING", loading: false });
    setWorkflowOrigin({ fromChat: false, threadId: null, creatorId: null });
    setWorkflowHasDetectedChanges(false);
    setWorkflowRequiresApproval(false);
    setSearchProgress(0);
    approvalItemsLoadedForSearchRef.current = "";
  }

  const refreshThreads = useCallback(async (creatorId) => {
    if (!creatorId) return;
    try {
      const threads = await listThreads(creatorId);
      setThreadsByCreator(prev => ({ ...prev, [creatorId]: threads }));
    } catch (err) {
      console.error(`Failed to load threads for creator ${creatorId}:`, err);
    }
  }, []);

  const refreshArchivedThreads = useCallback(async (creatorId) => {
    if (!creatorId) return;
    try {
      // listThreads(creatorId, true) fetches archived
      const threads = await listThreads(creatorId, true);
      setArchivedThreadsByCreator(prev => ({ ...prev, [creatorId]: threads }));
    } catch (err) {
      console.error(`Failed to load archived threads for creator ${creatorId}:`, err);
    }
  }, []);

  const refreshCreators = useCallback(async () => {
    try {
      const data = await listCreators();
      const creators = data.creators || [];
      const threadEntries = await Promise.all(
        creators.map(async (creator) => {
          try {
            const threads = await listThreads(creator.id);
            return [creator.id, threads || []];
          } catch (err) {
            console.error(`Failed to load threads for creator ${creator.id}:`, err);
            return [creator.id, []];
          }
        })
      );

      setExistingCreators(creators);
      setThreadsByCreator(Object.fromEntries(threadEntries));
      markBackendConnected();
    } catch (error) {
      console.error("Failed to load creators:", error);
      if (isBackendConnectionError(error)) {
        markBackendProbeFailure();
      }
    } finally {
      setCreatorsLoaded(true);
    }
  }, [isBackendConnectionError, markBackendConnected, markBackendProbeFailure]);

  // Load a thread into the 'chats' state (active session cache)
  const ensureThreadsLoaded = useCallback(async (threadId, creatorId) => {
    // Check if already loaded in chats
    const existing = chats.find(c => c.id === threadId);
    if (existing) return existing;

    try {
      const messages = await getThreadMessages(threadId);
      // Construct chat object compatible with ChatPanel
      const creator = existingCreators.find(c => c.id === creatorId);

      // Look in both active and archived lists
      let thread = (threadsByCreator[creatorId] || []).find(t => t.id === threadId);
      if (!thread) {
        thread = (archivedThreadsByCreator[creatorId] || []).find(t => t.id === threadId);
      }

      const newChat = {
        id: threadId,
        creatorId: creatorId,
        creatorName: creator ? (creator.name || creator.handle) : "Unknown",
        handle: creator ? creator.handle : "",
        creatorAvatarUrl: creator ? creator.profile_picture_url : "",
        styleFingerprint: creator ? (creator.style_fingerprint || {}) : {},
        visualConfig: creator ? (creator.visual_config || {}) : {},
        searchMode: creator ? (creator.search_mode || "hybrid") : "hybrid",
        messages: messages.map(m => ({
          id: m.id,
          role: m.role,
          text: m.content,
          ts: m.created_at,
          images: m.images, // Include images from backend response
          cards: m.cards,    // Include recommendation cards from backend response
          citations: m.citations,
        })),
        isTemporary: false
      };

      setChats(prev => [...prev, newChat]);
      return newChat;
    } catch (err) {
      console.error("Failed to load thread messages:", err);
      showToast(`Failed to load chat history: ${err.message}`, "error");
      // Clear invalid session to prevent persistent error on reload
      if (localStorage.getItem("lastActiveThread")?.includes(threadId)) {
        localStorage.removeItem("lastActiveThread");
      }
      return null;
    }
  }, [archivedThreadsByCreator, chats, existingCreators, showToast, threadsByCreator]);


  const handleSelectThreadWrapper = useCallback(async (threadId, creatorId) => {
    setActiveCreatorId(creatorId);
    const chat = await ensureThreadsLoaded(threadId, creatorId);
    if (chat) {
      setActiveChatId(threadId);
      if (isCompactChatViewport()) {
        setSidebarCollapsed(true);
      }
      // We might want to refresh messages to be sure? ensureThreadsLoaded fetches them fresh if not in cache.
      // If already in cache, maybe refresh?
      // For now, assume loaded is fine.
    }
  }, [ensureThreadsLoaded]);

  function updateChatMessages(threadId, updater) {
    setChats(prev => prev.map(chat => {
      if (chat.id !== threadId) return chat;

      const newMessages = typeof updater === 'function' ? updater(chat.messages) : updater;
      return { ...chat, messages: newMessages };
    }));
  }

  async function handleNewThreadWrapper(creatorId) {
    try {
      const thread = await createThread(creatorId);
      await refreshThreads(creatorId);
      // Select it
      handleSelectThreadWrapper(thread.id, creatorId);
      // Auto-expand sidebar group is handled by activeCreatorId
      setActiveCreatorId(creatorId);
    } catch (err) {
      console.error("Failed to create thread:", err);
      showToast("Failed to create new chat", "error");
    }
  }

  async function openChatForCreator({ creatorId, preferredThreadId = null, createFresh = false } = {}) {
    if (!creatorId) {
      dispatch({ type: "SET_STEP", step: 5 });
      return;
    }

    setActiveCreatorId(creatorId);

    const selectThread = async (threadId) => {
      if (!threadId) return false;
      await refreshThreads(creatorId);
      await handleSelectThreadWrapper(threadId, creatorId);
      dispatch({ type: "SET_STEP", step: 5 });
      return true;
    };

    if (preferredThreadId && await selectThread(preferredThreadId)) {
      return;
    }

    if (!createFresh) {
      try {
        const lastActive = await getLastActiveThread(creatorId);
        if (lastActive?.id && await selectThread(lastActive.id)) {
          return;
        }
      } catch (err) {
        console.error("Failed to load last active thread:", err);
      }
    }

    const availableThreads = threadsByCreator[creatorId] || [];
    if (!createFresh && availableThreads.length > 0 && await selectThread(availableThreads[0].id)) {
      return;
    }

    if (!createFresh) {
      dispatch({ type: "SET_STEP", step: 5 });
      return;
    }

    try {
      const thread = await createThread(creatorId);
      await refreshThreads(creatorId);
      await handleSelectThreadWrapper(thread.id, creatorId);
      dispatch({ type: "SET_STEP", step: 5 });
    } catch (err) {
      console.error("Failed to open creator chat:", err);
      if (!createFresh && availableThreads.length > 0 && await selectThread(availableThreads[0].id)) {
        return;
      }
      showToast(err.message || "Failed to open chat", "error");
    }
  }

  function handleResetChat() {
    if (activeCreatorId) {
      handleNewThreadWrapper(activeCreatorId);
    }
  }

  async function handleRestoreThread(threadId, creatorId) {
    try {
      await updateThread(threadId, { is_archived: false });
      await refreshThreads(creatorId);
      await refreshArchivedThreads(creatorId);
      showToast("Conversation restored");
    } catch (err) {
      console.error(err);
      showToast("Failed to restore", "error");
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function probeBackendHealth() {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      if (backendProbeInFlightRef.current) return;
      if (typeof navigator !== "undefined" && navigator.onLine === false) {
        markBackendProbeFailure();
        return;
      }
      backendProbeInFlightRef.current = true;
      try {
        await health();
        if (!cancelled) {
          markBackendConnected();
        }
      } catch (error) {
        if (!cancelled && isBackendConnectionError(error)) {
          markBackendProbeFailure();
        }
      } finally {
        backendProbeInFlightRef.current = false;
      }
    }

    probeBackendHealth();
    const intervalId = window.setInterval(probeBackendHealth, BACKEND_HEALTH_POLL_MS);
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        probeBackendHealth();
      }
    };
    const handleOnline = () => probeBackendHealth();
    const handleOffline = () => markBackendProbeFailure();

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [isBackendConnectionError, markBackendConnected, markBackendProbeFailure]);

  useEffect(() => {
    if (!isAuthenticated) return;
    dispatch({ type: "SET_STEP", step: 5 });
    setCreatorsLoaded(false);
    setSessionRestorePending(true);
    refreshCreators();
  }, [isAuthenticated, refreshCreators]);

  useEffect(() => {
    if ((state.currentStep === 3 || state.currentStep === 4) && state.creatorId) {
      syncWorkflowApprovalStatus(state.creatorId);
    }
  }, [state.currentStep, state.creatorId]);

  // Load review items if entering Approval step (Step 3) for an existing creator.
  // Prefer the latest search run because workflow badges are based on scrape_items
  // review_status. Falling back to queue/docs is only for older creators with no
  // active review run.
  useEffect(() => {
    if (state.currentStep === 3 && state.creatorId) {
      const searchScope = isSearchRunId(state.scrapeId) ? state.scrapeId : null;
      const loadKey = `${state.creatorId}:${searchScope || "latest"}`;
      if (approvalItemsLoadedForSearchRef.current === loadKey) {
        return undefined;
      }

      let cancelled = false;
      dispatch({ type: "SET_LOADING", loading: true });

      const loadApprovalItems = async () => {
        let data = null;
        let workflow = workflowState.state;

        if (!workflow) {
          try {
            workflow = await getCreatorWorkflow(state.creatorId, { searchId: searchScope });
          } catch (error) {
            console.error("Failed to load workflow before approval items:", error);
          }
        }

        const approveStep = (workflow?.steps || []).find((step) => step.key === "approve");
        const approveCount = approveStep?.count || {};
        const latestSearchId = workflow?.latest_search_id || state.scrapeId || "";
        const latestSearchTotal = Number(approveCount.total || 0);

        if (latestSearchId && latestSearchTotal > 0) {
          data = await getScrapeItems(latestSearchId);
          if (!cancelled) {
            dispatch({ type: "SET_SCRAPE_ID", scrapeId: latestSearchId });
          }
        } else {
          data = await getQueueItems(state.creatorId);
          if (!cancelled && isSearchRunId(data?.search_id)) {
            dispatch({ type: "SET_SCRAPE_ID", scrapeId: data.search_id });
          }
        }

        if (cancelled) return;
        if (data && data.items) {
          const mappedItems = normalizeApprovalItems(data.items);
          dispatch({ type: "SET_SCRAPED_ITEMS", items: mappedItems });
          approvalItemsLoadedForSearchRef.current = loadKey;
          if (mappedItems.length === 0 && latestSearchTotal > 0 && Number(approveCount.pending || 0) === 0) {
            setWorkflowRequiresApproval(false);
            dispatch({ type: "SET_STEP", step: 4 });
          }
        }
      };

      loadApprovalItems()
        .catch(err => {
          if (cancelled) return;
          console.error("Failed to load approval items:", err);
          showToast("Failed to load approval items", "error");
        })
        .finally(() => {
          if (!cancelled) dispatch({ type: "SET_LOADING", loading: false });
        });

      return () => {
        cancelled = true;
      };
    }
    return undefined;
  }, [state.currentStep, state.creatorId, state.scrapedItems.length, state.scrapeId, workflowState.state, showToast]);

  useEffect(() => {
    const pendingStatuses = new Set(["processing", "queued", "pending", "not_started"]);
    if (state.currentStep !== 3 || !isSearchRunId(state.scrapeId)) return undefined;
    const hasPendingTranscripts = state.scrapedItems.some((item) => pendingStatuses.has(String(item.transcript_status || "").toLowerCase()));
    const hasSearchRunItems = state.scrapedItems.some((item) => !String(item.item_id || item.queue_id || "").startsWith("doc_"));
    if (!hasPendingTranscripts || !hasSearchRunItems) {
      return undefined;
    }

    let cancelled = false;
    const poll = async () => {
      try {
        const res = await getScrapeItems(state.scrapeId);
        if (cancelled || !res?.items) return;
        const normalizedItems = normalizeApprovalItems(res.items || []);
        const nextSignature = buildApprovalItemsSignature(normalizedItems);
        const currentSignature = buildApprovalItemsSignature(scrapedItemsRef.current || []);
        if (nextSignature !== currentSignature) {
          dispatch({ type: "SET_SCRAPED_ITEMS", items: normalizedItems });
        }
      } catch (err) {
        console.error("Failed to refresh transcript statuses:", err);
      }
    };

    poll();
    const intervalId = setInterval(poll, 2500);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, [state.currentStep, state.scrapeId, state.scrapedItems]);

  function _handleNewChat() {
    setShowNewChatModal(true);
  }

  function handleStartCreatorSetup() {
    setShowNewChatModal(false);
    setActiveChatId(null);
    resetWorkflowSession({ step: 1, isDraft: false });
  }

  async function handleCreateChat(chatConfig) {
    let creatorId = null;
    let creatorName = chatConfig.name;
    let handle = chatConfig.handle || "";
    let isTemporary = chatConfig.type === "temporary";

    if (chatConfig.type === "existing") {
      creatorId = chatConfig.creatorId;
      // ALWAYS go to Chat (Step 5) for existing creators
      dispatch({ type: "SET_CREATOR_ID", creatorId });
      dispatch({
        type: "SET_CREATOR_INFO",
        creatorName: chatConfig.name,
        handle: chatConfig.handle || "",
        creatorAvatarUrl: chatConfig.profile_picture_url || "",
        visualConfig: chatConfig.visual_config || {},
        url: "", platform: "", source: ""
      });
      dispatch({ type: "SET_IS_DRAFT", isDraft: false });
      dispatch({ type: "SET_STEP", step: 5 });
    } else if (chatConfig.type === "new") {
      // Redirect to Setup, NOT Temporary (Explicit Save)
      if (chatConfig.creatorId) {
        try {
          const data = await getCreatorConfig(chatConfig.creatorId);
          resetWorkflowSession({
            step: 1,
            creatorId: chatConfig.creatorId,
            creatorName: data.name,
            handle: data.handle || "",
            creatorAvatarUrl: data.profile_picture_url || "",
            visualConfig: data.visual_config || {},
            isDraft: false,
          });
        } catch (error) {
          console.error("Failed to load creator config for new chat:", error);
          showToast("Failed to load creator config. Please try again.", "error");
        }
      } else {
        resetWorkflowSession({
          step: 1,
          creatorName: chatConfig.name,
          handle: chatConfig.handle || "",
          isDraft: false,
        });
      }
      setShowNewChatModal(false);
      return;
    } else if (chatConfig.type === "temporary") {
      // Redirect to Setup, BUT Temporary (Draft)
      resetWorkflowSession({
        step: 1,
        creatorName: chatConfig.name || "Temporary Creator",
        handle: chatConfig.handle || "",
        isDraft: true,
      });
      setShowNewChatModal(false);
      return;
    }

    // Create new chat (only for existing flow now)
    const creatorStyleFingerprint = creatorId
      ? ((existingCreators.find((creator) => creator.id === creatorId) || {}).style_fingerprint || {})
      : {};
    const newChat = {
      id: generateChatId(),
      creatorId: creatorId,
      creatorName: creatorName,
      handle: handle,
      styleFingerprint: creatorStyleFingerprint,
      creatorAvatarUrl: chatConfig.profile_picture_url || (chatConfig.type === "existing" ? chatConfig.profile_picture_url : ""),
      visualConfig: chatConfig.visual_config || {},
      messages: [
        {
          id: "welcome",
          role: "assistant",
          text: buildCreatorStarterMessage(creatorName, creatorStyleFingerprint),
        },
      ],
      isTemporary: isTemporary,
    };

    setChats((prev) => [...prev, newChat]);
    setActiveChatId(newChat.id);
    setShowNewChatModal(false);
  }

  async function handleUpdateCreatorAvatar(creatorId, newAvatarUrl) {
    // OPTIMISTIC UPDATE: Update UI immediately for instant feedback

    // Update global wizard state if this matches current setup
    if (state.creatorId === creatorId) {
      dispatch({
        type: "SET_CREATOR_INFO",
        creatorName: state.creatorName,
        handle: state.handle,
        creatorAvatarUrl: newAvatarUrl,
        creatorUrl: state.creatorUrl,
        platform: state.platform,
        source: state.source
      });
    }

    // Update all chats belonging to this creator (or current temp chat)
    setChats(prev => prev.map(c => {
      // If it's a real creator, match by creatorId
      if (creatorId && creatorId !== -1 && c.creatorId === creatorId) {
        return { ...c, creatorAvatarUrl: newAvatarUrl };
      }
      // If it's a temporary/current chat, match by activeChatId
      if (c.id === activeChatId) {
        return { ...c, creatorAvatarUrl: newAvatarUrl };
      }
      return c;
    }));

    // Persist to backend if it's a real saved creator (in background)
    if (creatorId && creatorId !== -1) {
      try {
        await updateCreator(creatorId, { profile_picture_url: newAvatarUrl });
        refreshCreators();
        injectSystemNotice(creatorId, "Profile photo updated");
      } catch (err) {
        console.error("Failed to persist creator avatar to backend:", err);
        showToast("Failed to save profile picture: " + err.message, "error");
      }
    }
  }

  // Workflow Handlers

  function injectSystemNotice(creatorId, text) {
    setChats((prev) =>
      prev.map((chat) => {
        if (chat.creatorId === creatorId) {
          const lastMsg = chat.messages[chat.messages.length - 1];
          if (lastMsg && lastMsg.role === "system-notice" && lastMsg.text === text) {
            return chat;
          }
          return {
            ...chat,
            messages: [
              ...chat.messages,
              {
                id: Date.now(),
                role: "system-notice",
                text,
                ts: new Date().toISOString(),
              },
            ],
          };
        }
        return chat;
      })
    );
  }

  function handleSaveConfig({ creatorId, name, handle, profile_picture_url, status, visual_config, isExisting }) {
    const previousCreator = existingCreators.find((creator) => creator.id === creatorId);
    const wasExisting = Boolean(previousCreator || isExisting);
    const displayName = name || handle || "Creator";
    const nextCreator = {
      ...(previousCreator || {}),
      id: creatorId,
      name: displayName,
      handle: handle || previousCreator?.handle || "",
      profile_picture_url: profile_picture_url || previousCreator?.profile_picture_url || "",
      visual_config: visual_config || previousCreator?.visual_config || {},
      search_mode: previousCreator?.search_mode || "hybrid",
    };

    dispatch({ type: "SET_CREATOR_ID", creatorId });
    dispatch({ type: "SET_CREATOR_INFO", creatorName: displayName, handle: handle || "", creatorAvatarUrl: profile_picture_url || "", visualConfig: visual_config || {}, url: "", platform: "", source: "" });

    setExistingCreators((prev) => {
      const exists = prev.some((creator) => creator.id === creatorId);
      if (exists) {
        return prev.map((creator) => (creator.id === creatorId ? { ...creator, ...nextCreator } : creator));
      }
      return [nextCreator, ...prev];
    });

    setChats((prev) => prev.map((chat) => (
      chat.creatorId === creatorId
        ? {
            ...chat,
            creatorName: displayName,
            handle: handle || chat.handle || "",
            creatorAvatarUrl: profile_picture_url || chat.creatorAvatarUrl || "",
            visualConfig: visual_config || chat.visualConfig || {},
          }
        : chat
    )));

    const needsReapproval = Boolean(status?.needs_reapproval ?? true);
    setWorkflowHasDetectedChanges(needsReapproval);
    setWorkflowRequiresApproval(needsReapproval);

    if (wasExisting) {
      injectSystemNotice(creatorId, `${displayName} has been updated.`);
      showToast(`${displayName} has been updated.`);
    } else {
      showToast(`New creator added: ${displayName}`);
    }

    refreshCreators();
  }

  function handleUseExistingCreator(existingCreatorId) {
    const existingCreator = existingCreators.find((creator) => creator.id === existingCreatorId);
    if (!existingCreator) return;

    resetWorkflowSession({
      step: 1,
      creatorId: existingCreator.id,
      creatorName: existingCreator.name || existingCreator.handle || "",
      handle: existingCreator.handle || "",
      creatorAvatarUrl: existingCreator.profile_picture_url || "",
      visualConfig: existingCreator.visual_config || {},
      isDraft: false,
      fromChat: Boolean(activeChatId),
      threadId: activeChatId,
    });
    showToast(`${existingCreator.name || existingCreator.handle || "Creator"} already exists. Switched to edit mode.`);
  }

  function handleSearchStart(scrapeId) {
    dispatch({ type: "SET_ERROR", error: null });
    if (scrapeId) completedSearchIdsRef.current.delete(String(scrapeId));
    approvalItemsLoadedForSearchRef.current = "";
    setSearchProgress(2);
    dispatch({ type: "SET_SCRAPE_ID", scrapeId });
    dispatch({ type: "SET_STEP", step: 2 }); // Move to Search Step UI
  }

  const applyScrapeResult = useCallback((result, { advance = true, notify = true } = {}) => {
    dispatch({ type: "SET_ERROR", error: null });
    // Don't wipe scrapeId if missing in result; it should already be in state
    const newScrapeId = result.scrape_id || result.search_id;
    if (newScrapeId) {
      completedSearchIdsRef.current.add(String(newScrapeId));
      dispatch({ type: "SET_SCRAPE_ID", scrapeId: newScrapeId });
    }
    if (result.creator_id) dispatch({ type: "SET_CREATOR_ID", creatorId: result.creator_id });
    const items = normalizeApprovalItems(result.items || []);
    dispatch({ type: "SET_SCRAPED_ITEMS", items });
    dispatch({ type: "SET_PLATFORM_STATUSES", platformStatuses: result.platform_statuses || null });
    setSearchProgress(100);
    if (advance) {
      dispatch({ type: "SET_STEP", step: 3 }); // Skip Preview, go straight to Approve
    }
    setWorkflowHasDetectedChanges(Boolean(items.length));
    setWorkflowRequiresApproval(Boolean(items.length));
    if (notify) {
      if (items.length) {
        showToast(
          advance
            ? `Found ${items.length} items. Transcript enrichment will keep running in the background.`
            : `Search finished with ${items.length} items. Review content when ready.`,
          advance ? "success" : "info"
        );
      } else {
        showToast("Search finished. No items found. Check platform statuses.", "error");
      }
    }
  }, [showToast]);

  function handleScrapeResult(result) {
    applyScrapeResult(result, { advance: true, notify: true });
  }

  useEffect(() => {
    const scrapeId = state.scrapeId ? String(state.scrapeId) : "";
    if (!isAuthenticated || !isSearchRunId(scrapeId) || state.currentStep === 2 || completedSearchIdsRef.current.has(scrapeId)) {
      return undefined;
    }

    let cancelled = false;
    let timeoutId = null;
    let pollCount = 0;

    const pollSearch = async () => {
      try {
        const progress = await getSearchProgress(scrapeId);
        if (cancelled) return;

        const backendPercent = Number(progress.percent ?? progress.percentage ?? 0);
        if (Number.isFinite(backendPercent) && backendPercent > 0) {
          setSearchProgress((current) => Math.max(current, Math.min(backendPercent, 99)));
        }

        const rawStage = String(progress.stage || progress.phase || "search").toLowerCase();
        const status = String(progress.status || "running").toLowerCase();
        const isDone = rawStage === "done" || status === "completed" || backendPercent >= 100;
        const isFailed = status === "failed" || status === "error" || Boolean(progress.error);

        if (isFailed) {
          completedSearchIdsRef.current.add(scrapeId);
          const message = progress.error || progress.message || "Search failed";
          dispatch({ type: "SET_ERROR", error: message });
          showToast(message, "error");
          return;
        }

        if (isDone) {
          const itemsResult = await getScrapeItems(scrapeId);
          if (cancelled) return;
          applyScrapeResult({
            ...progress,
            scrape_id: scrapeId,
            search_id: scrapeId,
            items: itemsResult.items || [],
            platform_statuses: itemsResult.platform_statuses || progress.platform_statuses || null,
          }, { advance: false, notify: true });
          return;
        }
      } catch (error) {
        console.error("Background search poll failed:", error);
      }

      if (!cancelled) {
        pollCount += 1;
        timeoutId = window.setTimeout(pollSearch, pollCount > 8 ? 4000 : 1800);
      }
    };

    pollSearch();
    return () => {
      cancelled = true;
      if (timeoutId) window.clearTimeout(timeoutId);
    };
  }, [applyScrapeResult, isAuthenticated, showToast, state.currentStep, state.scrapeId]);

  async function handleApproveSave(decisions) {
    dispatch({ type: "SET_LOADING", loading: true });
    dispatch({
      type: "SET_PROGRESS",
      progress: {
        stage: "starting",
        current: 2,
        total: 100,
        message: "Saving your decisions",
        detail: "Creating a knowledge update job.",
      },
    });

    try {
      if (!state.scrapeId) {
        throw new Error("No search ID found");
      }

      // Step 1: Commit decisions and get a job_id
      const res = await approveIngestCommit({
        search_id: state.scrapeId,
        decisions,
        creator_id: state.creatorId,
      });

      if (!res.job_id) {
        // No job needed (e.g. only denials or deletions)
        showToast("Content decisions saved.");
        dispatch({ type: "SET_PROGRESS", progress: null });
        dispatch({ type: "SET_STEP", step: 4 });
        dispatch({ type: "SET_LOADING", loading: false });
        setWorkflowRequiresApproval(false);
        return true;
      }

      const jobId = res.job_id;

      // Step 2: Poll the job progress
      return new Promise((resolve, reject) => {
        let pollCount = 0;
        let timeoutId = null;
        let longJobNoticeShown = false;
        const startedAt = Date.now();

        const schedulePoll = (delay) => {
          timeoutId = window.setTimeout(pollJob, delay);
        };

        const buildProgressState = (progress) => {
          const rawPercent = Number(progress.progress_percent || 0);
          const status = progress.status || "queued";
          const percent = status === "queued"
            ? Math.max(3, Math.min(rawPercent || 3, 12))
            : Math.max(5, Math.min(rawPercent, 100));
          const queuePosition = Number(progress.queue_position || 0);
          const elapsedSeconds = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
          let detail = progress.detail || "";

          if (!detail) {
            if (status === "queued") {
              detail = queuePosition > 1
                ? `Queued behind ${queuePosition - 1} knowledge update${queuePosition - 1 === 1 ? "" : "s"}.`
                : "Waiting for the knowledge worker to pick it up.";
            } else if (status === "processing" && elapsedSeconds > 75) {
              detail = "Still working through the batch. You can keep this open while it finishes.";
            } else if (status === "processing") {
              detail = "Building the approved content into the creator knowledge base.";
            } else {
              detail = "Finalising the knowledge base update.";
            }
          }

          return {
            stage: status,
            current: percent,
            total: 100,
            message: progress.message || "Updating knowledge base",
            detail,
            jobType: progress.job_type,
            queuePosition,
          };
        };

        const pollJob = async () => {
          try {
            const progress = await getJobProgress(jobId);

            dispatch({
              type: "SET_PROGRESS",
              progress: buildProgressState(progress),
            });

            if (progress.status === "completed") {
              if (timeoutId) window.clearTimeout(timeoutId);
              showToast(`Knowledge base updated with ${res.approved} approved items.`);
              injectSystemNotice(state.creatorId, "Updated creator knowledge");

              // Update local state to reflect the new approved/denied statuses 
              const updatedItems = state.scrapedItems.map(item => {
                const itemKey = item.item_id || item.queue_id;
                const decisionObj = decisions.find(d => String(d.item_id) === String(itemKey));
                if (decisionObj) {
                  const mappedStatus = decisionObj.decision === "approve" ? "approved" :
                    decisionObj.decision === "deny" ? "denied" : "pending";
                  return { ...item, status: mappedStatus, item_status: mappedStatus };
                }
                return item;
              });
              dispatch({ type: "SET_SCRAPED_ITEMS", items: updatedItems });

              dispatch({ type: "SET_PROGRESS", progress: null });
              dispatch({ type: "SET_STEP", step: 4 });
              setWorkflowHasDetectedChanges(true);
              setWorkflowRequiresApproval(false);

              if (!state.isDraft) {
                listCreators().then(data => setExistingCreators(data.creators || []));
              }

              dispatch({ type: "SET_LOADING", loading: false });
              resolve(true);
            } else if (progress.status === "failed" || progress.status === "error") {
              if (timeoutId) window.clearTimeout(timeoutId);
              showToast(progress.error_log || "Ingestion failed", "error");
              dispatch({ type: "SET_PROGRESS", progress: null });
              dispatch({ type: "SET_LOADING", loading: false });
              reject(new Error(progress.error_log));
            } else {
              pollCount += 1;
              const elapsedMs = Date.now() - startedAt;
              if (!longJobNoticeShown && elapsedMs > 90000) {
                longJobNoticeShown = true;
                showToast("Still updating the knowledge base. Larger batches can take a few minutes.");
              }
              const nextDelay = elapsedMs > 120000 ? 4500 : pollCount > 12 ? 3000 : 1500;
              schedulePoll(nextDelay);
            }
          } catch (pollErr) {
            console.error("Polling error:", pollErr);
            pollCount += 1;
            const elapsedMs = Date.now() - startedAt;
            if (pollCount > 4 && elapsedMs > 45000) {
              dispatch({
                type: "SET_PROGRESS",
                progress: {
                  stage: "reconnecting",
                  current: 8,
                  total: 100,
                  message: "Reconnecting to the knowledge job",
                  detail: "The update is still queued; checking again in a moment.",
                },
              });
            }
            schedulePoll(4000);
          }
        };

        schedulePoll(600);
      });

    } catch (error) {
      showToast(error.message, "error");
      dispatch({ type: "SET_PROGRESS", progress: null });
      dispatch({ type: "SET_LOADING", loading: false });
    }
  }

  function handleApproveBack() {
    dispatch({ type: "SET_STEP", step: 1 });
  }

  function buildAutoApprovalDecisions() {
    return state.scrapedItems.map((item) => {
      const itemKey = item.item_id || item.queue_id;
      const currentStatus = (item.item_status || item.status || "pending").toLowerCase();
      let decision = "pending";
      if (["approved", "ingested", "completed", "ready"].includes(currentStatus)) {
        decision = "approve";
      } else if (currentStatus === "denied") {
        decision = "deny";
      }
      return { item_id: itemKey, decision };
    });
  }

  function canAutoConfirmApproval() {
    if (!state.scrapedItems.length) return false;
    return state.scrapedItems.every((item) => {
      const currentStatus = (item.item_status || item.status || "pending").toLowerCase();
      return ["approved", "ingested", "completed", "ready", "denied"].includes(currentStatus);
    });
  }

  async function handlePersonaSave(personaText) {
    dispatch({ type: "SET_LOADING", loading: true });
    try {
      const creatorId = state.creatorId;
      if (!creatorId) throw new Error("No creator selected");
      await savePersona(creatorId, personaText);
      dispatch({ type: "SET_PERSONA", persona: personaText });
      showToast("Persona saved successfully!");
      injectSystemNotice(state.creatorId, "Persona updated");
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      dispatch({ type: "SET_LOADING", loading: false });
    }
  }

  async function handlePersonaContinue({ creatorStatus } = {}) {
    let nextStatus = creatorStatus;
    let requiresSave = Boolean(nextStatus?.needs_reapproval);

    if (requiresSave) {
      const shouldSaveChanges = await feedback.confirm({
        title: "Save changes?",
        message: "Changes were detected for this creator. Save them to the knowledge base before returning to chat?",
        confirmLabel: "Save changes",
        cancelLabel: "Discard",
      });
      if (shouldSaveChanges) {
        if (canAutoConfirmApproval()) {
          const saved = await handleApproveSave(buildAutoApprovalDecisions());
          try {
            const refreshed = await getCreatorConfig(state.creatorId);
            nextStatus = refreshed?.status || nextStatus;
          } catch (error) {
            console.error("Failed to refresh creator status after auto-confirm:", error);
          }
          requiresSave = Boolean(nextStatus?.needs_reapproval);
          if (!saved && requiresSave) {
            dispatch({ type: "SET_STEP", step: 3 });
            return;
          }
        } else {
          dispatch({ type: "SET_STEP", step: 3 });
          return;
        }
      }
    }

    const shouldCreateFreshChat = workflowHasDetectedChanges && !requiresSave;
    await openChatForCreator({
      creatorId: state.creatorId,
      preferredThreadId: workflowOrigin.fromChat && !shouldCreateFreshChat ? workflowOrigin.threadId : null,
      createFresh: shouldCreateFreshChat || !workflowOrigin.fromChat,
    });
    clearWorkflowTransientState();
  }

  const lastSources = useMemo(() => {
    if (!activeChat) return [];
    for (let i = activeChat.messages.length - 1; i >= 0; i--) {
      if (activeChat.messages[i].retrieved) return activeChat.messages[i].retrieved;
    }
    return [];
  }, [activeChat]);

  function renderWorkflowStep() {
    const approveReviewCount = workflowState.stepsByKey?.approve?.count || null;

    switch (state.currentStep) {
      case 1:
        return (
          <CreatorSetup
            onSaveConfig={handleSaveConfig}
            onSearchStart={handleSearchStart}
            onSaveSuccess={() => showToast("Config saved. You can search now.")}
            loading={state.loading || searchTaskRunning}
            savedCreatorId={state.creatorId}
            initialCreatorName={state.creatorName}
            initialAvatarUrl={state.creatorAvatarUrl}
            existingCreators={existingCreators}
            onUseExistingCreator={handleUseExistingCreator}
          />
        );
      case 2:
        return (
          <ScrapeProgress
            scrapeId={state.scrapeId}
            onComplete={handleScrapeResult}
            onProgress={(p) => setSearchProgress(p)}
            onError={(msg) => dispatch({ type: "SET_ERROR", error: msg })}
          />
        );
      case 3:
        return (
          <ApprovalGate
            items={state.scrapedItems}
            platformStatuses={state.platformStatuses}
            onSave={handleApproveSave}
            onBack={handleApproveBack}
            loading={state.loading}
            progress={state.progress}
            reviewCount={approveReviewCount}
            forceShowSave={workflowRequiresApproval && state.scrapedItems.length > 0}
          />
        );
      case 4:
        return (
          <PersonaSetup
            creatorId={state.creatorId}
            creatorName={state.creatorName}
            onSave={handlePersonaSave}
            onContinue={handlePersonaContinue}
            loading={state.loading}
            onGoToApprove={() => dispatch({ type: "SET_STEP", step: 3 })}
          />
        );
      default:
        return null;
    }
  }

  // If we're in chat mode (step 5), show chat interface
  const showChatInterface = state.currentStep === 5;

  // Handler for global navigation step clicks
  function handleStepClick(step) {
    if (step === 2) {
      return;
    }

    if (step === 5) {
      dispatch({ type: "SET_STEP", step });
      return;
    }

    if (state.currentStep === 5) {
      if (workflowSessionActive && state.creatorId) {
        dispatch({ type: "SET_STEP", step });
        return;
      }
      resetWorkflowSession({
        step,
        isDraft: false,
        fromChat: Boolean(activeChatId),
        threadId: activeChatId,
      });
      return;
    }

    dispatch({ type: "SET_STEP", step });
  }

  // Determine if we have any creators
  const hasCreators = creatorsLoaded && existingCreators.length > 0;
  const isChatBooting = showChatInterface && (!creatorsLoaded || sessionRestorePending);
  const appBooting = isAuthenticated && (!creatorsLoaded || sessionRestorePending);
  const shouldShowSidebar = hasCreators || Boolean(activeChatId);

  useEffect(() => {
    if (!shouldShowSidebar) {
      setSidebarCollapsed(false);
    }
  }, [shouldShowSidebar]);

  useEffect(() => {
    if (!showChatInterface || !shouldShowSidebar) return;
    if (activeChatId && isCompactChatViewport()) {
      setSidebarCollapsed(true);
    }
  }, [showChatInterface, shouldShowSidebar, activeChatId]);

  const handleChatTouchStart = useCallback((event) => {
    if (!shouldShowSidebar || !isCompactChatViewport()) return;
    const target = event.target;
    if (target?.closest?.("textarea, input, button, a, [role='button']")) return;

    const touch = event.touches?.[0];
    if (!touch) return;

    chatSwipeRef.current = {
      x: touch.clientX,
      y: touch.clientY,
    };
  }, [shouldShowSidebar]);

  const handleChatTouchEnd = useCallback((event) => {
    const start = chatSwipeRef.current;
    chatSwipeRef.current = null;
    if (!start || !shouldShowSidebar || !isCompactChatViewport()) return;

    const touch = event.changedTouches?.[0];
    if (!touch) return;

    const deltaX = touch.clientX - start.x;
    const deltaY = touch.clientY - start.y;
    const absX = Math.abs(deltaX);
    const absY = Math.abs(deltaY);
    if (absX < 58 || absX < absY * 1.35) return;

    if (deltaX > 0 && (sidebarCollapsed || start.x < 44)) {
      setSidebarCollapsed(false);
    } else if (deltaX < 0 && !sidebarCollapsed) {
      setSidebarCollapsed(true);
    }
  }, [shouldShowSidebar, sidebarCollapsed]);

  async function handleUpdateVisualConfig(creatorId, newConfig) {
    if (!creatorId || creatorId === -1) return;
    dispatch({ type: "SET_LOADING", loading: true });
    try {
      // Find current config to merge
      const currentCreator = existingCreators.find(c => c.id === creatorId);
      const oldConfig = currentCreator?.visual_config || {};
      const mergedConfig = { ...oldConfig, ...newConfig };

      // Update in existing creators list
      setExistingCreators(prev => prev.map(c => c.id === creatorId ? { ...c, visual_config: mergedConfig } : c));

      // Update in active chat if relevant
      setChats(prev => prev.map(c => c.creatorId === creatorId ? { ...c, visualConfig: mergedConfig } : c));

      // Update global state if relevant
      if (state.creatorId === creatorId) {
        dispatch({
          type: "SET_CREATOR_INFO",
          creatorName: state.creatorName,
          url: state.creatorUrl,
          platform: state.platform,
          handle: state.handle,
          source: state.source,
          creatorAvatarUrl: state.creatorAvatarUrl,
          visualConfig: mergedConfig
        });
      }

      await updateCreator(creatorId, { visual_config: mergedConfig });
      showToast("Settings saved", "success");
    } catch (err) {
      console.error("Failed to update visual config:", err);
      showToast("Failed to save settings: " + err.message, "error");
    } finally {
      dispatch({ type: "SET_LOADING", loading: false });
    }
  }

  async function handleUpdateSearchMode(creatorId, mode) {
    if (!creatorId || creatorId === -1) return;
    try {
      // Update in existing creators list
      setExistingCreators(prev => prev.map(c => c.id === creatorId ? { ...c, search_mode: mode } : c));

      // Update in active chats
      setChats(prev => prev.map(c => c.creatorId === creatorId ? { ...c, searchMode: mode } : c));

      await updateCreator(creatorId, { search_mode: mode });
      showToast("Search preference updated", "success");
    } catch (err) {
      console.error("Failed to update search mode:", err);
      showToast("Failed to save search preference: " + err.message, "error");
    }
  }

  // Persistent Session Restoration
  // Persistent Session Restoration & URL Handling
  useEffect(() => {
    if (!isAuthenticated || !creatorsLoaded) return;

    // Only attempt if we have creators loaded and no active chat yet
    if (existingCreators.length > 0 && !activeChatId) {
      const urlParams = new URLSearchParams(window.location.search);
      const threadParam = urlParams.get("thread");

      if (threadParam) {
        // precise handling for deep links
        let foundCreatorId = null;
        // Search through all loaded threads
        for (const [cIdStr, threads] of Object.entries(threadsByCreator)) {
          if (threads.find(t => t.id === threadParam)) {
            foundCreatorId = parseInt(cIdStr);
            break;
          }
        }

        if (foundCreatorId) {
          handleSelectThreadWrapper(threadParam, foundCreatorId)
            .catch(console.error)
            .finally(() => setSessionRestorePending(false));
        } else {
          setSessionRestorePending(false);
        }
        // If URL param exists, do not use localStorage fallback
        return;
      }

      // Fallback to localStorage
      const stored = localStorage.getItem("lastActiveThread");
      if (stored) {
        try {
          const { threadId, creatorId } = JSON.parse(stored);
          // Verify creator still exists
          if (existingCreators.find(c => c.id === creatorId)) {
            handleSelectThreadWrapper(threadId, creatorId)
              .catch(console.error)
              .finally(() => setSessionRestorePending(false));
            return;
          }
        } catch (e) {
          console.error("Failed to restore session", e);
        }
      }
    }
    setSessionRestorePending(false);
  }, [isAuthenticated, creatorsLoaded, existingCreators, activeChatId, threadsByCreator, handleSelectThreadWrapper]);

  // Save Session
  useEffect(() => {
    if (activeChatId) {
      const chat = chats.find(c => c.id === activeChatId);
      if (chat && !chat.isTemporary) {
        localStorage.setItem("lastActiveThread", JSON.stringify({
          threadId: activeChatId,
          creatorId: chat.creatorId
        }));
      }
    }
  }, [activeChatId, chats]);

  async function handleRenameThread(threadId, creatorId, newTitle) {
    try {
      if (!newTitle) return;
      await updateThread(threadId, { title: newTitle });
      await refreshThreads(creatorId);
      await refreshArchivedThreads(creatorId);
    } catch (err) {
      console.error("Rename failed", err);
      showToast("Failed to rename thread", "error");
    }
  }

  async function handleArchiveThread(threadId, creatorId) {
    const ok = await feedback.confirm({
      title: "Archive conversation?",
      message: "You can restore archived conversations later from settings.",
      confirmLabel: "Archive",
    });
    if (!ok) return;
    try {
      await updateThread(threadId, { is_archived: true });
      await refreshThreads(creatorId);
      await refreshArchivedThreads(creatorId);
      if (activeChatId === threadId) setActiveChatId(null);
      showToast("Conversation archived", "success");
    } catch (err) {
      console.error("Archive failed", err);
      showToast("Failed to archive thread", "error");
    }
  }

  async function handleDeleteThread(threadId, creatorId) {
    const ok = await feedback.confirm({
      title: "Delete conversation?",
      message: "This will permanently remove the conversation. This cannot be undone.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteThread(threadId);
      await refreshThreads(creatorId);
      await refreshArchivedThreads(creatorId);

      // Important: Also remove it from the active `chats` tabs array!
      setChats(prev => prev.filter(c => String(c.id) !== String(threadId)));

      if (String(activeChatId) === String(threadId)) {
        setActiveChatId(null);
        localStorage.removeItem("lastActiveThread");
      }
      showToast("Conversation deleted", "success");
    } catch (err) {
      console.error("Delete failed", err);
      showToast("Failed to delete thread", "error");
    }
  }

  async function handleDeleteCreators(creatorIds) {
    if (!creatorIds || creatorIds.length === 0) return;

    // Optimistic Update: Remove from UI immediately
    const deletedSet = new Set(creatorIds);
    setExistingCreators(prev => prev.filter(c => !deletedSet.has(c.id)));

    // Cleanup active chats immediately
    if (activeChatId) {
      const currentChat = chats.find(c => c.id === activeChatId);
      if (currentChat && deletedSet.has(currentChat.creatorId)) {
        setActiveChatId(null);
        localStorage.removeItem("lastActiveThread");
      }
    }
    setChats(prev => prev.filter(c => !deletedSet.has(c.creatorId)));

    dispatch({ type: "SET_LOADING", loading: true });
    try {
      await Promise.all(creatorIds.map(id => deleteCreator(id)));

      // Still refresh to ensure backend sync, but UI is already updated
      refreshCreators();

      showToast(`Deleted ${creatorIds.length} creator(s)`, "success");
    } catch (err) {
      console.error("Failed to delete creators:", err);
      showToast("Failed to delete creators: " + err.message, "error");
      // On error, refresh to restore state if deletion failed
      refreshCreators();
    } finally {
      dispatch({ type: "SET_LOADING", loading: false });
    }
  }

  return (
    authStatus === "checking" ? (
      <AppBootScreen heading="Opening workspace" subtitle="Checking your session." />
    ) : !isAuthenticated ? (
      <Login onLogin={() => setAuthStatus("authenticated")} />
    ) : appBooting ? (
      <AppBootScreen />
    ) : (
    <div className={`app-shell ${showChatInterface ? "fixed-height" : ""}`}>
      {/* Global Navigation - Always visible (requirement #1) */}
      {state.creatorId && state.creatorId > 0 ? (
        <WorkflowNav
          currentStep={state.currentStep}
          steps={STEPS}
          onStepClick={handleStepClick}
          creatorId={state.creatorId}
          searchId={state.scrapeId}
          searchProgress={searchProgress}
          workflowState={workflowViewState}
          onUserClick={() => setShowUserSettings(true)}
          userAvatarUrl={userAvatarUrl}
        />
      ) : (
        <Stepper
          currentStep={state.currentStep}
          steps={STEPS}
          onStepClick={handleStepClick}
          searchProgress={searchProgress}
          onUserClick={() => setShowUserSettings(true)}
          userAvatarUrl={userAvatarUrl}
        />
      )}

      {state.creatorId && state.creatorId > 0 && (
        <WorkflowStaleBanner
          creatorId={state.creatorId}
          searchId={state.scrapeId}
          currentStepKey={currentStepKey}
          workflowState={workflowViewState}
          onJumpTo={(targetKey) => {
            const idx = STEPS.findIndex((s) => s.key === targetKey);
            if (idx >= 0) handleStepClick(idx + 1);
          }}
        />
      )}

      {backendConnected === false && !activeChatTurnInFlight && (
        <div className="error-banner connection-banner">
          Connection looks unstable. Retrying in the background.
        </div>
      )}

      {state.error && (
        <div className="error-banner">
          {state.error}
        </div>
      )}

      <div className="main-content-area">
        {showChatInterface ? (
          <div
            className={`chat-fullscreen ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}
            onTouchStart={handleChatTouchStart}
            onTouchEnd={handleChatTouchEnd}
          >
            {shouldShowSidebar && sidebarCollapsed && (
              <button
                type="button"
                className="sidebar-reopen-button"
                onClick={() => setSidebarCollapsed(false)}
                aria-label="Open chats"
              >
                <span className="sidebar-reopen-icon" aria-hidden="true">
                  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <rect x="3.5" y="4" width="4.5" height="12" rx="1.6" fill="currentColor" opacity="0.9" />
                    <path d="M11 6L15 10L11 14" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </span>
                <span className="sidebar-reopen-label">Chats</span>
              </button>
            )}

            {shouldShowSidebar && !sidebarCollapsed && (
              <button
                type="button"
                className="mobile-sidebar-scrim"
                onClick={() => setSidebarCollapsed(true)}
                aria-label="Close chats"
              />
            )}

            {shouldShowSidebar && (
              <ChatSidebar
                creators={existingCreators}
                threadsByCreator={threadsByCreator}
                activeThreadId={activeChatId}
                activeCreatorIdProp={activeCreatorId}
                onSelectThread={handleSelectThreadWrapper}
                onNewThread={handleNewThreadWrapper}
                onToggleSidebar={setSidebarCollapsed}
                onRenameThread={handleRenameThread}
                onArchiveThread={handleArchiveThread}
                onRestoreThread={handleRestoreThread}
                onDeleteThread={handleDeleteThread}
                archivedThreadsByCreator={archivedThreadsByCreator}
                onLoadArchived={refreshArchivedThreads}
                onNewCreator={() => {
                  resetWorkflowSession({ step: 1, isDraft: false });
                }}
                onDeleteCreators={handleDeleteCreators}
                canCreateCreator={!workflowSessionActive}
                collapsed={sidebarCollapsed}
              />
            )}
            <div className={`chat-main-area ${shouldShowSidebar && sidebarCollapsed ? "chat-main-area-with-sidebar-toggle" : ""}`}>
              {/* Empty state when no creators exist (requirement #3) */}
              {isChatBooting ? (
                <div className="chat-boot-state" aria-live="polite" aria-busy="true">
                  <div className="chat-boot-mark" aria-hidden="true"></div>
                  <div>
                    <h2>Opening chats</h2>
                    <p>Loading your creators and last conversation.</p>
                  </div>
                </div>
              ) : !hasCreators && !activeChat ? (
                <div className="empty-creator-state">
                  <div className="empty-creator-shell">
                    <div className="empty-creator-copy">
                      <div className="empty-kicker">Creator workspace</div>
                      <h1 className="empty-title">Start the first conversation surface</h1>
                      <p className="empty-subtitle">Add one creator, approve the right material, and the blank canvas turns into a live chat room.</p>
                      <div className="empty-creator-actions">
                        <button
                          onClick={() => resetWorkflowSession({ step: 1, isDraft: false })}
                          className="create-creator-btn"
                          disabled={workflowSessionActive}
                        >
                          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                            <path d="M10 4V16M4 10H16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                          </svg>
                          New Creator
                        </button>
                      </div>
                    </div>

                      <div className="empty-creator-interactive" role="img" aria-label="Animated creator chat prompt preview">
                        <div className="empty-orbit-stage">
                          <div className="empty-orbit-ring empty-orbit-ring-outer" aria-hidden="true"></div>
                          <div className="empty-orbit-ring empty-orbit-ring-inner" aria-hidden="true"></div>
                          <div className="empty-orbit-core">
                            <span className="empty-orbit-core-label">Chat opens here</span>
                            <strong>Ready when one creator exists</strong>
                          </div>
                          <div className="empty-floating-prompt empty-floating-prompt-a">Ask how they frame offers</div>
                          <div className="empty-floating-prompt empty-floating-prompt-b">Pull grounded replies from approved content</div>
                          <div className="empty-floating-prompt empty-floating-prompt-c">Turn one handle into a voice you can question</div>
                      </div>
                    </div>
                  </div>
                </div>
              ) : activeChat ? (
                <ChatPanel
                  key={activeChat.id}
                  threadId={activeChat.id}
                  creatorId={activeChat.creatorId || -1}
                  creatorDisplayName={activeChat.creatorName || activeChat.handle || "Creator"}
                  creatorHandle={activeChat.handle}
                  creatorStyleFingerprint={activeChat.styleFingerprint || {}}
                  topK={topK}
                  maxDistance={maxDistance}
                  messages={activeChat.messages}
                  setMessages={(updater) => updateChatMessages(activeChat.id, updater)}
                  loading={false}
                  setLoading={() => { }}
                  onResetChat={handleResetChat}

                  onChangePersona={(switchId) => {
                    if (typeof switchId === "number") {
                      handleNewThreadWrapper(switchId);
                      return;
                    }
                    if (activeChat.creatorId) {
                      resetWorkflowSession({
                        step: 4,
                        creatorId: activeChat.creatorId,
                        creatorName: activeChat.creatorName || activeChat.handle || "",
                        handle: activeChat.handle || "",
                        creatorAvatarUrl: activeChat.creatorAvatarUrl || "",
                        visualConfig: activeChat.visualConfig || {},
                        isDraft: false,
                        fromChat: true,
                        threadId: activeChat.id,
                      });
                    }
                  }}
                  onRescrape={() => {
                    if (activeChat.creatorId) {
                      resetWorkflowSession({
                        step: 1,
                        creatorId: activeChat.creatorId,
                        creatorName: activeChat.creatorName || activeChat.handle || "",
                        handle: activeChat.handle || "",
                        creatorAvatarUrl: activeChat.creatorAvatarUrl || "",
                        visualConfig: activeChat.visualConfig || {},
                        isDraft: false,
                        fromChat: true,
                        threadId: activeChat.id,
                      });
                    }
                  }}
                  onResolveApproval={async () => {
                    if (activeChat.creatorId) {
                      resetWorkflowSession({
                        step: 3,
                        creatorId: activeChat.creatorId,
                        creatorName: activeChat.creatorName || activeChat.handle || "",
                        handle: activeChat.handle || "",
                        creatorAvatarUrl: activeChat.creatorAvatarUrl || "",
                        visualConfig: activeChat.visualConfig || {},
                        isDraft: false,
                        fromChat: true,
                        threadId: activeChat.id,
                      });
                      await syncWorkflowApprovalStatus(activeChat.creatorId);
                    }
                  }}
                  creatorAvatarUrl={activeChat.creatorAvatarUrl || state.creatorAvatarUrl}
                  userAvatarUrl={userAvatarUrl}
                  visualConfig={activeChat.visualConfig || state.visualConfig || {}}
                  searchMode={activeChat.searchMode || "hybrid"}
                  onUpdateCreatorAvatar={handleUpdateCreatorAvatar}
                  onUpdateUserAvatar={setUserAvatarUrl}
                  onUpdateVisualConfig={handleUpdateVisualConfig}
                  onUpdateSearchMode={handleUpdateSearchMode}
                  userName={userSettings?.display_name}
                  debug={debugAsk}
                  onInteraction={() => {
                    if (activeChat?.creatorId) {
                      const creatorId = activeChat.creatorId;
                      // Thread titles are generated after the stream finishes, behind slower save/quality work.
                      [1200, 4500, 10000].forEach((delay) => {
                        setTimeout(() => refreshThreads(creatorId), delay);
                      });
                    }
                  }}
                />
              ) : (
                <div className="select-chat-state">
                  <h2>Select a chat</h2>
                  <p>Choose a conversation from the sidebar or start a new one</p>
                  <button onClick={handleStartCreatorSetup} className="create-creator-btn">
                    <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                      <path d="M10 4V16M4 10H16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                    </svg>
                    New Chat
                  </button>
                </div>
              )}
              {showDebug && <SourcesPanel lastSources={lastSources} />}
            </div>
          </div>
        ) : (
          <div className="workflow-container">
            <WorkflowGuide
              workflowState={workflowViewState}
              currentStepKey={currentStepKey}
            />
            {renderWorkflowStep()}
          </div>
        )}
      </div>

      {showNewChatModal && (
        <NewChatModal
          onClose={() => setShowNewChatModal(false)}
          onCreateChat={handleCreateChat}
          existingCreators={existingCreators}
          onRefreshCreators={refreshCreators}
        />
      )}
      <UserSettingsModal
        isOpen={showUserSettings}
        onClose={() => setShowUserSettings(false)}
        userSettings={userSettings}
        onUpdateUserSettings={handleUpdateUserSettings}
        onLogout={handleLogout}
      />
    </div>
    )
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <AppInner />
    </ErrorBoundary>
  );
}
