import { useState, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { formatCreatorName } from "../utils/format";
import "./ChatSidebar.css";

export function ChatSidebar({
    creators = [],
    threadsByCreator = {}, // { creatorId: [thread1, thread2] }
    activeThreadId,
    activeCreatorIdProp, // Optional, can force expand
    onSelectThread,
    onNewThread, // (creatorId) => void
    onToggleSidebar,
    onRenameThread,
    onArchiveThread,
    onDeleteThread
}) {
    const [isCollapsed, setIsCollapsed] = useState(false);
    const [expandedCreators, setExpandedCreators] = useState({});

    // Menu state
    const [activeMenu, setActiveMenu] = useState(null); // { thread: {}, creatorId: number, top: 0, left: 0 }

    // Rename state
    const [renamingId, setRenamingId] = useState(null);
    const [renameValue, setRenameValue] = useState("");
    const menuRef = useRef(null);

    // Auto-expand the active creator
    useEffect(() => {
        if (activeCreatorIdProp) {
            setExpandedCreators(prev => ({ ...prev, [activeCreatorIdProp]: true }));
        }
    }, [activeCreatorIdProp]);

    // Also auto-expand if we find the active thread in a creator's list
    useEffect(() => {
        if (activeThreadId && threadsByCreator) {
            for (const [cId, threads] of Object.entries(threadsByCreator)) {
                if (threads.find(t => t.id === activeThreadId)) {
                    setExpandedCreators(prev => ({ ...prev, [cId]: true }));
                    break;
                }
            }
        }
    }, [activeThreadId, threadsByCreator]);

    // Close menu on outside click
    useEffect(() => {
        function handleClickOutside(event) {
            if (menuRef.current && !menuRef.current.contains(event.target)) {
                setActiveMenu(null);
            }
        }
        document.addEventListener("mousedown", handleClickOutside);
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, []);

    const handleToggle = () => {
        setIsCollapsed(!isCollapsed);
        onToggleSidebar && onToggleSidebar(!isCollapsed);
    };

    const toggleCreator = (creatorId, e) => {
        if (e) e.stopPropagation();
        setExpandedCreators(prev => ({
            ...prev,
            [creatorId]: !prev[creatorId]
        }));
    };

    const toggleMenu = (e, thread, creatorId) => {
        e.stopPropagation();
        if (activeMenu && activeMenu.thread.id === threadId) {
            setActiveMenu(null);
        } else {
            const rect = e.currentTarget.getBoundingClientRect();
            setActiveMenu({
                thread,
                creatorId,
                top: rect.bottom + 4,
                left: rect.right
            });
        }
    };

    const startRename = (e) => {
        e.stopPropagation();
        if (!activeMenu) return;
        setRenamingId(activeMenu.thread.id);
        setRenameValue(activeMenu.thread.title || "");
        setActiveMenu(null);
    };

    const submitRename = (creatorId) => {
        if (renamingId && renameValue.trim()) {
            onRenameThread && onRenameThread(renamingId, creatorId, renameValue.trim());
        }
        setRenamingId(null);
    };

    const handleRenameKeyDown = (e, creatorId) => {
        if (e.key === 'Enter') submitRename(creatorId);
        if (e.key === 'Escape') setRenamingId(null);
    };

    const handleArchive = (e) => {
        e.stopPropagation();
        if (!activeMenu) return;
        onArchiveThread && onArchiveThread(activeMenu.thread.id, activeMenu.creatorId);
        setActiveMenu(null);
    };

    const handleDelete = (e) => {
        e.stopPropagation();
        if (!activeMenu) return;
        onDeleteThread && onDeleteThread(activeMenu.thread.id, activeMenu.creatorId);
        setActiveMenu(null);
    };

    const copyLink = (e) => {
        e.stopPropagation();
        if (!activeMenu) return;
        const url = `${window.location.origin}/?thread=${activeMenu.thread.id}`;
        navigator.clipboard.writeText(url)
            .then(() => alert("Link copied to clipboard"))
            .catch(err => console.error("Failed to copy", err));
        setActiveMenu(null);
    }

    // Render Menu via Portal (Moved OUTSIDE the loop)
    const renderActiveMenu = () => {
        if (!activeMenu) return null;

        return createPortal(
            <div
                className="thread-dropdown-menu portal-menu"
                ref={menuRef}
                style={{
                    top: activeMenu.top,
                    left: activeMenu.left,
                    position: 'fixed',
                    transform: 'translateX(-100%)', // Align right edge
                    marginTop: 0,
                    zIndex: 99999
                }}
                onClick={e => e.stopPropagation()}
            >
                <div className="menu-item" onClick={copyLink}>
                    Share
                </div>
                <div className="menu-item" onClick={startRename}>
                    Rename
                </div>
                <div className="menu-item" onClick={handleArchive}>
                    Archive
                </div>
                <div className="menu-item delete" onClick={handleDelete}>
                    Delete
                </div>
            </div>,
            document.body
        );
    };

    return (
        <div className={`chat-sidebar ${isCollapsed ? "collapsed" : ""}`}>
            <div className="sidebar-header">
                {!isCollapsed && <h3>Chats</h3>}
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
                    {creators.length === 0 ? (
                        <div className="empty-state">
                            <p>No creators yet</p>
                        </div>
                    ) : (
                        creators.map((creator) => {
                            const threads = threadsByCreator[creator.id] || [];
                            const isExpanded = expandedCreators[creator.id];

                            // Check if this creator is "active" (active thread belongs to them)
                            const hasActiveThread = threads.some(t => t.id === activeThreadId);

                            return (
                                <div key={creator.id} className={`creator-group ${hasActiveThread ? 'has-active' : ''}`}>
                                    <div
                                        className="creator-header"
                                        onClick={(e) => toggleCreator(creator.id, e)}
                                    >
                                        <div className="chat-avatar-mini">
                                            {creator.profile_picture_url ? (
                                                <img src={creator.profile_picture_url} alt="" className="mini-avatar-img" />
                                            ) : (
                                                <div className="mini-avatar-placeholder">
                                                    {creator.name ? creator.name[0].toUpperCase() : "?"}
                                                </div>
                                            )}
                                        </div>
                                        <div className="creator-info">
                                            <div className="creator-name">
                                                {formatCreatorName(creator.name || creator.handle || "Unknown")}
                                            </div>
                                        </div>
                                        <div className="creator-toggle-icon">
                                            <svg
                                                width="16" height="16" viewBox="0 0 24 24"
                                                fill="none"
                                                stroke="currentColor"
                                                strokeWidth="2"
                                                style={{ transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}
                                            >
                                                <path d="M9 18L15 12L9 6" strokeLinecap="round" strokeLinejoin="round" />
                                            </svg>
                                        </div>
                                    </div>

                                    {isExpanded && (
                                        <div className="thread-list">
                                            {/* List threads */}
                                            {threads.map(thread => {
                                                const isRenaming = renamingId === thread.id;
                                                // activeMenu check is global now

                                                return (
                                                    <div
                                                        key={thread.id}
                                                        className={`thread-item ${thread.id === activeThreadId ? 'active' : ''}`}
                                                        onClick={() => !isRenaming && onSelectThread(thread.id, creator.id)}
                                                    >
                                                        {isRenaming ? (
                                                            <input
                                                                autoFocus
                                                                value={renameValue}
                                                                onChange={(e) => setRenameValue(e.target.value)}
                                                                onBlur={() => submitRename(creator.id)}
                                                                onKeyDown={(e) => handleRenameKeyDown(e, creator.id)}
                                                                onClick={(e) => e.stopPropagation()}
                                                                className="thread-rename-input"
                                                            />
                                                        ) : (
                                                            <>
                                                                <div className="thread-title">{thread.title || "New conversation"}</div>
                                                                <div className="thread-menu-trigger" onClick={(e) => toggleMenu(e, thread, creator.id)}>
                                                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                                                                        <circle cx="12" cy="12" r="2" />
                                                                        <circle cx="19" cy="12" r="2" />
                                                                        <circle cx="5" cy="12" r="2" />
                                                                    </svg>
                                                                </div>
                                                                {/* Menu is now rendered outside */}
                                                            </>
                                                        )}
                                                    </div>
                                                );
                                            })}

                                            {/* New Chat Button */}
                                            <button
                                                className="new-thread-btn"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    onNewThread(creator.id);
                                                }}
                                            >
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ marginRight: 6 }}>
                                                    <path d="M12 5V19M5 12H19" strokeLinecap="round" strokeLinejoin="round" />
                                                </svg>
                                                New Chat
                                            </button>
                                        </div>
                                    )}
                                </div>
                            );
                        })
                    )}
                </div>
            )}
            {/* Render global menu */}
            {renderActiveMenu()}
        </div>
    );
}
