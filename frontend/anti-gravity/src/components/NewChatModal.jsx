import { useState } from "react";
import "./NewChatModal.css";

export function NewChatModal({ onClose, onCreateChat, existingCreators }) {
    const [mode, setMode] = useState("temporary"); // "temporary" or "new" or "existing"
    const [creatorName, setCreatorName] = useState("");
    const [creatorHandle, setCreatorHandle] = useState("");
    const [selectedCreatorId, setSelectedCreatorId] = useState(null);

    const handleSubmit = (e) => {
        e.preventDefault();

        if (mode === "temporary") {
            onCreateChat({
                type: "temporary",
                name: creatorName || "Temporary Creator",
                handle: creatorHandle || "temp",
            });
        } else if (mode === "new") {
            onCreateChat({
                type: "new",
                name: creatorName,
                handle: creatorHandle,
            });
        } else if (mode === "existing") {
            const creator = existingCreators.find(c => c.id === selectedCreatorId);
            if (creator) {
                onCreateChat({
                    type: "existing",
                    creatorId: creator.id,
                    name: creator.name,
                    handle: creator.handle,
                });
            }
        }

        onClose();
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h2>New Chat</h2>
                    <button onClick={onClose} className="close-modal-button">
                        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M5 5L15 15M5 15L15 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                        </svg>
                    </button>
                </div>

                <form onSubmit={handleSubmit} className="modal-body">
                    <div className="mode-selector">
                        <label className={`mode-option ${mode === "temporary" ? "active" : ""}`}>
                            <input
                                type="radio"
                                value="temporary"
                                checked={mode === "temporary"}
                                onChange={(e) => setMode(e.target.value)}
                            />
                            <div className="mode-details">
                                <div className="mode-title">💬 Temporary Chat</div>
                                <div className="mode-description">Quick chat without saving the creator</div>
                            </div>
                        </label>

                        {existingCreators && existingCreators.length > 0 && (
                            <label className={`mode-option ${mode === "existing" ? "active" : ""}`}>
                                <input
                                    type="radio"
                                    value="existing"
                                    checked={mode === "existing"}
                                    onChange={(e) => setMode(e.target.value)}
                                />
                                <div className="mode-details">
                                    <div className="mode-title">🤖 Existing Creator</div>
                                    <div className="mode-description">Chat with a saved creator</div>
                                </div>
                            </label>
                        )}

                        <label className={`mode-option ${mode === "new" ? "active" : ""}`}>
                            <input
                                type="radio"
                                value="new"
                                checked={mode === "new"}
                                onChange={(e) => setMode(e.target.value)}
                            />
                            <div className="mode-details">
                                <div className="mode-title">✨ New Creator</div>
                                <div className="mode-description">Create and save a new creator</div>
                            </div>
                        </label>
                    </div>

                    {mode === "existing" && existingCreators && existingCreators.length > 0 && (
                        <div className="form-group">
                            <label>Select Creator</label>
                            <select
                                value={selectedCreatorId || ""}
                                onChange={(e) => setSelectedCreatorId(parseInt(e.target.value))}
                                required
                            >
                                <option value="">Choose a creator...</option>
                                {existingCreators.map((creator) => (
                                    <option key={creator.id} value={creator.id}>
                                        {creator.name || creator.handle}
                                    </option>
                                ))}
                            </select>
                        </div>
                    )}

                    {(mode === "temporary" || mode === "new") && (
                        <>
                            <div className="form-group">
                                <label>Creator Name</label>
                                <input
                                    type="text"
                                    placeholder="e.g., Alex Hormozi"
                                    value={creatorName}
                                    onChange={(e) => setCreatorName(e.target.value)}
                                    required={mode === "new"}
                                />
                            </div>

                            <div className="form-group">
                                <label>Handle (optional)</label>
                                <input
                                    type="text"
                                    placeholder="e.g., @alexhormozi"
                                    value={creatorHandle}
                                    onChange={(e) => setCreatorHandle(e.target.value)}
                                />
                            </div>
                        </>
                    )}

                    <div className="modal-footer">
                        <button type="button" onClick={onClose} className="secondary-button">
                            Cancel
                        </button>
                        <button
                            type="submit"
                            className="primary-button"
                            disabled={mode === "existing" && !selectedCreatorId}
                        >
                            Start Chat
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}
