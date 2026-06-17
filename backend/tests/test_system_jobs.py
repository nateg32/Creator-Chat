from backend.services.system_jobs import make_job_dedupe_key


def test_fingerprint_dedupe_key_is_creator_scoped():
    assert make_job_dedupe_key(
        creator_id=7,
        job_type="FINGERPRINT",
        payload={"creator_id": 7, "mode": "incremental"},
    ) == "creator:7:fingerprint:incremental:current"


def test_ingest_dedupe_key_is_search_scoped():
    assert make_job_dedupe_key(
        creator_id=7,
        job_type="INGEST",
        payload={"search_id": "abc"},
    ) == "creator:7:ingest:abc"


def test_scrape_dedupe_key_uses_search_run_id():
    assert make_job_dedupe_key(
        creator_id=7,
        job_type="SCRAPE",
        payload={"search_run_id": "run-1"},
    ) == "creator:7:scrape:run-1"
