import sys as _sys

# Other test modules in this suite install lightweight stubs into sys.modules
# for backend.* packages and submodules. Evict any of those stubs (anything
# whose loaded file isn't the real backend/services/*.py on disk) so this
# module always exercises the real production code paths.
import os as _os
_BACKEND_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))


def _is_stub(module) -> bool:
    try:
        file_path = getattr(module, "__file__", None) or ""
    except Exception:
        file_path = ""
    if file_path:
        return not _os.path.normcase(_os.path.abspath(file_path)).startswith(_os.path.normcase(_BACKEND_DIR))
    # Modules with no ``__file__`` and no usable ``__spec__.origin`` are
    # almost always hand-rolled ``types.ModuleType(...)`` stubs installed by
    # other test modules. Evict them so the real implementation gets imported
    # on demand.
    spec = getattr(module, "__spec__", None)
    spec_origin = getattr(spec, "origin", None) if spec is not None else None
    if not spec_origin or spec_origin == "namespace":
        try:
            path_attr = list(getattr(module, "__path__", None) or [])
        except Exception:
            return True
        # A package with a real ``__path__`` is allowed to act as a namespace
        # parent (Python will resolve submodules against its directory), but a
        # leaf module that lacks both ``__file__`` and ``__spec__.origin`` is
        # always a stub.
        if not path_attr:
            return True
        for entry in path_attr:
            if entry and _os.path.normcase(_os.path.abspath(entry)).startswith(_os.path.normcase(_BACKEND_DIR)):
                return False
        return True
    return False


for _name in list(_sys.modules.keys()):
    if not _name.startswith("backend"):
        continue
    if _name == "backend.tests" or _name.startswith("backend.tests."):
        continue
    _mod = _sys.modules.get(_name)
    if _mod is not None and _is_stub(_mod):
        _sys.modules.pop(_name, None)

import pytest
import httpx

from backend import app as app_module
from backend.services import research_provider as research_provider_module
from backend.services import transcript_worker as transcript_worker_module
from backend.services.user_priority_service import user_priority_service


def _purge_backend_stubs():
    """Drop any backend.* stub modules other tests have installed and ensure
    the real ``backend`` / ``backend.services`` packages are present so
    ``importlib`` can resolve submodules on demand.
    """
    for name in list(_sys.modules.keys()):
        if not name.startswith("backend"):
            continue
        if name == "backend.tests" or name.startswith("backend.tests."):
            continue
        mod = _sys.modules.get(name)
        if mod is not None and _is_stub(mod):
            _sys.modules.pop(name, None)
    # Reinstate the real ``backend`` and ``backend.services`` parent packages
    # so deferred ``from backend.X import Y`` lookups work.
    import importlib as _importlib
    for _pkg in ("backend", "backend.services"):
        _mod = _sys.modules.get(_pkg)
        if _mod is None or _is_stub(_mod):
            _sys.modules.pop(_pkg, None)
            _importlib.import_module(_pkg)


@pytest.fixture(autouse=True)
def _restore_real_backend_modules():
    """Before every test in this file, drop any backend.* stub modules that
    other test files may have installed in sys.modules. The module-level
    imports above already captured the real implementations into the
    ``app_module`` / ``research_provider_module`` / ``transcript_worker_module``
    / ``user_priority_service`` globals before any test runs, so we just need
    to make sure ``monkeypatch.setattr("backend.…")`` and similar string-based
    lookups also resolve to the real modules.
    """
    _purge_backend_stubs()
    yield


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


@pytest.mark.anyio
async def test_login_response_does_not_expose_session_credentials(monkeypatch):
    password_hash = app_module.hash_password("super-secret")

    def fake_execute_one(query, params=None):
        normalized = " ".join(str(query).strip().lower().split())
        if normalized.startswith("select id, password_hash from users where email = %s"):
            return {"id": 7, "password_hash": password_hash}
        raise AssertionError(f"Unexpected execute_one query: {query}")

    monkeypatch.setattr("backend.db.db.execute_one", fake_execute_one)
    monkeypatch.setattr("backend.app.create_session", lambda user_id: f"session-{user_id}")

    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "super-secret"},
        )

    assert response.status_code == 200
    assert response.json() == {"user_id": 7}
    assert response.cookies.get("session_id") == "session-7"


@pytest.mark.anyio
async def test_security_headers_include_csp_and_hsts(monkeypatch):
    def fake_execute_query(query, params=None):
        return [{"ok": 1}]

    monkeypatch.setattr("backend.db.db.execute_query", fake_execute_query)

    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health", headers={"X-Forwarded-Proto": "https"})

    assert response.headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
