export function SettingsBar({
  topK,
  setTopK,
  showDebug,
  setShowDebug,
}) {
  return (
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
      </div>
    </div>
  );
}
