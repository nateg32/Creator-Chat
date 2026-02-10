import { useState, useRef, useEffect } from "react";
import "./UserSettingsModal.css";
import { resizeImage } from "../utils/image";

const RESPONSE_STYLES = [
    "Simple English",
    "Concise answers",
    "Step-by-step explanations",
    "Friendly and conversational",
    "Professional and direct",
    "Examples-first explanations",
    "Bullet-point heavy"
];

export function UserSettingsModal({
    isOpen,
    onClose,
    userSettings,
    onUpdateUserSettings
}) {
    if (!isOpen) return null;

    const [displayName, setDisplayName] = useState(userSettings?.display_name || "");
    const [avatarUrl, setAvatarUrl] = useState(userSettings?.profile_picture_url || "");
    const [presets, setPresets] = useState(userSettings?.response_preferences?.presets || []);
    const [customPref, setCustomPref] = useState(userSettings?.response_preferences?.custom || "");
    const [saving, setSaving] = useState(false);

    // Sync state if userSettings changes while open? usually not needed if modal is remounted or key changes
    useEffect(() => {
        if (isOpen && userSettings) {
            setDisplayName(userSettings.display_name || "");
            setAvatarUrl(userSettings.profile_picture_url || "");
            setPresets(userSettings.response_preferences?.presets || []);
            setCustomPref(userSettings.response_preferences?.custom || "");
        }
    }, [isOpen, userSettings]);

    const fileInputRef = useRef(null);

    const handleAvatarUpload = async (e) => {
        if (e.target.files && e.target.files[0]) {
            try {
                const base64 = await resizeImage(e.target.files[0]);
                setAvatarUrl(base64);
            } catch (err) {
                console.error("Avatar upload failed:", err);
                alert("Failed to upload image");
            }
        }
    };

    const togglePreset = (style) => {
        setPresets(prev =>
            prev.includes(style)
                ? prev.filter(p => p !== style)
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
                    custom: customPref
                }
            });
            onClose();
        } catch (err) {
            console.error("Failed to save user settings:", err);
            alert("Failed to save: " + err.message);
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="user-settings-overlay" onClick={onClose}>
            <div className="user-settings-modal" onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <h2>User Settings</h2>
                    <button className="close-btn" onClick={onClose}>&times;</button>
                </div>

                <div className="settings-scroll-area">
                    {/* User Profile Section */}
                    <div className="settings-section">
                        <h3>Profile</h3>
                        <div className="profile-row">
                            <div className="avatar-wrapper">
                                {avatarUrl ? (
                                    <img src={avatarUrl} alt="User Avatar" />
                                ) : (
                                    <div className="avatar-placeholder">You</div>
                                )}
                                <button className="edit-avatar-btn" onClick={() => fileInputRef.current?.click()}>
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                                    </svg>
                                </button>
                                <input
                                    type="file"
                                    ref={fileInputRef}
                                    style={{ display: 'none' }}
                                    accept="image/*"
                                    onChange={handleAvatarUpload}
                                />
                            </div>
                            <div className="name-input-wrapper">
                                <label>Display Name</label>
                                <input
                                    type="text"
                                    value={displayName}
                                    onChange={e => setDisplayName(e.target.value)}
                                    placeholder="Your Name"
                                />
                            </div>
                        </div>
                    </div>

                    {/* Response Preferences Section */}
                    <div className="settings-section">
                        <h3>Bot Response Preferences</h3>
                        <p className="section-desc">Control how creators respond to you. These settings apply to all chats.</p>

                        <div className="presets-grid">
                            {RESPONSE_STYLES.map(style => (
                                <button
                                    key={style}
                                    className={`preset-chip ${presets.includes(style) ? 'selected' : ''}`}
                                    onClick={() => togglePreset(style)}
                                >
                                    {style}
                                </button>
                            ))}
                        </div>

                        <div className="custom-pref-wrapper">
                            <label>Custom Instructions</label>
                            <textarea
                                value={customPref}
                                onChange={e => setCustomPref(e.target.value)}
                                placeholder="e.g. 'Explain things like I'm 5', 'Challenge my thinking', etc."
                                rows={3}
                            />
                        </div>
                    </div>
                </div>

                <div className="modal-footer">
                    <button className="cancel-btn" onClick={onClose}>Cancel</button>
                    <button className="save-btn" onClick={handleSave} disabled={saving}>
                        {saving ? "Saving..." : "Save Settings"}
                    </button>
                </div>
            </div>
        </div>
    );
}
