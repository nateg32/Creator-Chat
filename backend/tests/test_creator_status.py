import pytest
from backend.app import get_creator_status
from backend.models import CreatorWithConfigResponse
from backend.db import db

def test_creator_status_new_creator(monkeypatch):
    """New creator, no approvals -> ready_to_chat false."""
    def mock_execute_one(query, params=None):
        q = query.lower()
        if "from scrape_items" in q:
            return {"count": 0}
        if "from documents" in q:
            return {"count": 0}
        if "from creators" in q:
            return {"config_version": 1, "last_approved_version": 0, "fingerprint_status": "empty"}
        return None
    
    monkeypatch.setattr(db, "execute_one", mock_execute_one)
    
    status = get_creator_status(1)
    
    assert status["needs_reapproval"] is False
    assert status["ready_to_chat"] is False
    assert status["block_reason"] == "Waiting for content to be ingested."

def test_creator_status_approved_but_not_ingested(monkeypatch):
    """Approved=1, ingested=0, status not ready -> false, 'Waiting for content to be ingested.'"""
    def mock_execute_one(query, params=None):
        q = query.lower()
        if "from scrape_items" in q:
            return {"count": 1}
        if "from documents" in q:
            return {"count": 0}
        if "from creators" in q:
            return {"config_version": 1, "last_approved_version": 1, "fingerprint_status": "empty"}
        return None
        
    monkeypatch.setattr(db, "execute_one", mock_execute_one)
    
    status = get_creator_status(1)
    
    assert status["needs_reapproval"] is False
    assert status["ready_to_chat"] is False
    assert status["block_reason"] == "Waiting for content to be ingested."

def test_creator_status_fully_ready(monkeypatch):
    """Approved=1, ingested=1 -> true"""
    def mock_execute_one(query, params=None):
        q = query.lower()
        if "from scrape_items" in q:
            return {"count": 1}
        if "from documents" in q:
            return {"count": 1}
        if "from creators" in q:
            return {"config_version": 1, "last_approved_version": 1, "fingerprint_status": "ready"}
        return None
        
    monkeypatch.setattr(db, "execute_one", mock_execute_one)
    
    status = get_creator_status(1)
    
    assert status["needs_reapproval"] is False
    assert status["ready_to_chat"] is True
    assert status["block_reason"] == ""
