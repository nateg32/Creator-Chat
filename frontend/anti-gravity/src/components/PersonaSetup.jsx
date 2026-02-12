import { useState, useEffect } from "react";
import { getPersona, getQueueItems } from "../api/client";
import "./PersonaSetup.css";

export function PersonaSetup({ creatorId, onSave, onContinue, loading }) {
  const [concise, setConcise] = useState(5);
  const [serious, setSerious] = useState(5);
  const [direct, setDirect] = useState(5);
  const [creativity, setCreativity] = useState(5);
  const [energy, setEnergy] = useState(5);
  const [hasContent, setHasContent] = useState(false);
  const [contentLoading, setContentLoading] = useState(true);
  const [initialPersonaLoaded, setInitialPersonaLoaded] = useState(false);

  useEffect(() => {
    if (creatorId) {
      setContentLoading(true);
      getQueueItems(creatorId)
        .then((data) => {
          const items = data.items || [];
          const hasIngested = items && items.length > 0 && items.some(i =>
            ['ingested', 'approved', 'completed', 'ready'].includes(i.status) ||
            (i.item_status && ['ingested', 'approved', 'completed', 'ready'].includes(i.item_status))
          );
          setHasContent(hasIngested);
        })
        .finally(() => setContentLoading(false));

      getPersona(creatorId)
        .then((data) => {
          if (data && data.persona) {
            parsePersona(data.persona);
            setInitialPersonaLoaded(true);
          }
        })
        .catch(err => console.error("Failed to load persona:", err));
    }
  }, [creatorId]);

  function parsePersona(text) {
    const styleMatch = text.match(/Style:\s*(.*)/i);
    if (styleMatch) {
      const styles = styleMatch[1].toLowerCase();
      setConcise(5); setSerious(5); setDirect(5); setCreativity(5); setEnergy(5);

      if (styles.includes("detailed")) setConcise(2);
      if (styles.includes("concise")) setConcise(8);
      if (styles.includes("playful")) setSerious(2);
      if (styles.includes("serious")) setSerious(8);
      if (styles.includes("gentle")) setDirect(2);
      if (styles.includes("direct")) setDirect(8);
      if (styles.includes("bold")) setCreativity(8);
      if (styles.includes("literal")) setCreativity(2);
      if (styles.includes("hype")) setEnergy(8);
      if (styles.includes("calm")) setEnergy(2);
    }
  }

  function buildPersonaText() {
    const parts = [];
    const styleParts = [];
    if (concise <= 3) styleParts.push("Detailed");
    else if (concise >= 7) styleParts.push("Concise");

    if (serious <= 3) styleParts.push("Playful");
    else if (serious >= 7) styleParts.push("Serious");

    if (direct <= 3) styleParts.push("Gentle");
    else if (direct >= 7) styleParts.push("Direct");

    if (creativity <= 3) styleParts.push("Literal");
    else if (creativity >= 7) styleParts.push("Bold");

    if (energy <= 3) styleParts.push("Calm");
    else if (energy >= 7) styleParts.push("Hype");

    if (styleParts.length > 0) {
      parts.push(`Style: ${styleParts.join(", ")}`);
    } else {
      parts.push("Style: Balanced");
    }

    return parts.join("\n");
  }

  async function handleSave() {
    const personaText = buildPersonaText();
    await onSave(personaText);
  }

  const getWeightClass = (val) => {
    if (val <= 3) return "left";
    if (val >= 7) return "right";
    return "neutral";
  };

  return (
    <div className="persona-setup-card">
      <div className="persona-header">
        <h2>Persona</h2>
        <p className="persona-subtitle">Tune your creator's communication style.</p>
      </div>

      <div className="persona-form">
        <div className="mixing-board">
          {/* Row 1 */}
          <div className="mixing-row">
            <div className={`mixing-label ${getWeightClass(concise)}`}>
              <span className="side-label label-left">Detailed</span>
              <span className="middle-label">Conciseness</span>
              <span className="side-label label-right">Pushy</span>
            </div>
            <input
              type="range"
              min="1"
              max="10"
              value={concise}
              onChange={(e) => setConcise(Number(e.target.value))}
              className="mixing-slider"
              disabled={loading}
            />
          </div>

          {/* Row 2 */}
          <div className="mixing-row">
            <div className={`mixing-label ${getWeightClass(serious)}`}>
              <span className="side-label label-left">Playful</span>
              <span className="middle-label">Vibe</span>
              <span className="side-label label-right">Serious</span>
            </div>
            <input
              type="range"
              min="1"
              max="10"
              value={serious}
              onChange={(e) => setSerious(Number(e.target.value))}
              className="mixing-slider"
              disabled={loading}
            />
          </div>

          {/* Row 3 */}
          <div className="mixing-row">
            <div className={`mixing-label ${getWeightClass(direct)}`}>
              <span className="side-label label-left">Gentle</span>
              <span className="middle-label">Directness</span>
              <span className="side-label label-right">Blunt</span>
            </div>
            <input
              type="range"
              min="1"
              max="10"
              value={direct}
              onChange={(e) => setDirect(Number(e.target.value))}
              className="mixing-slider"
              disabled={loading}
            />
          </div>

          {/* Row 4 */}
          <div className="mixing-row">
            <div className={`mixing-label ${getWeightClass(creativity)}`}>
              <span className="side-label label-left">Literal</span>
              <span className="middle-label">Expression</span>
              <span className="side-label label-right">Bold</span>
            </div>
            <input
              type="range"
              min="1"
              max="10"
              value={creativity}
              onChange={(e) => setCreativity(Number(e.target.value))}
              className="mixing-slider"
              disabled={loading}
            />
          </div>

          {/* Row 5 */}
          <div className="mixing-row">
            <div className={`mixing-label ${getWeightClass(energy)}`}>
              <span className="side-label label-left">Stoic</span>
              <span className="middle-label">Energy</span>
              <span className="side-label label-right">Hype</span>
            </div>
            <input
              type="range"
              min="1"
              max="10"
              value={energy}
              onChange={(e) => setEnergy(Number(e.target.value))}
              className="mixing-slider"
              disabled={loading}
            />
          </div>
        </div>

        <div className="button-group">
          <button
            onClick={handleSave}
            className="secondary-button"
            disabled={loading}
          >
            {loading ? "Saving..." : "Apply Settings"}
          </button>
          <button
            onClick={onContinue}
            className="primary-button"
            disabled={loading}
          >
            Finish & Chat
          </button>
        </div>
      </div>
    </div>
  );
}
