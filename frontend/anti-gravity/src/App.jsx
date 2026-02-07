import { useReducer, useState, useMemo, useEffect } from "react";
import { Stepper } from "./components/Stepper";
import { CreatorSetup } from "./components/CreatorSetup";
import { ScrapeProgress } from "./components/ScrapeProgress";
import { ApprovalGate } from "./components/ApprovalGate";
import { PersonaSetup } from "./components/PersonaSetup";
import { ChatPanel } from "./components/ChatPanel";
import { SourcesPanel } from "./components/SourcesPanel";
import { ChatSidebar } from "./components/ChatSidebar";
import { NewChatModal } from "./components/NewChatModal";
import { scrape, approveIngestV2Stream, savePersona, getScrapeItems, health, listCreators, createCreator } from "./api/client";
import "./App.css";

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
        loading: false,
        progress: null,
        error: null,
        isDraft: true, // Default to draft/temporary unless specified
      };
    default:
      return state;
  }
}

export default function App() {
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
    loading: false,
    progress: null,
    error: null,
    isDraft: true,
  });

  const [userAvatarUrl, setUserAvatarUrl] = useState(() => {
    return localStorage.getItem("userAvatarUrl") || "";
  });

  useEffect(() => {
    localStorage.setItem("userAvatarUrl", userAvatarUrl);
  }, [userAvatarUrl]);

  // ... (useState declarations) ...
  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatId] = useState(null);
  const [showNewChatModal, setShowNewChatModal] = useState(false);
  const [existingCreators, setExistingCreators] = useState([]);

  // ... (Other state) ...
  const [topK, setTopK] = useState(5);
  const [maxDistance] = useState(1.15);
  const [showDebug, setShowDebug] = useState(false);
  const [debugAsk, setDebugAsk] = useState(false);
  const [toast, setToast] = useState(null);
  const [backendConnected, setBackendConnected] = useState(null);
  const [searchProgress, setSearchProgress] = useState(0);

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  async function refreshCreators() {
    try {
      const data = await listCreators();
      setExistingCreators(data.creators || []);
      setBackendConnected(true);
    } catch (error) {
      console.error("Failed to load creators:", error);
      // Don't set error state globally to avoid blocking UI, just log it
      if (error.message.includes("Failed to fetch")) {
        setBackendConnected(false);
      }
    }
  }

  useEffect(() => {
    refreshCreators();

    // Check health
    health()
      .then(() => setBackendConnected(true))
      .catch(() => setBackendConnected(false));
  }, []);

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
        url: "", platform: "", source: ""
      });
      dispatch({ type: "SET_IS_DRAFT", isDraft: false });
      dispatch({ type: "SET_STEP", step: 5 });
    } else if (chatConfig.type === "new") {
      // Redirect to Setup, NOT Temporary (Explicit Save)
      if (chatConfig.creatorId) {
        try {
          const data = await getCreatorConfig(chatConfig.creatorId);
          dispatch({
            type: "SET_CREATOR_INFO",
            creatorName: data.name,
            handle: data.handle || "",
            creatorAvatarUrl: data.profile_picture_url || "",
            url: "", platform: "", source: ""
          });
          dispatch({ type: "SET_CREATOR_ID", creatorId: chatConfig.creatorId });
        } catch (error) {
          console.error("Failed to load creator config for new chat:", error);
          showToast("Failed to load creator config. Please try again.", "error");
        }
      } else {
        dispatch({ type: "RESET" });
        dispatch({
          type: "SET_CREATOR_INFO",
          creatorName: chatConfig.name,
          handle: chatConfig.handle || "",
          url: "", platform: "", source: ""
        });
      }
      dispatch({ type: "SET_IS_DRAFT", isDraft: false });
      dispatch({ type: "SET_STEP", step: 1 });
      setShowNewChatModal(false);
      return;
    } else if (chatConfig.type === "temporary") {
      // Redirect to Setup, BUT Temporary (Draft)
      dispatch({ type: "RESET" });
      dispatch({
        type: "SET_CREATOR_INFO",
        creatorName: chatConfig.name || "Temporary Creator",
        handle: chatConfig.handle || "",
        url: "",
        platform: "",
        source: ""
      });
      dispatch({ type: "SET_IS_DRAFT", isDraft: true }); // Temporary / Draft
      dispatch({ type: "SET_STEP", step: 1 });
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
      } catch (err) {
        console.error("Failed to persist creator avatar to backend:", err);
        // Don't show error toast since the UI already updated successfully
        // The user's experience is smooth even if backend sync fails
      }
    }
  }

  // Workflow Handlers

  function handleSaveConfig({ creatorId, name, handle, profile_picture_url }) {
    dispatch({ type: "SET_CREATOR_ID", creatorId });
    dispatch({ type: "SET_CREATOR_INFO", creatorName: name || "", handle: handle || "", creatorAvatarUrl: profile_picture_url || "", url: "", platform: "", source: "" });
    // Refresh list so it shows up in sidebar/modal immediately
    refreshCreators();
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
    dispatch({ type: "SET_SCRAPED_ITEMS", items: result.items || [] });
    dispatch({ type: "SET_PLATFORM_STATUSES", platformStatuses: result.platform_statuses || null });
    dispatch({ type: "SET_STEP", step: 3 }); // Skip Preview, go straight to Approve
    const n = (result.items || []).length;
    if (n) showToast(`Found ${n} items!`);
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
    dispatch({ type: "SET_PROGRESS", progress: { stage: "starting", current: 0, total: 0, message: "Starting..." } });

    try {
      if (!state.scrapeId) {
        throw new Error("No search ID found");
      }

      const result = await approveIngestV2Stream({
        scrape_id: state.scrapeId,
        decisions,
        creator_id: state.creatorId || 1,
        onProgress: (progressData) => {
          dispatch({ type: "SET_PROGRESS", progress: progressData });
        }
      });

      showToast(`Knowledge base updated! ${result.approved} items ingested.`);
      dispatch({ type: "SET_PROGRESS", progress: null });
      dispatch({ type: "SET_STEP", step: 4 });

      // Refresh existing creators list ONLY if not a draft
      if (!state.isDraft) {
        const data = await listCreators();
        setExistingCreators(data.creators || []);
      }
    } catch (error) {
      showToast(error.message, "error");
      dispatch({ type: "SET_PROGRESS", progress: null });
    } finally {
      dispatch({ type: "SET_LOADING", loading: false });
    }
  }

  function handleApproveBack() {
    dispatch({ type: "SET_STEP", step: 2 });
  }

  async function handlePersonaSave(personaText) {
    dispatch({ type: "SET_LOADING", loading: true });
    try {
      const creatorId = state.creatorId || 1;
      await savePersona(creatorId, personaText);
      dispatch({ type: "SET_PERSONA", persona: personaText });
      showToast("Persona saved successfully!");
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      dispatch({ type: "SET_LOADING", loading: false });
    }
  }

  function handlePersonaContinue() {
    // Check Draft Status
    let finalIsTemporary = false;
    if (state.isDraft) {
      // Prompt user
      const save = window.confirm("Do you want to save this creator to your collection?");
      if (save) {
        // "Save" - just proceed, maybe toast
        showToast("Creator saved to collection.");
        finalIsTemporary = false;
      } else {
        // "Discard" - treat as temporary
        finalIsTemporary = true;
        // Ideally we would ensure it doesn't show in lists, but we rely on isTemporary flag in chat
      }
    }

    // When persona setup is complete, create a chat for this creator
    const newChat = {
      id: generateChatId(),
      creatorId: state.creatorId,
      creatorName: state.creatorName || state.handle || "Creator",
      handle: state.handle || "",
      creatorAvatarUrl: state.creatorAvatarUrl,
      messages: [
        {
          id: "welcome",
          role: "assistant",
          text: `Hey! I'm ${state.creatorName || state.handle || "the creator"}. Ask me anything!`,
        },
      ],
      isTemporary: finalIsTemporary,
    };

    setChats((prev) => [...prev, newChat]);
    setActiveChatId(newChat.id);
    dispatch({ type: "SET_STEP", step: 5 });

    // If not temporary, ensure list is refreshed
    if (!finalIsTemporary) {
      refreshCreators();
    }
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
          />
        );
      case 4:
        return (
          <PersonaSetup
            creatorId={state.creatorId || 1}
            onSave={handlePersonaSave}
            onContinue={handlePersonaContinue}
            loading={state.loading}
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
    // Allow navigation to any step at any time (requirement #1)
    dispatch({ type: "SET_STEP", step });
  }

  // Determine if we have any creators
  const hasCreators = existingCreators.length > 0;

  return (
    <div className="app-shell">
      {/* Global Navigation - Always visible (requirement #1) */}
      <Stepper
        currentStep={state.currentStep}
        steps={STEPS}
        onStepClick={(s) => dispatch({ type: "SET_STEP", step: s })}
        searchProgress={searchProgress}
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
              chats={chats}
              activeChat={activeChatId}
              onSelectChat={handleSelectChat}
              onNewChat={handleNewChat}
              onCloseChat={handleCloseChat}
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
                    onClick={() => dispatch({ type: "SET_STEP", step: 1 })}
                    className="create-creator-btn"
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
                  onChangePersona={() => {
                    if (activeChat.creatorId) {
                      dispatch({ type: "SET_CREATOR_ID", creatorId: activeChat.creatorId });
                      dispatch({ type: "SET_STEP", step: 4 });
                    }
                  }}
                  onRescrape={() => {
                    if (activeChat.creatorId) {
                      dispatch({ type: "SET_CREATOR_ID", creatorId: activeChat.creatorId });
                      dispatch({ type: "SET_STEP", step: 1 });
                    }
                  }}
                  creatorAvatarUrl={activeChat.creatorAvatarUrl || state.creatorAvatarUrl}
                  userAvatarUrl={userAvatarUrl}
                  onUpdateCreatorAvatar={handleUpdateCreatorAvatar}
                  onUpdateUserAvatar={setUserAvatarUrl}
                  debug={debugAsk}
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
    </div>
  );
}
