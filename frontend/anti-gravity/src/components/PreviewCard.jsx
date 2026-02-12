import React from "react";
import "./PreviewCard.css";

export function PreviewCard({ card }) {
    if (!card) return null;

    const {
        resource_type = "video",
        title,
        subtitle,
        thumbnail_url,
        short_snippet,
        url,
        action_label = "Open"
    } = card;

    // Icons based on type
    const renderIcon = () => {
        if (card.type === "channel_search_card") {
            return (
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="card-type-icon search">
                    <circle cx="11" cy="11" r="8"></circle>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                </svg>
            );
        }
        if (resource_type === "video" || card.type === "channel_card") {
            return (
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" className="card-type-icon video">
                    <path d="M19.615 3.184c-3.604-.246-11.631-.245-15.23 0-3.897.266-4.356 2.62-4.385 8.816.029 6.185.484 8.549 4.385 8.816 3.6.245 11.626.246 15.23 0 3.897-.266 4.356-2.62 4.385-8.816-.029-6.185-.484-8.549-4.385-8.816zm-10.615 12.816v-8l8 3.993-8 4.007z" />
                </svg>
            );
        }
        return (
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="card-type-icon article">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                <polyline points="14 2 14 8 20 8"></polyline>
                <line x1="16" y1="13" x2="8" y2="13"></line>
                <line x1="16" y1="17" x2="8" y2="17"></line>
                <polyline points="10 9 9 9 8 9"></polyline>
            </svg>
        );
    };

    return (
        <a href={url} target="_blank" rel="noopener noreferrer" className="preview-card">
            <div className="preview-card-thumbnail">
                {thumbnail_url ? (
                    <img src={thumbnail_url} alt={title} />
                ) : (
                    <div className="preview-card-placeholder">
                        {renderIcon()}
                    </div>
                )}
                {resource_type === "video" && (
                    <div className="play-overlay">
                        <svg viewBox="0 0 24 24" width="32" height="32" fill="white"><path d="M8 5v14l11-7z" /></svg>
                    </div>
                )}
            </div>
            <div className="preview-card-content">
                <div className="preview-card-header">
                    <h4 className="preview-card-title">{title}</h4>
                    <div className="preview-card-meta">{subtitle}</div>
                </div>
                {short_snippet && <p className="preview-card-snippet">{short_snippet}</p>}
                <div className="preview-card-footer">
                    <span className="preview-action">{action_label}</span>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
                        <polyline points="15 3 21 3 21 9"></polyline>
                        <line x1="10" y1="14" x2="21" y2="3"></line>
                    </svg>
                </div>
            </div>
        </a>
    );
}
