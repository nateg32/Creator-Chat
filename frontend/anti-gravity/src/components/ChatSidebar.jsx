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
    onRecall, // Placeholder for future
    onNewCreator, // () => void
    onDeleteCreators, // (creatorIds[]) => Promise<void>
    onDeleteThread, // (threadId, creatorId) => void
    onRestoreThread, // (threadId, creatorId) => void
    archivedThreadsByCreator = {},
    onLoadArchived // (creatorId) => void
}) {
    const [isCollapsed, setIsCollapsed] = useState(false);
    const [expandedCreators, setExpandedCreators] = useState({});

    // Delete Mode State
    const [isDeleteMode, setIsDeleteMode] = useState(false);
    const [selectedCreators, setSelectedCreators] = useState(new Set());

    // Archive Mode State
    const [isArchiveMode, setIsArchiveMode] = useState(false);

    // Menu state
    const [activeMenu, setActiveMenu] = useState(null); // { thread: {}, creatorId: number, top?: number, bottom?: number, left: number }

    // Rename state
    const [renamingId, setRenamingId] = useState(null);
    const [renameValue, setRenameValue] = useState("");
    const menuRef = useRef(null);

    // Auto-expand the active creator
    useEffect(() => {
        if (activeCreatorIdProp && !isDeleteMode) {
            setExpandedCreators(prev => ({ ...prev, [activeCreatorIdProp]: true }));
        }
    }, [activeCreatorIdProp, isDeleteMode]);

    // Also auto-expand if we find the active thread in a creator's list
    useEffect(() => {
        if (activeThreadId && (threadsByCreator || archivedThreadsByCreator) && !isDeleteMode) {
            // Check active threads first
            for (const [cId, threads] of Object.entries(threadsByCreator)) {
                if (threads.find(t => t.id === activeThreadId)) {
                    setExpandedCreators(prev => ({ ...prev, [cId]: true }));
                    // If found in active, insure we are not in archive mode (optional, or switch mode?)
                    // Actually, if user clicks a link to an archived thread, we might want to switch to archive mode?
                    // For now, keep simple.
                    return;
                }
            }
            // Check archived threads
            for (const [cId, threads] of Object.entries(archivedThreadsByCreator)) {
                if (threads && threads.find(t => t.id === activeThreadId)) {
                    setExpandedCreators(prev => ({ ...prev, [cId]: true }));
                    setIsArchiveMode(true); // Switch to archive mode if active thread is archived
                    return;
                }
            }
        }
    }, [activeThreadId, threadsByCreator, archivedThreadsByCreator, isDeleteMode]);

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

        if (isDeleteMode) {
            // Toggle selection
            const newSelected = new Set(selectedCreators);
            if (newSelected.has(creatorId)) {
                newSelected.delete(creatorId);
            } else {
                newSelected.add(creatorId);
            }
            setSelectedCreators(newSelected);
        } else {
            // Toggle expansion
            const isExpanding = !expandedCreators[creatorId];

            setExpandedCreators(prev => ({
                ...prev,
                [creatorId]: isExpanding
            }));

            // If expanding in Archive Mode, load archived threads
            if (isExpanding && isArchiveMode && onLoadArchived) {
                onLoadArchived(creatorId);
            }
        }
    };

    const toggleArchiveMode = () => {
        const newMode = !isArchiveMode;
        setIsArchiveMode(newMode);
        // If switching to archive mode, trigger load for all expanded creators
        if (newMode && onLoadArchived) {
            Object.keys(expandedCreators).forEach(cId => {
                if (expandedCreators[cId]) onLoadArchived(cId);
            });
        }
    };

    const toggleMenu = (e, thread, creatorId) => {
        e.stopPropagation();
        if (activeMenu && activeMenu.thread.id === thread.id) {
            setActiveMenu(null);
        } else {
            const rect = e.currentTarget.getBoundingClientRect();
            const spaceBelow = window.innerHeight - rect.bottom;
            // Approximate height of the menu (4 items * ~40px) = ~160px
            const MENU_HEIGHT = 160;

            let topPos, bottomPos;

            if (spaceBelow < MENU_HEIGHT) {
                // Not enough space below, position above
                // 'bottom' value should be distance from viewport bottom to button top
                bottomPos = window.innerHeight - rect.top + 4;
            } else {
                // Default: position below
                topPos = rect.bottom + 4;
            }

            setActiveMenu({
                thread,
                creatorId,
                top: topPos,
                bottom: bottomPos,
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

    const handleRestore = (e) => {
        e.stopPropagation();
        if (!activeMenu) return;
        onRestoreThread && onRestoreThread(activeMenu.thread.id, activeMenu.creatorId);
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

    // Toggle Delete Mode
    const toggleDeleteMode = () => {
        if (isDeleteMode) {
            // Cancel/Exit
            setIsDeleteMode(false);
            setSelectedCreators(new Set());
        } else {
            // Enter delete mode
            setIsDeleteMode(true);
            // Optionally collapse all to make selection easier? 
            // setExpandedCreators({}); 
        }
    };

    const executeDeleteCreators = async () => {
        if (selectedCreators.size === 0) return;
        if (!window.confirm(`Are you sure you want to delete ${selectedCreators.size} creator(s)? This cannot be undone.`)) return;

        if (onDeleteCreators) {
            await onDeleteCreators(Array.from(selectedCreators));
        }
        setIsDeleteMode(false);
        setSelectedCreators(new Set());
    };

    // Render Menu via Portal (Moved OUTSIDE the loop)
    const renderActiveMenu = () => {
        if (!activeMenu) return null;

        return createPortal(
            <div
                className="thread-dropdown-menu portal-menu"
                ref={menuRef}
                style={{
                    top: activeMenu.top,
                    bottom: activeMenu.bottom,
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
                {isArchiveMode ? (
                    <div className="menu-item" onClick={handleRestore}>
                        Unarchive
                    </div>
                ) : (
                    <div className="menu-item" onClick={handleArchive}>
                        Archive
                    </div>
                )}

                <div className="menu-item delete" onClick={handleDelete}>
                    Delete
                </div>
            </div>,
            document.body
        );
    };

    return (
        <div className={`chat-sidebar ${isCollapsed ? "collapsed" : ""} ${isDeleteMode ? "delete-mode" : ""} ${isArchiveMode ? "archive-mode" : ""}`}>
            <div className="sidebar-header">
                {!isCollapsed && (
                    <div className="header-actions-left">
                        <h3>{isArchiveMode ? "Archived" : "Chats"}</h3>

                        {!isDeleteMode ? (
                            <>
                                <button
                                    onClick={onNewCreator}
                                    className="icon-btn new-creator-btn"
                                    title="New Creator"
                                >
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <path d="M12 5V19M5 12H19" strokeLinecap="round" strokeLinejoin="round" />
                                    </svg>
                                </button>
                                <button
                                    onClick={toggleDeleteMode}
                                    className="icon-btn delete-mode-btn"
                                    title="Delete Creators"
                                >
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" strokeLinecap="round" strokeLinejoin="round" />
                                    </svg>
                                </button>
                                <button
                                    onClick={toggleArchiveMode}
                                    className={`icon-btn archive-mode-btn ${isArchiveMode ? 'active' : ''}`}
                                    title={isArchiveMode ? "Show Active Chats" : "Show Archived Chats"}
                                >
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <polyline points="21 8 21 21 3 21 3 8" />
                                        <rect x="1" y="3" width="22" height="5" />
                                        <line x1="10" y1="12" x2="14" y2="12" />
                                    </svg>
                                </button>
                            </>
                        ) : (
                            <div className="delete-actions">
                                <span className="delete-count">{selectedCreators.size} selected</span>
                                {selectedCreators.size > 0 && (
                                    <button
                                        onClick={executeDeleteCreators}
                                        className="confirm-delete-btn"
                                        title="Delete Selected"
                                    >
                                        Delete
                                    </button>
                                )}
                                <button
                                    onClick={toggleDeleteMode}
                                    className="cancel-delete-btn"
                                    title="Cancel"
                                >
                                    Cancel
                                </button>
                            </div>
                        )}
                    </div>
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
                    {creators.length === 0 ? (
                        <div className="empty-state">
                            <p>No creators yet</p>
                        </div>
                    ) : (
                        creators.map((creator) => {
                            const threads = isArchiveMode
                                ? (archivedThreadsByCreator[creator.id] || [])
                                : (threadsByCreator[creator.id] || []);

                            const isExpanded = expandedCreators[creator.id];
                            const isSelected = selectedCreators.has(creator.id);

                            // Check if this creator is "active" (active thread belongs to them)
                            // In archive mode, we can still highlight if the active thread is in archive list
                            const hasActiveThread = threads.some(t => t.id === activeThreadId);

                            return (
                                <div key={creator.id} className={`creator-group ${hasActiveThread ? 'has-active' : ''} ${isSelected ? 'selected' : ''}`}>
                                    <div
                                        className="creator-header"
                                        onClick={(e) => toggleCreator(creator.id, e)}
                                    >
                                        {isDeleteMode && (
                                            <div className="checkbox-wrapper">
                                                <div className={`custom-checkbox ${isSelected ? 'checked' : ''}`}>
                                                    {isSelected && (
                                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                                                            <polyline points="20 6 9 17 4 12" strokeLinecap="round" strokeLinejoin="round" />
                                                        </svg>
                                                    )}
                                                </div>
                                            </div>
                                        )}

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

                                        {!isDeleteMode && (
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
                                        )}
                                    </div>

                                    {!isDeleteMode && isExpanded && (
                                        <div className="thread-list">
                                            {/* List threads */}
                                            {threads.length === 0 && (
                                                <div className="empty-threads">
                                                    {isArchiveMode ? "No archived chats" : "No chats yet"}
                                                </div>
                                            )}
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

                                            {/* New Chat Button - Only show if NOT in Archive Mode */}
                                            {!isArchiveMode && (
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
                                            )}
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
