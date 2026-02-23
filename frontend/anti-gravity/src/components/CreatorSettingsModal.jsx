import { useState, useRef } from "react";
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
    onUpdateSearchMode
}) {
    if (!isOpen) return null;

    const [isSaving, setIsSaving] = useState(false);

    // Local state for immediate feedback inside modal
    const [localCreatorColor, setLocalCreatorColor] = useState(visualConfig?.creatorNameColor || "#4285F4");
    const [localUserColor, setLocalUserColor] = useState(visualConfig?.userNameColor || "#3C4043");
    const [localSearchMode, setLocalSearchMode] = useState(searchMode);

    const fileInputRef = useRef(null);

    const handleColorChange = async (type, color) => {
        if (type === "creator") {
            setLocalCreatorColor(color);
            await onUpdateVisualConfig({ creatorNameColor: color });
        } else {
            setLocalUserColor(color);
            await onUpdateVisualConfig({ userNameColor: color });
        }
    };

    const handleSearchModeChange = async (mode) => {
        setLocalSearchMode(mode);
        if (onUpdateSearchMode) {
            await onUpdateSearchMode(mode);
        }
    };

    const handleAvatarUpload = async (e) => {
        if (e.target.files && e.target.files[0]) {
            const file = e.target.files[0];
            try {
                const base64 = await resizeImage(file);
                // This updates the avatar globally via App.jsx handler
                await onUpdateCreatorAvatar(base64);
            } catch (err) {
                console.error("Avatar upload failed:", err);
                alert("Failed to upload image");
            }
            e.target.value = "";
        }
    };

    return (
        <div className="settings-modal-overlay" onClick={onClose}>
            <div className="settings-modal" onClick={e => e.stopPropagation()}>
                <div className="settings-header">
                    <h3>{creatorName} Settings</h3>
                    <button className="close-btn" onClick={onClose}>&times;</button>
                </div>

                <div className="settings-section">
                    <h4>Profile Picture</h4>
                    <div className="avatar-upload-row">
                        <div className="current-avatar">
                            {creatorAvatarUrl ? (
                                <img src={creatorAvatarUrl} alt="Creator" />
                            ) : (
                                <div className="avatar-placeholder">{creatorName[0]}</div>
                            )}
                        </div>
                        <button onClick={() => fileInputRef.current?.click()} className="upload-btn">
                            Change Photo
                        </button>
                        <input
                            type="file"
                            ref={fileInputRef}
                            style={{ display: 'none' }}
                            accept="image/*"
                            onChange={handleAvatarUpload}
                        />
                    </div>
                </div>

                <div className="settings-section">
                    <h4>Search Mode</h4>
                    <div className="mode-toggle-group">
                        <button
                            className={`mode-btn ${localSearchMode === "ingested_only" ? "selected" : ""}`}
                            onClick={() => handleSearchModeChange("ingested_only")}
                        >
                            <div className="radio-circle">
                                <div className="radio-inner"></div>
                            </div>
                            <div className="mode-info">
                                <span className="mode-label">Ingested Content Only</span>
                                <span className="mode-desc">Only refers to your official documents and transcripts.</span>
                            </div>
                        </button>

                        <button
                            className={`mode-btn ${localSearchMode === "hybrid" ? "selected" : ""}`}
                            onClick={() => handleSearchModeChange("hybrid")}
                        >
                            <div className="radio-circle">
                                <div className="radio-inner"></div>
                            </div>
                            <div className="mode-info">
                                <span className="mode-label">Ingested Content + Web Search</span>
                                <span className="mode-desc">Uses your content first, then searches the web for context.</span>
                            </div>
                        </button>
                    </div>
                </div>

                <div className="settings-section">
                    <h4>Creator Name Colour</h4>
                    <div className="color-palette">
                        {COLORS.map(c => (
                            <button
                                key={c.value}
                                className={`color-swatch ${localCreatorColor === c.value ? 'selected' : ''}`}
                                style={{ backgroundColor: c.value }}
                                onClick={() => handleColorChange("creator", c.value)}
                                title={c.label}
                            />
                        ))}
                    </div>
                </div>

                <div className="settings-section">
                    <h4>User Name Colour (You)</h4>
                    <div className="color-palette">
                        {COLORS.map(c => (
                            <button
                                key={c.value}
                                className={`color-swatch ${localUserColor === c.value ? 'selected' : ''}`}
                                style={{ backgroundColor: c.value }}
                                onClick={() => handleColorChange("user", c.value)}
                                title={c.label}
                            />
                        ))}
                    </div>
                </div>

                <div className="settings-footer">
                    <button className="done-btn" onClick={onClose}>Done</button>
                </div>
            </div>
        </div>
    );
}
