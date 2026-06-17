import "./SourcesPanel.css";

export function SourcesPanel({ showDebug, lastSources }) {
  if (!showDebug) return null;

  return (
    <div className="sources-panel">
      <h3>Sources used</h3>
      {!lastSources || lastSources.length === 0 ? (
        <div className="sources-empty">No retrieved chunks returned.</div>
      ) : (
        <div className="sources-list">
          {lastSources.map((s, i) => (
            <div key={i} className="source-item">
              <div className="source-meta">
                <span>
                  Rank <strong>{i + 1}</strong>
                </span>
                <span>
                  Distance <strong>{Number(s.distance).toFixed(3)}</strong>
                </span>
              </div>
              {s.preview && (
                <div className="source-preview">
                  {s.preview}
                  {s.preview.length === 200 && "…"}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
