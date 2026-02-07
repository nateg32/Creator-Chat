import { useState } from "react";
import { formatCreatorName } from "../utils/format";
import "./ChatSidebar.css";

export function ChatSidebar({
    chats,
    activeChat,
    onSelectChat,
    onNewChat,
    onCloseChat,
    onToggleSidebar
}) {
    const [isCollapsed, setIsCollapsed] = useState(true); // Start collapsed by default

    const handleToggle = () => {
        setIsCollapsed(!isCollapsed);
        onToggleSidebar && onToggleSidebar(!isCollapsed);
    };

    return (
        <div className={`chat-sidebar ${isCollapsed ? "collapsed" : ""}`}>
            <div className="sidebar-header">
                {!isCollapsed && (
                    <>
                        <h3>Chats</h3>
                        <button onClick={onNewChat} className="new-chat-button" title="New Chat">
                            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                                <path d="M10 4V16M4 10H16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                            </svg>
                        </button>
                    </>
                )}
                <button onClick={handleToggle} className="toggle-button" title={isCollapsed ? "Expand" : "Collapse"}>
                    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                        {isCollapsed ? (
                            <path d="M7 4L13 10L7 16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                        ) : (
                            <path d="M13 4L7 10L13 16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                        )}
                    </svg>
                </button>
            </div>

            {!isCollapsed && (
                <div className="chat-list">
                    {chats.length === 0 ? (
                        <div className="empty-state">
                            <p>No chats yet</p>
                        </div>
                    ) : (
                        chats.map((chat) => (
                            <a
                                key={chat.id}
                                className={`chat-item ${chat.id === activeChat ? "active" : ""}`}
                                href={chat.creatorId ? `?creator_id=${chat.creatorId}` : "#"}
                                onClick={(e) => {
                                    if (!e.ctrlKey && !e.metaKey && !e.shiftKey && chat.creatorId) {
                                        e.preventDefault();
                                        onSelectChat(chat.id);
                                    } else if (!chat.creatorId) {
                                        e.preventDefault();
                                        onSelectChat(chat.id);
                                    }
                                }}
                                style={{ textDecoration: 'none', color: 'inherit', display: 'flex' }}
                            >
                                <div className="chat-avatar-mini">
                                    {chat.creatorAvatarUrl ? (
                                        <img src={chat.creatorAvatarUrl} alt="" className="mini-avatar-img" />
                                    ) : (
                                        <div className="mini-avatar-placeholder">
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                                                <path d="M12 4L14.4 9.6L20 12L14.4 14.4L12 20L9.6 14.4L4 12L9.6 9.6L12 4Z" fill="#4285F4" />
                                            </svg>
                                        </div>
                                    )}
                                </div>
                                <div className="chat-info">
                                    <div className="chat-name">
                                        {formatCreatorName(chat.creatorName || chat.handle || "Unknown Creator")}
                                    </div>
                                    <div className="chat-preview">{chat.messages.length > 1
                                        ? chat.messages[chat.messages.length - 1].text.substring(0, 50) + "..."
                                        : "New conversation"}</div>
                                    {chat.isTemporary && (
                                        <span className="temp-badge">Temporary</span>
                                    )}
                                </div>
                                {chats.length > 1 && (
                                    <button
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            e.preventDefault();
                                            onCloseChat(chat.id);
                                        }}
                                        className="close-chat-button"
                                        title="Close chat"
                                    >
                                        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                                            <path d="M4 4L12 12M4 12L12 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                                        </svg>
                                    </button>
                                )}
                            </a>
                        ))
                    )}
                </div>
            )}
        </div>
    );
}
