import { useReducer, useState, useMemo, useEffect } from "react";
import { Stepper } from "./components/Stepper";
import { CreatorSetup } from "./components/CreatorSetup";
import { ScrapePreview } from "./components/ScrapePreview";
import { ApprovalGate } from "./components/ApprovalGate";
import { PersonaSetup } from "./components/PersonaSetup";
import { ChatPanel } from "./components/ChatPanel";
import { SourcesPanel } from "./components/SourcesPanel";
import { scrape, approveIngestV2, savePersona, getScrapeItems, health } from "./api/client";
import "./App.css";

const STEPS = [
  { label: "Setup", key: "setup" },
  { label: "Search", key: "scrape" },
  { label: "Approve", key: "approve" },
  { label: "Persona", key: "persona" },
  { label: "Chat", key: "chat" },
];

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
    case "ADD_MESSAGE":
      return {
        ...state,
        messages: [...state.messages, action.message],
      };
    case "SET_MESSAGES":
      return { ...state, messages: action.messages };
    case "UPDATE_MESSAGES":
      return { ...state, messages: action.updater(state.messages) };
    case "SET_LOADING":
      return { ...state, loading: action.loading };
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
        messages: [
          {
            id: "welcome",
            role: "assistant",
            text: "Hey! I'm ready to chat. Ask me anything!",
          },
        ],
        loading: false,
        error: null,
      };
    default:
      return state;
  }
}

export default function App() {
  const [state, dispatch] = useReducer(wizardReducer, {
    currentStep: 1,
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
    messages: [
      {
        id: "welcome",
        role: "assistant",
        text: "Hey! I'm ready to chat. Ask me anything!",
      },
    ],
    loading: false,
    error: null,
  });

  const [topK, setTopK] = useState(5);
  const [maxDistance] = useState(1.15);
  const [showDebug, setShowDebug] = useState(false);
  const [debugAsk, setDebugAsk] = useState(false);
  const [toast, setToast] = useState(null);
  const [backendConnected, setBackendConnected] = useState(null);

  // Check backend connection on mount
  useEffect(() => {
    health()
      .then(() => {
        setBackendConnected(true);
        // Clear any connection errors
        if (state.error && state.error.includes("Cannot connect to backend")) {
          dispatch({ type: "SET_ERROR", error: null });
        }
      })
      .catch((err) => {
        setBackendConnected(false);
        console.error("Backend health check failed:", err);
      });
  }, []);

  function showToast(message, type = "success") {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  }

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
    // decisions is array of {item_id, decision}
    dispatch({ type: "SET_LOADING", loading: true });
    try {
      if (!state.scrapeId) {
        throw new Error("No search ID found");
      }

      const result = await approveIngestV2({
        scrape_id: state.scrapeId,
        decisions,
        creator_id: state.creatorId || 1,
      });

      showToast(`Knowledge base updated! ${result.approved} items ingested.`);
      dispatch({ type: "SET_STEP", step: 4 });
    } catch (error) {
      showToast(error.message, "error");
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
    dispatch({ type: "SET_STEP", step: 5 });
  }

  function handleResetChat() {
    dispatch({
      type: "SET_MESSAGES",
      messages: [
        {
          id: "reset",
          role: "assistant",
          text: "Chat reset. Ask me anything!",
        },
      ],
    });
  }

  function handleChangePersona() {
    dispatch({ type: "SET_STEP", step: 4 });
  }

  function handleRescrape() {
    dispatch({ type: "SET_STEP", step: 1 });
    dispatch({ type: "SET_SCRAPED_ITEMS", items: [] });
    dispatch({ type: "SET_DECISIONS", decisions: {} });
  }

  const lastSources = useMemo(() => {
    for (let i = state.messages.length - 1; i >= 0; i--) {
      if (state.messages[i].retrieved) return state.messages[i].retrieved;
    }
    return [];
  }, [state.messages]);

  function renderStep() {
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
      case 5:
        return (
          <div className="chat-layout">
            <ChatPanel
              creatorId={state.creatorId || 1}
              creatorDisplayName={state.creatorName || state.handle || "the creator"}
              topK={topK}
              maxDistance={maxDistance}
              messages={state.messages}
              setMessages={(updater) => {
                if (typeof updater === "function") {
                  dispatch({ type: "UPDATE_MESSAGES", updater });
                } else {
                  dispatch({ type: "SET_MESSAGES", messages: updater });
                }
              }}
              loading={state.loading}
              setLoading={(loading) => dispatch({ type: "SET_LOADING", loading })}
              onResetChat={handleResetChat}
              onChangePersona={handleChangePersona}
              onRescrape={handleRescrape}
              debug={debugAsk}
            />
            {showDebug && <SourcesPanel lastSources={lastSources} />}
          </div>
        );
      default:
        return null;
    }
  }

  return (
    <div className="app">
      <div className="app-header">
        <h1 className="app-title">Creator Bot</h1>
        <p className="app-subtitle">Build AI bots that sound like your favorite creators</p>
      </div>

      <Stepper currentStep={state.currentStep} steps={STEPS} />

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

      {toast && (
        <div className={`toast toast-${toast.type}`}>
          {toast.message}
        </div>
      )}

      <div className={`main-content ${state.currentStep === 5 ? "chat-step" : ""}`}>
        {renderStep()}
      </div>

      {state.currentStep === 5 && (
        <div className="settings-bar">
          <div className="settings-group">
            <label>
              Top K
              <input
                type="number"
                value={topK}
                onChange={(e) => setTopK(Number(e.target.value) || 5)}
                min="1"
                max="20"
              />
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={showDebug}
                onChange={(e) => setShowDebug(e.target.checked)}
              />
              Show sources
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={debugAsk}
                onChange={(e) => setDebugAsk(e.target.checked)}
              />
              Debug /ask
            </label>
          </div>
        </div>
      )}
    </div>
  );
}
