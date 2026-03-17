import { useReducer, useState, useMemo, useEffect, useRef, Component } from "react";
import { Stepper } from "./components/Stepper";
import { CreatorSetup } from "./components/CreatorSetup";
import { ScrapeProgress } from "./components/ScrapeProgress";
import { ApprovalGate } from "./components/ApprovalGate";
import { PersonaSetup } from "./components/PersonaSetup";
import { ChatPanel } from "./components/ChatPanel";
import { SourcesPanel } from "./components/SourcesPanel";
import { ChatSidebar } from "./components/ChatSidebar";
import { NewChatModal } from "./components/NewChatModal";
import { UserSettingsModal } from "./components/UserSettingsModal";
import {
  scrape,
  approveIngestCommit,
  getJobProgress,
  savePersona,
  getScrapeItems,
  health,
  listCreators,
  createCreator,
  getQueueItems,
  updateCreator,
  getUserSettings,
  updateUserSettings,
  createThread,
  listThreads,
  getThreadMessages,
  deleteThread,
  getLastActiveThread,
  getCreatorConfig,
  updateThread,
  deleteCreator,
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
  { label: "Search", key: "scrape" },
  { label: "Approve", key: "approve" },
  { label: "Persona", key: "persona" },
  { label: "Chat", key: "chat" },
];

// Unique ID generator
let chatIdCounter = 0;
const generateChatId = () => `chat_${Date.now()}_${chatIdCounter++}`;

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
  return items.map((item) => normalizeApprovalItem(item));
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

  useEffect(() => {
    getUserSettings()
      .then(settings => {
        setUserSettings(settings);
        if (settings.profile_picture_url) {
          setUserAvatarUrl(settings.profile_picture_url);
        }
      })
      .catch(err => console.error("Error loading user settings:", err));
  }, []);

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

  useEffect(() => {
    localStorage.setItem("userAvatarUrl", userAvatarUrl);
  }, [userAvatarUrl]);

  // ... (useState declarations) ...
  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatId] = useState(null);
  const [showNewChatModal, setShowNewChatModal] = useState(false);
  const [existingCreators, setExistingCreators] = useState([]);
  const [threadsByCreator, setThreadsByCreator] = useState({});
  const [archivedThreadsByCreator, setArchivedThreadsByCreator] = useState({});
  const [activeCreatorId, setActiveCreatorId] = useState(null); // Track active creator for sidebar expansion

  // ... (Other state) ...
  const [topK, setTopK] = useState(5);
  const [maxDistance] = useState(1.15);
  const [showDebug, setShowDebug] = useState(false);
  const [debugAsk, setDebugAsk] = useState(false);
  const [toast, setToast] = useState(null);
  const [backendConnected, setBackendConnected] = useState(null);
  const [searchProgress, setSearchProgress] = useState(0);
  const [workflowOrigin, setWorkflowOrigin] = useState({ fromChat: false, threadId: null, creatorId: null });
  const [workflowHasDetectedChanges, setWorkflowHasDetectedChanges] = useState(false);
  const [workflowRequiresApproval, setWorkflowRequiresApproval] = useState(false);
  const scrapedItemsRef = useRef(state.scrapedItems);

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

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 2000);
  };

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
  }

  async function refreshCreators() {
    try {
      const data = await listCreators();
      setExistingCreators(data.creators || []);
      setBackendConnected(true);

      // Pre-load threads for all creators (lazy load is better at scale but this is fine for now)
      if (data.creators) {
        data.creators.forEach(c => refreshThreads(c.id));
      }
    } catch (error) {
      console.error("Failed to load creators:", error);
      if (error.message.includes("Failed to fetch")) {
        setBackendConnected(false);
      }
    }
  }

  async function refreshThreads(creatorId) {
    if (!creatorId) return;
    try {
      const threads = await listThreads(creatorId);
      setThreadsByCreator(prev => ({ ...prev, [creatorId]: threads }));
    } catch (err) {
      console.error(`Failed to load threads for creator ${creatorId}:`, err);
    }
  }

  async function refreshArchivedThreads(creatorId) {
    if (!creatorId) return;
    try {
      // listThreads(creatorId, true) fetches archived
      const threads = await listThreads(creatorId, true);
      setArchivedThreadsByCreator(prev => ({ ...prev, [creatorId]: threads }));
    } catch (err) {
      console.error(`Failed to load archived threads for creator ${creatorId}:`, err);
    }
  }

  // Load a thread into the 'chats' state (active session cache)
  async function ensureThreadsLoaded(threadId, creatorId) {
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
        visualConfig: creator ? (creator.visual_config || {}) : {},
        searchMode: creator ? (creator.search_mode || "hybrid") : "hybrid",
        messages: messages.map(m => ({
          id: m.id,
          role: m.role,
          text: m.content,
          ts: m.created_at,
          images: m.images, // Include images from backend response
          cards: m.cards    // Include recommendation cards from backend response
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
  }


  async function handleSelectThreadWrapper(threadId, creatorId) {
    setActiveCreatorId(creatorId);
    const chat = await ensureThreadsLoaded(threadId, creatorId);
    if (chat) {
      setActiveChatId(threadId);
      // We might want to refresh messages to be sure? ensureThreadsLoaded fetches them fresh if not in cache.
      // If already in cache, maybe refresh?
      // For now, assume loaded is fine.
    }
  }

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

  async function handleDeleteThread(threadId, creatorId) {
    if (!window.confirm("Permanently delete this conversation? This cannot be undone.")) return;
    try {
      await deleteThread(threadId);
      await refreshThreads(creatorId);
      if (activeChatId === threadId) {
        // Find next thread
        const remaining = (threadsByCreator[creatorId] || []).filter(t => t.id !== threadId);
        if (remaining.length > 0) {
          handleSelectThreadWrapper(remaining[0].id, creatorId);
        } else {
          handleNewThreadWrapper(creatorId);
        }
      }
      showToast("Conversation deleted");
    } catch (err) {
      console.error(err);
      showToast("Failed to delete", "error");
    }
  }

  async function handleArchiveThread(threadId, creatorId) {
    try {
      await updateThread(threadId, { is_archived: true });
      await refreshThreads(creatorId);
      await refreshArchivedThreads(creatorId); // Refresh archive too
      if (activeChatId === threadId) {
        setActiveChatId(null);
        // Maybe select another active thread?
        const remaining = (threadsByCreator[creatorId] || []).filter(t => t.id !== threadId);
        if (remaining.length > 0) handleSelectThreadWrapper(remaining[0].id, creatorId);
        else handleNewThreadWrapper(creatorId);
      }
      showToast("Conversation archived");
    } catch (err) {
      console.error(err);
      showToast("Failed to archive", "error");
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

  async function handleRenameThread(threadId, creatorId, newTitle) {
    try {
      await updateThread(threadId, { title: newTitle });
      // Refresh both lists to be safe, though usually just one
      await refreshThreads(creatorId);
      await refreshArchivedThreads(creatorId);
    } catch (err) {
      console.error(err);
      showToast("Failed to rename", "error");
    }
  }

  useEffect(() => {
    refreshCreators();

    health()
      .then(() => setBackendConnected(true))
      .catch(() => setBackendConnected(false));
  }, []);

  useEffect(() => {
    if ((state.currentStep === 3 || state.currentStep === 4) && state.creatorId) {
      syncWorkflowApprovalStatus(state.creatorId);
    }
  }, [state.currentStep, state.creatorId]);

  // Load existing items if entering Approval step (Step 3) for existing creator
  useEffect(() => {
    if (state.currentStep === 3 && state.creatorId && state.scrapedItems.length === 0) {
      dispatch({ type: "SET_LOADING", loading: true });
      getQueueItems(state.creatorId)
        .then((data) => {
          if (data && data.items) {
            const mappedItems = normalizeApprovalItems(data.items);

            // Use search_id from backend (likely creatorId) as scrapeId to pass validation
            dispatch({ type: "SET_SCRAPE_ID", scrapeId: data.search_id });
            dispatch({ type: "SET_SCRAPED_ITEMS", items: mappedItems });
          }
        })
        .catch(err => {
          console.error("Failed to load existing knowledge base:", err);
          showToast("Failed to load existing knowledge base", "error");
        })
        .finally(() => dispatch({ type: "SET_LOADING", loading: false }));
    }
  }, [state.currentStep, state.creatorId, state.scrapedItems.length]);

  useEffect(() => {
    const pendingStatuses = new Set(["processing", "queued", "pending", "not_started"]);
    if (state.currentStep !== 3 || !state.scrapeId) return undefined;
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

  // Chat Management
  const activeChat = chats.find((c) => c.id === activeChatId);

  function handleNewChat() {
    setShowNewChatModal(true);
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
    const newChat = {
      id: generateChatId(),
      creatorId: creatorId,
      creatorName: creatorName,
      handle: handle,
      creatorAvatarUrl: chatConfig.profile_picture_url || (chatConfig.type === "existing" ? chatConfig.profile_picture_url : ""),
      visualConfig: chatConfig.visual_config || {},
      messages: [
        {
          id: "welcome",
          role: "assistant",
          text: `Hey! I'm ${creatorName}. Ask me anything!`,
        },
      ],
      isTemporary: isTemporary,
    };

    setChats((prev) => [...prev, newChat]);
    setActiveChatId(newChat.id);
    setShowNewChatModal(false);
  }

  function handleSelectChat(chatId) {
    setActiveChatId(chatId);
  }

  function handleCloseChat(chatId) {
    setChats((prev) => {
      const filtered = prev.filter((c) => c.id !== chatId);
      if (activeChatId === chatId && filtered.length > 0) {
        setActiveChatId(filtered[filtered.length - 1].id);
      } else if (filtered.length === 0) {
        setActiveChatId(null);
      }
      return filtered;
    });
  }

  function updateChatMessages(chatId, updater) {
    setChats((prev) =>
      prev.map((chat) => {
        if (chat.id === chatId) {
          const newMessages = typeof updater === "function" ? updater(chat.messages) : updater;
          return { ...chat, messages: newMessages };
        }
        return chat;
      })
    );
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

  function handleSaveConfig({ creatorId, name, handle, profile_picture_url, status, visual_config }) {
    const previousCreator = existingCreators.find((creator) => creator.id === creatorId);
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

    if (previousCreator) {
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
    dispatch({ type: "SET_SCRAPE_ID", scrapeId });
    dispatch({ type: "SET_STEP", step: 2 }); // Move to Search Step UI
  }

  function handleScrapeResult(result) {
    dispatch({ type: "SET_ERROR", error: null });
    // Don't wipe scrapeId if missing in result; it should already be in state
    const newScrapeId = result.scrape_id || result.search_id;
    if (newScrapeId) {
      dispatch({ type: "SET_SCRAPE_ID", scrapeId: newScrapeId });
    }
    if (result.creator_id) dispatch({ type: "SET_CREATOR_ID", creatorId: result.creator_id });
    dispatch({ type: "SET_SCRAPED_ITEMS", items: normalizeApprovalItems(result.items || []) });
    dispatch({ type: "SET_PLATFORM_STATUSES", platformStatuses: result.platform_statuses || null });
    dispatch({ type: "SET_STEP", step: 3 }); // Skip Preview, go straight to Approve
    setWorkflowHasDetectedChanges(Boolean((result.items || []).length));
    setWorkflowRequiresApproval(true);
    const n = (result.items || []).length;
    if (n) showToast(`Found ${n} items. Transcript enrichment will keep running in the background.`);
    else showToast("Search finished. No items found—check platform statuses.", "error");
  }

  function handleScrapeContinue() {
    dispatch({ type: "SET_STEP", step: 3 });
  }

  function handleScrapeBack() {
    dispatch({ type: "SET_STEP", step: 1 });
  }

  async function handleApproveSave(decisions) {
    dispatch({ type: "SET_LOADING", loading: true });
    dispatch({ type: "SET_PROGRESS", progress: { stage: "starting", current: 0, total: 100, message: "Starting..." } });

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
        const pollInterval = setInterval(async () => {
          try {
            const progress = await getJobProgress(jobId);

            // Update UI progress
            dispatch({
              type: "SET_PROGRESS",
              progress: {
                stage: progress.status,
                current: progress.progress_percent,
                total: 100,
                message: progress.message || `Processing... ${progress.progress_percent}%`
              }
            });

            if (progress.status === "completed") {
              clearInterval(pollInterval);
              showToast(`Knowledge base updated! ${res.approved} items queued.`);
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
              clearInterval(pollInterval);
              showToast(progress.error_log || "Ingestion failed", "error");
              dispatch({ type: "SET_PROGRESS", progress: null });
              dispatch({ type: "SET_LOADING", loading: false });
              reject(new Error(progress.error_log));
            }

            // Exponential backoff for polling if it takes a while
            pollCount++;
            if (pollCount > 60) { // 1+ minute
              clearInterval(pollInterval);
              showToast("Job is taking a long time. Check back later.", "error");
              dispatch({ type: "SET_PROGRESS", progress: null });
              dispatch({ type: "SET_LOADING", loading: false });
              resolve(false); // Don't reject, it might still finish in background
            }
          } catch (pollErr) {
            console.error("Polling error:", pollErr);
            // Optionally ignore intermittent network errors during polling
          }
        }, 1500); // Poll every 1.5 seconds
      });

    } catch (error) {
      showToast(error.message, "error");
      dispatch({ type: "SET_PROGRESS", progress: null });
      dispatch({ type: "SET_LOADING", loading: false });
    }
  }

  function handleApproveBack() {
    dispatch({ type: "SET_STEP", step: 2 });
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
      const shouldSaveChanges = window.confirm("Changes were detected for this creator. Save them to the knowledge base before returning to chat?");
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

  function handleResetWizard() {
    dispatch({ type: "RESET" });
  }

  const lastSources = useMemo(() => {
    if (!activeChat) return [];
    for (let i = activeChat.messages.length - 1; i >= 0; i--) {
      if (activeChat.messages[i].retrieved) return activeChat.messages[i].retrieved;
    }
    return [];
  }, [activeChat]);

  function handleResetChat() {
    updateChatMessages(activeChat.id, [
      {
        id: "reset",
        role: "assistant",
        text: "Chat reset. Ask me anything!",
      },
    ]);
  }

  function renderWorkflowStep() {
    switch (state.currentStep) {
      case 1:
        return (
          <CreatorSetup
            onSaveConfig={handleSaveConfig}
            onSearchStart={handleSearchStart}
            onSaveSuccess={() => showToast("Config saved. You can search now.")}
            loading={state.loading}
            savedCreatorId={state.creatorId}
            initialCreatorName={state.creatorName}
            initialHandle={state.handle}
            initialAvatarUrl={state.creatorAvatarUrl}
            userAvatarUrl={userAvatarUrl}
            onUserAvatarChange={setUserAvatarUrl}
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
            onSave={handleApproveSave}
            onBack={handleApproveBack}
            loading={state.loading}
            progress={state.progress}
            forceShowSave={workflowRequiresApproval && state.scrapedItems.length > 0}
          />
        );
      case 4:
        return (
          <PersonaSetup
            creatorId={state.creatorId}
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
    if (step === 5) {
      dispatch({ type: "SET_STEP", step });
      return;
    }

    if (state.currentStep === 2) {
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
  const hasCreators = existingCreators.length > 0;

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
          handleSelectThreadWrapper(threadParam, foundCreatorId).catch(console.error);
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
            handleSelectThreadWrapper(threadId, creatorId).catch(console.error);
          }
        } catch (e) {
          console.error("Failed to restore session", e);
        }
      }
    }
  }, [existingCreators, activeChatId, threadsByCreator]);

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
      refreshThreads(creatorId);
    } catch (err) {
      console.error("Rename failed", err);
      showToast("Failed to rename thread", "error");
    }
  }

  async function handleArchiveThread(threadId, creatorId) {
    if (!window.confirm("Archive this conversation?")) return;
    try {
      await updateThread(threadId, { is_archived: true });
      refreshThreads(creatorId);
      if (activeChatId === threadId) setActiveChatId(null);
      showToast("Conversation archived", "success");
    } catch (err) {
      console.error("Archive failed", err);
      showToast("Failed to archive thread", "error");
    }
  }

  async function handleDeleteThread(threadId, creatorId) {
    if (!window.confirm("Delete this conversation permanently?")) return;
    try {
      await deleteThread(threadId);
      refreshThreads(creatorId);

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
    <div className={`app-shell ${showChatInterface ? "fixed-height" : ""}`}>
      {/* Global Navigation - Always visible (requirement #1) */}
      <Stepper
        currentStep={state.currentStep}
        steps={STEPS}
        onStepClick={handleStepClick}
        searchProgress={searchProgress}
        onUserClick={() => setShowUserSettings(true)}
        userAvatarUrl={userAvatarUrl}
      />

      {toast && (
        <div className={`toast toast-${toast.type}`}>
          {toast.message}
        </div>
      )}

      {backendConnected === false && (
        <div className="error-banner">
          Cannot connect to backend at http://127.0.0.1:8000. Make sure the backend server is running on port 8000.
        </div>
      )}

      {state.error && (
        <div className="error-banner">
          {state.error}
        </div>
      )}

      <div className="main-content-area">
        {showChatInterface ? (
          <div className="chat-fullscreen">
            <ChatSidebar
              creators={existingCreators}
              threadsByCreator={threadsByCreator}
              activeThreadId={activeChatId}
              activeCreatorIdProp={activeCreatorId}
              onSelectThread={handleSelectThreadWrapper}
              onNewThread={handleNewThreadWrapper}
              onToggleSidebar={() => { }}
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
            />
            <div className="chat-main-area">
              {/* Empty state when no creators exist (requirement #3) */}
              {!hasCreators && !activeChat ? (
                <div className="empty-creator-state">
                  <div className="empty-icon">
                    <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="#9aa0a6" strokeWidth="1.5">
                      <path d="M12 4L14.4 9.6L20 12L14.4 14.4L12 20L9.6 14.4L4 12L9.6 9.6L12 4Z" />
                    </svg>
                  </div>
                  <h1 className="empty-title">No creators yet</h1>
                  <p className="empty-subtitle">Create a creator to start chatting</p>
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
              ) : activeChat ? (
                <ChatPanel
                  key={activeChat.id}
                  threadId={activeChat.id}
                  creatorId={activeChat.creatorId || -1}
                  creatorDisplayName={activeChat.creatorName || activeChat.handle || "Creator"}
                  creatorHandle={activeChat.handle}
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
                      // Delay to allow LLM title generation to complete background task
                      setTimeout(() => refreshThreads(activeChat.creatorId), 3500);
                    }
                  }}
                />
              ) : (
                <div className="select-chat-state">
                  <h2>Select a chat</h2>
                  <p>Choose a conversation from the sidebar or start a new one</p>
                  <button onClick={handleNewChat} className="create-creator-btn">
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
      />
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <AppInner />
    </ErrorBoundary>
  );
}
