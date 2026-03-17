import { useEffect, useRef, useState } from "react";
import "./UserSettingsModal.css";
import { resizeImage } from "../utils/image";

const RESPONSE_STYLES = [
  {
    label: "Simple English",
    description: "Clearer, simpler explanations.",
  },
  {
    label: "Concise answers",
    description: "Lead with the point.",
  },
  {
    label: "Step-by-step explanations",
    description: "More structured guidance.",
  },
  {
    label: "Friendly and conversational",
    description: "Warmer, more relaxed tone.",
  },
  {
    label: "Professional and direct",
    description: "Sharper and more formal.",
  },
  {
    label: "Examples-first explanations",
    description: "Start with a concrete example.",
  },
];

const CUSTOM_PREF_LIMIT = 500;

export function UserSettingsModal({
  isOpen,
  onClose,
  userSettings,
  onUpdateUserSettings,
}) {
  const [displayName, setDisplayName] = useState(userSettings?.display_name || "");
  const [avatarUrl, setAvatarUrl] = useState(userSettings?.profile_picture_url || "");
  const [presets, setPresets] = useState(userSettings?.response_preferences?.presets || []);
  const [customPref, setCustomPref] = useState(userSettings?.response_preferences?.custom || "");
  const [saving, setSaving] = useState(false);
  const fileInputRef = useRef(null);

  useEffect(() => {
    if (isOpen && userSettings) {
      setDisplayName(userSettings.display_name || "");
      setAvatarUrl(userSettings.profile_picture_url || "");
      setPresets(userSettings.response_preferences?.presets || []);
      setCustomPref(userSettings.response_preferences?.custom || "");
    }
  }, [isOpen, userSettings]);

  if (!isOpen) return null;

  const handleAvatarUpload = async (event) => {
    if (event.target.files && event.target.files[0]) {
      try {
        const base64 = await resizeImage(event.target.files[0]);
        setAvatarUrl(base64);
      } catch (err) {
        console.error("Avatar upload failed:", err);
        alert("Failed to upload image");
      }
    }
  };

  const togglePreset = (style) => {
    setPresets((prev) =>
      prev.includes(style)
        ? prev.filter((item) => item !== style)
        : [...prev, style]
    );
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onUpdateUserSettings({
        display_name: displayName,
        profile_picture_url: avatarUrl,
        response_preferences: {
          presets,
          custom: customPref.slice(0, CUSTOM_PREF_LIMIT),
        },
      });
      onClose();
    } catch (err) {
      console.error("Failed to save user settings:", err);
      alert(`Failed to save: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="user-settings-overlay" onClick={onClose}>
      <div
        className="user-settings-modal"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="user-settings-title"
      >
        <div className="user-settings-header">
          <div>
            <div className="user-settings-kicker">Personalization</div>
            <h2 id="user-settings-title">User Settings</h2>
            <p>Personalize replies.</p>
          </div>
          <button type="button" className="user-settings-close" onClick={onClose} aria-label="Close settings">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div className="user-settings-scroll">
          <section className="user-settings-section">
            <div className="user-settings-section-head">
              <div className="user-settings-section-kicker">Profile</div>
              <h3>Profile</h3>
            </div>

            <div className="user-settings-profile-grid">
              <div className="user-avatar-panel">
                <button
                  type="button"
                  className="user-avatar-button"
                  onClick={() => fileInputRef.current?.click()}
                  title="Upload profile photo"
                >
                  {avatarUrl ? (
                    <img src={avatarUrl} alt="User avatar" />
                  ) : (
                    <div className="user-avatar-placeholder">+</div>
                  )}
                  <span className="user-avatar-overlay">Change</span>
                </button>

                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  style={{ display: "none" }}
                  onChange={handleAvatarUpload}
                />

                {avatarUrl ? (
                  <button
                    type="button"
                    className="user-avatar-remove"
                    onClick={() => setAvatarUrl("")}
                  >
                    Remove photo
                  </button>
                ) : (
                  <div className="user-avatar-note">Optional profile photo</div>
                )}
              </div>

              <div className="user-settings-form">
                <label htmlFor="display-name-input">Display Name</label>
                <input
                  id="display-name-input"
                  type="text"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  placeholder="What should creators call you?"
                />
              </div>
            </div>
          </section>

          <section className="user-settings-section">
            <div className="user-settings-section-head">
              <div className="user-settings-section-kicker">Delivery</div>
              <h3>Style</h3>
            </div>

            <div className="user-settings-presets">
              {RESPONSE_STYLES.map((style) => {
                const selected = presets.includes(style.label);
                return (
                  <button
                    key={style.label}
                    type="button"
                    className={`user-settings-preset ${selected ? "selected" : ""}`}
                    onClick={() => togglePreset(style.label)}
                  >
                    <span className="user-settings-preset-title">{style.label}</span>
                    <span className="user-settings-preset-desc">{style.description}</span>
                  </button>
                );
              })}
            </div>
          </section>

          <section className="user-settings-section">
            <div className="user-settings-section-head">
              <div className="user-settings-section-kicker">Context</div>
              <h3>Custom</h3>
            </div>

            <div className="user-settings-form">
              <label htmlFor="custom-instructions-input">Tell creators about you</label>
              <textarea
                id="custom-instructions-input"
                value={customPref}
                onChange={(event) => setCustomPref(event.target.value.slice(0, CUSTOM_PREF_LIMIT))}
                placeholder={
                  "Examples:\nI run a small agency with five employees.\nI am a beginner, so explain from first principles.\nChallenge my thinking instead of agreeing too quickly.\nSports examples usually click faster for me."
                }
                rows={6}
              />
              <div className="user-settings-custom-meta">
                <span>Filtered server-side.</span>
                <span>{customPref.length}/{CUSTOM_PREF_LIMIT}</span>
              </div>
            </div>
          </section>
        </div>

        <div className="user-settings-footer">
          <button type="button" className="secondary-button" onClick={onClose}>
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
