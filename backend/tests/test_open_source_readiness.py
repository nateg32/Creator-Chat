from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_auth_migration_does_not_seed_placeholder_user():
    sql = (REPO_ROOT / "backend" / "migrations" / "002_auth_creators.sql").read_text(encoding="utf-8")

    assert "default@example.com" not in sql
    assert "$2b$12$placeholder" not in sql
    assert "Create a real user" in sql


def test_local_database_defaults_match_documented_setup():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    env_example = (REPO_ROOT / "backend" / "env.example").read_text(encoding="utf-8")
    settings_py = (REPO_ROOT / "backend" / "settings.py").read_text(encoding="utf-8")

    assert "CREATE DATABASE creator_chat;" in readme
    assert "DB_PORT=5432" in env_example
    assert "DB_NAME=creator_chat" in env_example
    assert 'os.getenv("DB_PORT", os.getenv("PGPORT", "5432"))' in settings_py
    assert 'os.getenv("DB_NAME", os.getenv("PGDATABASE", "creator_chat"))' in settings_py


def test_docs_and_blueprint_expose_optional_provider_envs():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    render_yaml = (REPO_ROOT / "render.yaml").read_text(encoding="utf-8")

    for token in (
        "TRANSCRIPTION_API_KEY",
        "ASSEMBLYAI_API_KEY",
        "SEARCH_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "THREAD_CONTEXT_CACHE_REDIS_URL",
    ):
        assert token in readme
        assert f"- key: {token}" in render_yaml
