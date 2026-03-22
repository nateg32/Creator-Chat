import { useEffect, useRef, useState } from "react";
import "./CreatorSettingsModal.css";
import { resizeImage } from "../utils/image";

const COLORS = [
  { label: "Blue", value: "#4285F4" },
  { label: "Teal", value: "#24C1E0" },
  { label: "Green", value: "#34A853" },
  { label: "Purple", value: "#A142F4" },
  { label: "Orange", value: "#FA7B17" },
  { label: "Red", value: "#EA4335" },
  { label: "Neutral", value: "#3C4043" },
];

export function CreatorSettingsModal({
  isOpen,
  onClose,
  creatorName,
  creatorAvatarUrl,
  visualConfig,
  onUpdateVisualConfig,
  onUpdateCreatorAvatar,
  searchMode = "hybrid",
  onUpdateSearchMode,
}) {
  const [localCreatorColor, setLocalCreatorColor] = useState(
    visualConfig?.creatorNameColor || "#4285F4"
  );
  const [localUserColor, setLocalUserColor] = useState(
    visualConfig?.userNameColor || "#3C4043"
  );
  const [localSearchMode, setLocalSearchMode] = useState(searchMode);
  const [localAvatarUrl, setLocalAvatarUrl] = useState(creatorAvatarUrl || "");
  const [saving, setSaving] = useState(false);
  const fileInputRef = useRef(null);

  useEffect(() => {
    if (!isOpen) return;
    setLocalCreatorColor(visualConfig?.creatorNameColor || "#4285F4");
    setLocalUserColor(visualConfig?.userNameColor || "#3C4043");
    setLocalSearchMode(searchMode || "hybrid");
    setLocalAvatarUrl(creatorAvatarUrl || "");
    setSaving(false);
  }, [isOpen, visualConfig, searchMode, creatorAvatarUrl]);

  if (!isOpen) return null;

  const handleColorChange = (type, color) => {
    if (type === "creator") {
      setLocalCreatorColor(color);
      return;
    }

    setLocalUserColor(color);
  };

  const handleSearchModeChange = (mode) => {
    setLocalSearchMode(mode);
  };

  const handleAvatarUpload = async (event) => {
    if (event.target.files && event.target.files[0]) {
      try {
        const base64 = await resizeImage(event.target.files[0]);
        setLocalAvatarUrl(base64);
      } catch (err) {
        console.error("Avatar upload failed:", err);
        alert("Failed to upload image");
      }
      event.target.value = "";
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const nextVisualConfig = {
        creatorNameColor: localCreatorColor,
        userNameColor: localUserColor,
      };

      if (
        nextVisualConfig.creatorNameColor !== (visualConfig?.creatorNameColor || "#4285F4") ||
        nextVisualConfig.userNameColor !== (visualConfig?.userNameColor || "#3C4043")
      ) {
        await onUpdateVisualConfig(nextVisualConfig);
      }

      if ((searchMode || "hybrid") !== localSearchMode && onUpdateSearchMode) {
        await onUpdateSearchMode(localSearchMode);
      }

      if ((creatorAvatarUrl || "") !== localAvatarUrl) {
        await onUpdateCreatorAvatar(localAvatarUrl || "");
      }

      onClose();
    } catch (err) {
      console.error("Failed to save creator settings:", err);
      alert(`Failed to save: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="creator-settings-overlay" onClick={onClose}>
      <div
        className="creator-settings-modal"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="creator-settings-title"
      >
        <div className="creator-settings-header">
          <div>
            <div className="creator-settings-kicker">Personalization</div>
            <h2 id="creator-settings-title">Creator Settings</h2>
            <p>Tune creator appearance and retrieval behavior.</p>
          </div>
          <button type="button" className="creator-settings-close" onClick={onClose} aria-label="Close settings">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div className="creator-settings-scroll">
          <section className="creator-settings-section">
            <div className="creator-settings-section-head">
              <div className="creator-settings-section-kicker">Profile</div>
              <h3>Profile</h3>
            </div>

            <div className="creator-settings-profile-grid">
              <div className="creator-avatar-panel">
                <button
                  type="button"
                  className="creator-avatar-button"
                  onClick={() => fileInputRef.current?.click()}
                  title="Upload creator photo"
                >
                  {localAvatarUrl ? (
                    <img src={localAvatarUrl} alt="Creator" />
                  ) : (
                    <div className="creator-avatar-placeholder">
                      {(creatorName || "C").trim().charAt(0).toUpperCase() || "C"}
                    </div>
                  )}
                </button>

                <input
                  type="file"
                  ref={fileInputRef}
                  style={{ display: "none" }}
                  accept="image/*"
                  onChange={handleAvatarUpload}
                />

                <div className="creator-avatar-actions">
                  <button
                    type="button"
                    className="creator-avatar-link"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    {localAvatarUrl ? "Change photo" : "Add photo"}
                  </button>
                  {localAvatarUrl ? (
                    <button
                      type="button"
                      className="creator-avatar-link creator-avatar-link-danger"
                      onClick={() => setLocalAvatarUrl("")}
                    >
                      Remove
                    </button>
                  ) : (
                    <div className="creator-avatar-note">Optional</div>
                  )}
                </div>
              </div>

              <div className="creator-settings-form">
                <label htmlFor="creator-name-input">Creator</label>
                <input
                  id="creator-name-input"
                  type="text"
                  value={creatorName || ""}
                  readOnly
                />
              </div>
            </div>
          </section>

          <section className="creator-settings-section">
            <div className="creator-settings-section-head">
              <div className="creator-settings-section-kicker">Search</div>
              <h3>Retrieval</h3>
            </div>

            <div className="creator-mode-list">
              <button
                type="button"
                className={`creator-mode-card ${localSearchMode === "ingested_only" ? "selected" : ""}`}
                onClick={() => handleSearchModeChange("ingested_only")}
              >
                <div className="creator-mode-radio">
                  <div className="creator-mode-radio-inner" />
                </div>
                <div className="creator-mode-copy">
                  <span className="creator-mode-title">Ingested only</span>
                  <span className="creator-mode-desc">Docs and transcripts only.</span>
                </div>
              </button>

              <button
                type="button"
                className={`creator-mode-card ${localSearchMode === "hybrid" ? "selected" : ""}`}
                onClick={() => handleSearchModeChange("hybrid")}
              >
                <div className="creator-mode-radio">
                  <div className="creator-mode-radio-inner" />
                </div>
                <div className="creator-mode-copy">
                  <span className="creator-mode-title">Ingested + web</span>
                  <span className="creator-mode-desc">Your content first, web second.</span>
                </div>
              </button>
            </div>
          </section>

          <section className="creator-settings-section">
            <div className="creator-settings-section-head">
              <div className="creator-settings-section-kicker">Appearance</div>
              <h3>Colours</h3>
            </div>

            <div className="creator-color-group">
              <div className="creator-color-label">Creator</div>
              <div className="creator-color-row">
                {COLORS.map((color) => (
                  <button
                    key={`creator-${color.value}`}
                    type="button"
                    className={`creator-color-swatch ${localCreatorColor === color.value ? "selected" : ""}`}
                    style={{ backgroundColor: color.value }}
                    onClick={() => handleColorChange("creator", color.value)}
                    title={color.label}
                  />
                ))}
              </div>
            </div>

            <div className="creator-color-group">
              <div className="creator-color-label">You</div>
              <div className="creator-color-row">
                {COLORS.map((color) => (
                  <button
                    key={`user-${color.value}`}
                    type="button"
                    className={`creator-color-swatch ${localUserColor === color.value ? "selected" : ""}`}
                    style={{ backgroundColor: color.value }}
                    onClick={() => handleColorChange("user", color.value)}
                    title={color.label}
                  />
                ))}
              </div>
            </div>
          </section>
        </div>

        <div className="creator-settings-footer">
          <button type="button" className="secondary-button" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="primary-button" onClick={handleSave} disabled={saving}>
            {saving ? "Saving..." : "Save Settings"}
          </button>
        </div>
      </div>
    </div>
  );
}
