import { useState, useEffect } from "react";
import "./PersonaSetup.css";

export function PersonaSetup({ creatorId, onSave, onContinue, loading }) {
  const [tone, setTone] = useState("neutral");
  const [concise, setConcise] = useState(5);
  const [serious, setSerious] = useState(5);
  const [direct, setDirect] = useState(5);
  const [alwaysDo, setAlwaysDo] = useState("");
  const [neverDo, setNeverDo] = useState("");
  const [example, setExample] = useState("");

  useEffect(() => {
    // Load existing persona if available
    if (creatorId) {
      // This would fetch from API, but for now we'll start fresh
    }
  }, [creatorId]);

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

  async function handleSave() {
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
          <label htmlFor="always-do">Always do...</label>
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
          <label htmlFor="never-do">Never do...</label>
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

        <div className="button-group">
          <button
            onClick={handleSave}
            className="primary-button"
            disabled={loading}
          >
            {loading ? "Saving..." : "Save persona"}
          </button>
          <button
            onClick={onContinue}
            className="primary-button"
            disabled={loading}
          >
            Open chat
          </button>
        </div>
      </div>
    </div>
  );
}
