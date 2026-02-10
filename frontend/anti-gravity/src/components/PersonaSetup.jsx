import { useState, useEffect } from "react";
import { getPersona, getQueueItems } from "../api/client";
import "./PersonaSetup.css";

export function PersonaSetup({ creatorId, onSave, onContinue, loading }) {
  const [tone, setTone] = useState("neutral");
  const [concise, setConcise] = useState(5);
  const [serious, setSerious] = useState(5);
  const [direct, setDirect] = useState(5);
  const [alwaysDo, setAlwaysDo] = useState("");
  const [neverDo, setNeverDo] = useState("");
  const [example, setExample] = useState("");
  const [hasContent, setHasContent] = useState(false);
  const [contentLoading, setContentLoading] = useState(true);
  const [initialPersonaLoaded, setInitialPersonaLoaded] = useState(false);

  useEffect(() => {
    if (creatorId) {
      // Check for ingested content
      setContentLoading(true);
      getQueueItems(creatorId)
        .then((data) => {
          const items = data.items || [];
          console.log("[PersonaSetup] Queue items:", items);
          // Check if any items are ingested or approved (broad check)
          const hasIngested = items && items.length > 0 && items.some(i =>
            ['ingested', 'approved', 'completed', 'ready'].includes(i.status) ||
            (i.item_status && ['ingested', 'approved', 'completed', 'ready'].includes(i.item_status))
          );
          setHasContent(hasIngested);
        })
        .catch(err => {
          console.error("Failed to check content:", err);
          setHasContent(false);
        })
        .finally(() => setContentLoading(false));

      // Load existing persona
      getPersona(creatorId)
        .then((data) => {
          if (data && data.persona) {
            parsePersona(data.persona);
            // If we have an existing persona, we treat this as a valid creator
            // even if queue items check fails (e.g. legacy creators)
            setInitialPersonaLoaded(true);
          }
        })
        .catch(err => console.error("Failed to load persona:", err));
    }
  }, [creatorId]);

  function parsePersona(text) {
    // Simple parser for the text format
    // Tone: Neutral
    const toneMatch = text.match(/Tone:\s*(\w+)/i);
    if (toneMatch) setTone(toneMatch[1].toLowerCase());

    // Style: Detailed, Playful
    const styleMatch = text.match(/Style:\s*(.*)/i);
    if (styleMatch) {
      const styles = styleMatch[1].toLowerCase();
      // Reset defaults
      setConcise(5); setSerious(5); setDirect(5);

      if (styles.includes("detailed")) setConcise(2);
      if (styles.includes("concise")) setConcise(8);
      if (styles.includes("playful")) setSerious(2);
      if (styles.includes("serious")) setSerious(8);
      if (styles.includes("gentle")) setDirect(2);
      if (styles.includes("direct")) setDirect(8);
    }

    // Sections
    const alwaysMatch = text.match(/Always do:\s*([\s\S]*?)(?=\nNever do:|\nExample response:|$)/i);
    if (alwaysMatch) setAlwaysDo(alwaysMatch[1].trim());

    const neverMatch = text.match(/Never do:\s*([\s\S]*?)(?=\nExample response:|$)/i);
    if (neverMatch) setNeverDo(neverMatch[1].trim());

    const exampleMatch = text.match(/Example response:\s*([\s\S]*)/i);
    if (exampleMatch) setExample(exampleMatch[1].trim());
  }

  function buildPersonaText() {
    const parts = [];

    // Tone
    parts.push(`Tone: ${tone.charAt(0).toUpperCase() + tone.slice(1)}`);

    // Style sliders
    const styleParts = [];
    if (concise <= 3) styleParts.push("Detailed");
    else if (concise >= 7) styleParts.push("Concise");

    if (serious <= 3) styleParts.push("Playful");
    else if (serious >= 7) styleParts.push("Serious");

    if (direct <= 3) styleParts.push("Gentle");
    else if (direct >= 7) styleParts.push("Direct");

    if (styleParts.length > 0) {
      parts.push(`Style: ${styleParts.join(", ")}`);
    }

    // Do/Don't
    if (alwaysDo.trim()) {
      parts.push(`\nAlways do:\n${alwaysDo.trim()}`);
    }

    if (neverDo.trim()) {
      parts.push(`\nNever do:\n${neverDo.trim()}`);
    }

    // Example
    if (example.trim()) {
      parts.push(`\nExample response:\n${example.trim()}`);
    }

    return parts.join("\n");
  }

  const isValid = () => {
    return (
      alwaysDo.trim().length > 0 &&
      neverDo.trim().length > 0 &&
      (hasContent || initialPersonaLoaded)
    );
  };

  async function handleSave() {
    if (!isValid()) return;
    const personaText = buildPersonaText();
    await onSave(personaText);
  }

  return (
    <div className="persona-setup-card">
      <h2>Persona</h2>
      <p className="subtitle">How should this creator speak?</p>

      <div className="persona-form">
        <div className="form-group">
          <label htmlFor="tone">Tone</label>
          <select
            id="tone"
            value={tone}
            onChange={(e) => setTone(e.target.value)}
            disabled={loading}
          >
            <option value="neutral">Neutral</option>
            <option value="friendly">Friendly</option>
            <option value="hype">Hype</option>
            <option value="formal">Formal</option>
            <option value="casual">Casual</option>
            <option value="analytical">Analytical</option>
            <option value="storyteller">Storyteller</option>
          </select>
        </div>

        <div className="slider-group">
          <div className="form-group">
            <label>
              Concise vs Detailed
              <span className="slider-value">
                {concise <= 3 ? "Detailed" : concise >= 7 ? "Concise" : "Balanced"}
              </span>
            </label>
            <input
              type="range"
              min="1"
              max="10"
              value={concise}
              onChange={(e) => setConcise(Number(e.target.value))}
              className="slider"
              disabled={loading}
            />
          </div>

          <div className="form-group">
            <label>
              Serious vs Playful
              <span className="slider-value">
                {serious <= 3 ? "Playful" : serious >= 7 ? "Serious" : "Balanced"}
              </span>
            </label>
            <input
              type="range"
              min="1"
              max="10"
              value={serious}
              onChange={(e) => setSerious(Number(e.target.value))}
              className="slider"
              disabled={loading}
            />
          </div>

          <div className="form-group">
            <label>
              Direct vs Gentle
              <span className="slider-value">
                {direct <= 3 ? "Gentle" : direct >= 7 ? "Direct" : "Balanced"}
              </span>
            </label>
            <input
              type="range"
              min="1"
              max="10"
              value={direct}
              onChange={(e) => setDirect(Number(e.target.value))}
              className="slider"
              disabled={loading}
            />
          </div>
        </div>

        <div className="form-group">
          <label htmlFor="always-do">Always do... <span style={{ color: 'red' }}>*</span></label>
          <textarea
            id="always-do"
            value={alwaysDo}
            onChange={(e) => setAlwaysDo(e.target.value)}
            placeholder="e.g., Use emojis, Be enthusiastic, Reference specific videos"
            rows={3}
            disabled={loading}
          />
        </div>

        <div className="form-group">
          <label htmlFor="never-do">Never do... <span style={{ color: 'red' }}>*</span></label>
          <textarea
            id="never-do"
            value={neverDo}
            onChange={(e) => setNeverDo(e.target.value)}
            placeholder="e.g., Use formal language, Make political statements, Use jargon"
            rows={3}
            disabled={loading}
          />
        </div>

        <div className="form-group">
          <label htmlFor="example">Example response (optional)</label>
          <textarea
            id="example"
            value={example}
            onChange={(e) => setExample(e.target.value)}
            placeholder="Paste an example of how this creator should respond..."
            rows={4}
            disabled={loading}
          />
        </div>

        {!hasContent && !contentLoading && !initialPersonaLoaded && (
          <div style={{ color: '#d32f2f', fontSize: '14px', textAlign: 'center', marginTop: '10px' }}>
            Missing ingested content. Please approve some items first.
          </div>
        )}

        {!hasContent && !contentLoading && initialPersonaLoaded && (
          <div style={{ color: '#f57f17', fontSize: '13px', textAlign: 'center', marginTop: '10px' }}>
            Warning: No ingested content found. Bot responses may be limited.
          </div>
        )}

        <div className="button-group">
          <button
            onClick={handleSave}
            className="primary-button"
            disabled={loading || !isValid()}
            title={!isValid() ? "Fill out mandatory fields and ensure content is ingested" : ""}
          >
            {loading ? "Saving..." : "Save persona"}
          </button>
          <button
            onClick={onContinue}
            className="primary-button"
            disabled={loading || !isValid()}
            title={!isValid() ? "Fill out mandatory fields and ensure content is ingested" : ""}
          >
            Open chat
          </button>
        </div>
      </div>
    </div>
  );
}
