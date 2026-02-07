import { useReducer, useState, useMemo, useEffect } from "react";
import { Stepper } from "./components/Stepper";
import { CreatorSetup } from "./components/CreatorSetup";
import { ScrapePreview } from "./components/ScrapePreview";
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
        loading: false,
        progress: null,
        error: null,
      };
    default:
      return state;
  }
}

export default function App() {
  // Workflow state (for creator setup/ingestion)
  const [state, dispatch] = useReducer(wizardReducer, {
    currentStep: 5, // Default to Chat (requirement #2)
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
    loading: false,
    progress: null,
    error: null,
  });

  // Multi-chat state
  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatId] = useState(null);
  const [showNewChatModal, setShowNewChatModal] = useState(false);
  const [existingCreators, setExistingCreators] = useState([]);

  // Other state
  const [topK, setTopK] = useState(5);
  const [maxDistance] = useState(1.15);
  const [showDebug, setShowDebug] = useState(false);
  const [debugAsk, setDebugAsk] = useState(false);
  const [toast, setToast] = useState(null);
  const [backendConnected, setBackendConnected] = useState(null);

  async function refreshCreators() {
    try {
      const data = await listCreators();
      const creators = data.creators || [];
      setExistingCreators(creators);
      return creators;
    } catch (err) {
      console.error("Failed to load creators:", err);
      return [];
    }
  }

  // Load existing creators on mount
  useEffect(() => {
    health()
      .then(() => {
        setBackendConnected(true);
        if (state.error && state.error.includes("Cannot connect to backend")) {
          dispatch({ type: "SET_ERROR", error: null });
        }
      })
      .catch((err) => {
        setBackendConnected(false);
        console.error("Backend health check failed:", err);
      });

    refreshCreators().then((creators) => {
      // Handle URL params (open in new tab support)
      const params = new URLSearchParams(window.location.search);
      const urlCreatorId = params.get("creator_id");
      if (urlCreatorId) {
        const creator = creators.find(c => String(c.id) === String(urlCreatorId));
        if (creator) {
          if ((creator.item_count || 0) > 0) {
            // Create active chat session from URL
            const newId = generateChatId();
            const newChat = {
              id: newId,
              creatorId: creator.id,
              creatorName: creator.name,
              handle: creator.handle,
              messages: [
                {
                  id: "welcome",
                  role: "assistant",
                  text: `Hey! I'm ${creator.name}. Ask me anything!`,
                },
              ],
              isTemporary: false,
            };
            setChats(prev => [...prev, newChat]);
            setActiveChatId(newId);
            dispatch({ type: "SET_STEP", step: 5 });
          } else {
            // Redirect to setup if empty
            dispatch({ type: "SET_CREATOR_ID", creatorId: creator.id });
            dispatch({
              type: "SET_CREATOR_INFO",
              creatorName: creator.name || "",
              handle: creator.handle || "",
              url: "", platform: "", source: ""
            });
            dispatch({ type: "SET_STEP", step: 1 });
          }
          // Remove query param to avoid re-triggering? optional
          window.history.replaceState({}, document.title, window.location.pathname);
        }
      }
    });

  }, []);

  function showToast(message, type = "success") {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  }

  // ============================================================================
  // Chat Management
  // ============================================================================

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
      // Check for empty creator content
      const creator = existingCreators.find(c => c.id === chatConfig.creatorId);
      if (creator && (creator.item_count || 0) === 0) {
        // Redirect to setup
        handleSaveConfig({
          creatorId: creator.id,
          name: creator.name || "",
          handle: creator.handle || ""
        });
        dispatch({ type: "SET_STEP", step: 1 });
        setShowNewChatModal(false);
        return;
      }
      creatorId = chatConfig.creatorId;
    } else if (chatConfig.type === "new") {
      // Create new creator in DB
      try {
        const result = await createCreator(chatConfig.name, chatConfig.handle || "", []);
        creatorId = result.id;
        creatorName = result.name;
        handle = result.handle || "";
        isTemporary = false;

        // Refresh existing creators list
        const data = await listCreators();
        setExistingCreators(data.creators || []);

        showToast(`Created new creator: ${creatorName}`);
      } catch (error) {
        showToast(`Failed to create creator: ${error.message}`, "error");
        return;
      }
    }

    // Create new chat
    const newChat = {
      id: generateChatId(),
      creatorId: creatorId,
      creatorName: creatorName,
      handle: handle,
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

  // ============================================================================
  // Workflow Handlers (Creator Setup & Ingestion)
  // ============================================================================

  function handleSaveConfig({ creatorId, name, handle }) {
    dispatch({ type: "SET_CREATOR_ID", creatorId });
    dispatch({ type: "SET_CREATOR_INFO", creatorName: name || "", handle: handle || "", url: "", platform: "", source: "" });
  }

  function handleScrapeResult(result) {
    dispatch({ type: "SET_ERROR", error: null });
    dispatch({ type: "SET_SCRAPE_ID", scrapeId: result.scrape_id || result.search_id });
    if (result.creator_id) dispatch({ type: "SET_CREATOR_ID", creatorId: result.creator_id });
    dispatch({ type: "SET_SCRAPED_ITEMS", items: result.items || [] });
    dispatch({ type: "SET_PLATFORM_STATUSES", platformStatuses: result.platform_statuses || null });
    dispatch({ type: "SET_STEP", step: 2 });
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

      // Refresh existing creators list
      const data = await listCreators();
      setExistingCreators(data.creators || []);
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
    // When persona setup is complete, create a chat for this creator
    const newChat = {
      id: generateChatId(),
      creatorId: state.creatorId,
      creatorName: state.creatorName || state.handle || "Creator",
      handle: state.handle || "",
      messages: [
        {
          id: "welcome",
          role: "assistant",
          text: `Hey! I'm ${state.creatorName || state.handle || "the creator"}. Ask me anything!`,
        },
      ],
      isTemporary: false,
    };

    setChats((prev) => [...prev, newChat]);
    setActiveChatId(newChat.id);
    dispatch({ type: "SET_STEP", step: 5 });
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

  function renderWorkflowStep() {
    switch (state.currentStep) {
      case 1:
        return (
          <CreatorSetup
            onSaveConfig={handleSaveConfig}
            onScrape={handleScrapeResult}
            onSaveSuccess={() => showToast("Config saved. You can search now.")}
            loading={state.loading}
            savedCreatorId={state.creatorId}
          />
        );
      case 2:
        return (
          <ScrapePreview
            items={state.scrapedItems}
            platformStatuses={state.platformStatuses}
            onContinue={handleScrapeContinue}
            onBack={handleScrapeBack}
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
        onStepClick={handleStepClick}
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
                  onResetChat={() => {
                    updateChatMessages(activeChat.id, [
                      {
                        id: "reset",
                        role: "assistant",
                        text: "Chat reset. Ask me anything!",
                      },
                    ]);
                  }}
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
