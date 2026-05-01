import pytest
import httpx

from backend import app as app_module
from backend.services import research_provider as research_provider_module
from backend.services import transcript_worker as transcript_worker_module
from backend.services.user_priority_service import user_priority_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_detect_user_state_uses_supplied_creator_profile(monkeypatch):
    def fail_execute_one(*args, **kwargs):
        raise AssertionError("db.execute_one should not be called")

    captured = {}

    def fake_classify_all(question, history, creator_row):
        captured["question"] = question
        captured["history"] = history
        captured["creator_row"] = creator_row
        return {"request_type": "casual", "clarity_level": "clear", "confusion_level": "low", "skill_level": "unknown"}

    monkeypatch.setattr("backend.db.db.execute_one", fail_execute_one)
    monkeypatch.setattr("backend.services.classifiers.classifiers.classify_all", fake_classify_all)

    creator_profile = {"name": "Scoped Creator", "handle": "scoped"}
    result = user_priority_service.detect_user_state(
        "hello there",
        history=[{"role": "user", "content": "hi"}],
        creator_profile=creator_profile,
    )

    assert captured["creator_row"] == creator_profile
    assert result["request_type"] == "casual"


@pytest.mark.anyio
async def test_app_lifespan_rejects_default_secret_in_production(monkeypatch):
    monkeypatch.setattr(app_module, "_IS_PRODUCTION", True)
    monkeypatch.setattr(app_module.settings, "JWT_SECRET_KEY", app_module._DEFAULT_JWT_SECRET)
    monkeypatch.setattr(app_module.settings, "COOKIE_SAMESITE", "lax")
    monkeypatch.setattr(app_module.settings, "COOKIE_SECURE", True)

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        async with app_module.app_lifespan(app_module.app):
            pass


def test_safe_validation_target_rejects_private_hosts():
    valid_hosts = {"instagram": ["instagram.com", "www.instagram.com"]}

    assert app_module._is_safe_validation_target("https://www.instagram.com/someuser", "instagram", valid_hosts) is True
    assert app_module._is_safe_validation_target("http://localhost/internal", "instagram", valid_hosts) is False
    assert app_module._is_safe_validation_target("file:///etc/passwd", "instagram", valid_hosts) is False


def test_worker_fetch_guards_reject_private_urls():
    assert transcript_worker_module._is_safe_remote_url("https://example.com/video.mp4") is True
    assert transcript_worker_module._is_safe_remote_url("http://127.0.0.1:8000/internal") is False
    assert transcript_worker_module._is_safe_remote_url("file:///tmp/video.mp4") is False


def test_research_provider_rejects_private_validation_targets():
    assert research_provider_module._is_safe_public_url("https://example.com/resource") is True
    assert research_provider_module._is_safe_public_url("http://localhost/admin") is False
    assert research_provider_module._is_safe_public_url("https://www.youtube.com/watch?v=abc", {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}) is True
    assert research_provider_module._is_safe_public_url("https://evil.example/watch?v=abc", {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}) is False


@pytest.mark.anyio
async def test_session_auth_rejects_untrusted_origin(monkeypatch):
    def fail_get_user_from_session(*args, **kwargs):
        raise AssertionError("session lookup should not run for an invalid origin")

    monkeypatch.setattr(app_module, "get_user_from_session", fail_get_user_from_session)

    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("session_id", "session-123")
        response = await client.post(
            "/creators",
            headers={"Origin": "https://evil.example"},
            json={"name": "Blocked Creator", "handle": "blocked", "platforms": []},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid request origin"
