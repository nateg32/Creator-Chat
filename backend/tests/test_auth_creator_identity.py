import json
from datetime import datetime, timezone

import httpx
import pytest

from backend.app import app, create_access_token
from backend.db import db


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_register_and_login_normalize_email(monkeypatch):
    records = {"users": {}, "next_user_id": 1}

    def fake_execute_one(query, params=None):
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("select id from users where email = %s"):
            email = params[0]
            user = records["users"].get(email)
            return {"id": user["id"]} if user else None
        if normalized.startswith("select id, password_hash from users where email = %s"):
            email = params[0]
            user = records["users"].get(email)
            return {"id": user["id"], "password_hash": user["password_hash"]} if user else None
        raise AssertionError(f"Unexpected execute_one query: {query}")

    def fake_execute_insert(query, params=None):
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("insert into users (email, password_hash) values (%s, %s) returning id"):
            email, password_hash = params
            user_id = records["next_user_id"]
            records["next_user_id"] += 1
            records["users"][email] = {"id": user_id, "email": email, "password_hash": password_hash}
            return user_id
        raise AssertionError(f"Unexpected execute_insert query: {query}")

    def fake_execute_update(query, params=None):
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("insert into sessions"):
            return 1
        raise AssertionError(f"Unexpected execute_update query: {query}")

    monkeypatch.setattr(db, "execute_one", fake_execute_one)
    monkeypatch.setattr(db, "execute_insert", fake_execute_insert)
    monkeypatch.setattr(db, "execute_update", fake_execute_update)
    monkeypatch.setattr("backend.app.create_session", lambda user_id: f"session-{user_id}")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        register = await client.post(
            "/auth/register",
            json={"email": "  User@Example.COM  ", "password": "super-secret"},
        )
        login = await client.post(
            "/auth/login",
            json={"email": "USER@example.com", "password": "super-secret"},
        )
    assert register.status_code == 200
    assert records["users"]["user@example.com"]["email"] == "user@example.com"
    assert login.status_code == 200
    assert login.json()["user_id"] == records["users"]["user@example.com"]["id"]
    assert "session_id" not in register.json()
    assert "access_token" not in register.json()
    assert "session_id" not in login.json()
    assert "access_token" not in login.json()


@pytest.mark.anyio
async def test_creator_handle_is_unique_per_user(monkeypatch):
    created_at = datetime.now(timezone.utc)
    users = {
        1: {"id": 1, "email": "one@example.com"},
        2: {"id": 2, "email": "two@example.com"},
    }
    creators = []

    def fake_execute_one(query, params=None):
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("select id, email from users where id = %s"):
            return users.get(int(params[0]))
        if normalized.startswith("select id from creators where user_id = %s and handle = %s limit 1"):
            user_id, handle = params
            for creator in creators:
                if creator["user_id"] == user_id and creator["handle"] == handle:
                    return {"id": creator["id"]}
            return None
        if normalized.startswith("select count(*) as count from creators where user_id = %s"):
            user_id = int(params[0])
            return {"count": sum(1 for creator in creators if creator["user_id"] == user_id)}
        raise AssertionError(f"Unexpected execute_one query: {query}")

    def fake_execute_query(query, params=None):
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("insert into creators (user_id, name, handle, platforms) values (%s, %s, %s, %s) returning id, name, handle, platforms, created_at"):
            user_id, name, handle, platforms = params
            creator_id = len(creators) + 1
            row = {
                "id": creator_id,
                "user_id": user_id,
                "name": name,
                "handle": handle,
                "platforms": [],
                "created_at": created_at,
            }
            creators.append(row)
            return [row]
        raise AssertionError(f"Unexpected execute_query query: {query}")

    monkeypatch.setattr(db, "execute_one", fake_execute_one)
    monkeypatch.setattr(db, "execute_query", fake_execute_query)

    token_one = create_access_token(1, users[1]["email"])
    token_two = create_access_token(2, users[2]["email"])

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.post(
            "/creators",
            headers={"Authorization": f"Bearer {token_one}"},
            json={"name": "Alex Creator", "handle": "@SameHandle", "platforms": []},
        )
        duplicate_same_user = await client.post(
            "/creators",
            headers={"Authorization": f"Bearer {token_one}"},
            json={"name": "Another Alex", "handle": "samehandle", "platforms": []},
        )
        same_handle_other_user = await client.post(
            "/creators",
            headers={"Authorization": f"Bearer {token_two}"},
            json={"name": "Different Owner", "handle": "samehandle", "platforms": []},
        )
    assert first.status_code == 200
    assert first.json()["handle"] == "samehandle"
    assert duplicate_same_user.status_code == 409
    assert same_handle_other_user.status_code == 200
    assert len(creators) == 2


@pytest.mark.anyio
async def test_creator_config_handle_stays_user_scoped(monkeypatch):
    created_at = datetime.now(timezone.utc)
    users = {
        1: {"id": 1, "email": "one@example.com"},
        2: {"id": 2, "email": "two@example.com"},
    }
    creators = [
        {
            "id": 1,
            "user_id": 1,
            "name": "Existing Creator",
            "handle": "samehandle",
            "platform_configs": {"youtube": {"url": "https://youtube.com/@existing"}},
            "style_fingerprint": {},
            "created_at": created_at,
            "youtube_channel_id": None,
            "youtube_handle": None,
            "official_domains": [],
            "course_domains": [],
            "course_base_urls": [],
        }
    ]

    def fake_execute_one(query, params=None):
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("select id, email from users where id = %s"):
            return users.get(int(params[0]))
        if normalized.startswith("select id, platform_configs from creators where user_id = %s and handle = %s limit 1"):
            user_id, handle = params
            for creator in creators:
                if creator["user_id"] == user_id and creator["handle"] == handle:
                    return {"id": creator["id"], "platform_configs": creator["platform_configs"]}
            return None
        if normalized.startswith("select count(*) as count from creators where user_id = %s"):
            user_id = int(params[0])
            return {"count": sum(1 for creator in creators if creator["user_id"] == user_id)}
        if normalized.startswith("select id, handle, name as display_name, platform_configs, style_fingerprint, created_at, youtube_channel_id, youtube_handle, official_domains, course_domains, course_base_urls from creators where id = %s and user_id = %s"):
            creator_id = int(params[0])
            user_id = int(params[1])
            for creator in creators:
                if creator["id"] == creator_id and creator["user_id"] == user_id:
                    return {
                        "id": creator["id"],
                        "handle": creator["handle"],
                        "display_name": creator["name"],
                        "platform_configs": creator["platform_configs"],
                        "style_fingerprint": creator["style_fingerprint"],
                        "created_at": creator["created_at"],
                        "youtube_channel_id": creator["youtube_channel_id"],
                        "youtube_handle": creator["youtube_handle"],
                        "official_domains": creator["official_domains"],
                        "course_domains": creator["course_domains"],
                        "course_base_urls": creator["course_base_urls"],
                    }
            return None
        raise AssertionError(f"Unexpected execute_one query: {query}")

    def fake_execute_update(query, params=None):
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("update creators set name = %s where id = %s"):
            name, creator_id = params
            for creator in creators:
                if creator["id"] == creator_id:
                    creator["name"] = name
                    return 1
            return 0
        if normalized.startswith("update creators set name = %s, platform_configs = %s where id = %s"):
            name, platform_configs_json, creator_id = params
            for creator in creators:
                if creator["id"] == creator_id:
                    creator["name"] = name
                    creator["platform_configs"] = json.loads(platform_configs_json)
                    return 1
            return 0
        if normalized.startswith("update creators set name = %s, platform_configs = %s, config_version = config_version + 1 where id = %s"):
            name, platform_configs_json, creator_id = params
            for creator in creators:
                if creator["id"] == creator_id:
                    creator["name"] = name
                    creator["platform_configs"] = json.loads(platform_configs_json)
                    return 1
            return 0
        raise AssertionError(f"Unexpected execute_update query: {query}")

    def fake_execute_insert(query, params=None):
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("insert into creators (user_id, handle, name, profile_picture_url, youtube_channel_id, youtube_handle, official_domains, course_domains, course_base_urls, platform_configs) values"):
            user_id = params[0]
            handle = params[1]
            name = params[2]
            platform_configs_json = params[-1]
            creator_id = len(creators) + 1
            creators.append(
                {
                    "id": creator_id,
                    "user_id": user_id,
                    "name": name,
                    "handle": handle,
                    "platform_configs": json.loads(platform_configs_json),
                    "style_fingerprint": {},
                    "created_at": created_at,
                    "youtube_channel_id": None,
                    "youtube_handle": None,
                    "official_domains": [],
                    "course_domains": [],
                    "course_base_urls": [],
                }
            )
            return creator_id
        raise AssertionError(f"Unexpected execute_insert query: {query}")

    monkeypatch.setattr(db, "execute_one", fake_execute_one)
    monkeypatch.setattr(db, "execute_update", fake_execute_update)
    monkeypatch.setattr(db, "execute_insert", fake_execute_insert)
    monkeypatch.setattr("backend.app._validate_and_normalize_platform_configs", lambda configs: configs)
    monkeypatch.setattr("backend.app.autofill_creator_identity", lambda creator_id, profile: profile)
    monkeypatch.setattr("backend.app._creator_has_column", lambda column_name: column_name in {"name", "platform_configs"})
    monkeypatch.setattr("backend.app._creator_column_exists", lambda column_name: column_name == "platform_configs")
    monkeypatch.setattr("backend.app._creator_display_column", lambda: "name")
    monkeypatch.setattr("backend.app.get_creator_status", lambda creator_id: {"ready_to_chat": False, "needs_reapproval": False})

    token_one = create_access_token(1, users[1]["email"])
    token_two = create_access_token(2, users[2]["email"])
    payload = {
        "name": "Config Creator",
        "handle": "samehandle",
        "platform_configs": {"youtube": {"url": "https://youtube.com/@config-creator"}},
        "visual_config": {},
        "official_domains": [],
        "course_domains": [],
        "course_base_urls": [],
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        same_user = await client.post(
            "/creators/config",
            headers={"Authorization": f"Bearer {token_one}"},
            json=payload,
        )
        other_user = await client.post(
            "/creators/config",
            headers={"Authorization": f"Bearer {token_two}"},
            json=payload,
        )

    assert same_user.status_code == 200
    assert same_user.json()["id"] == 1
    assert creators[0]["platform_configs"]["youtube"]["url"] == "https://youtube.com/@config-creator"
    assert other_user.status_code == 200
    assert other_user.json()["id"] == 2
    assert len(creators) == 2
