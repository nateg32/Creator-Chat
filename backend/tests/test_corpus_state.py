from backend.services import corpus_state


class _FakeDb:
    def __init__(self, row=None, update_count=0, fail_first_update=False):
        self.row = row
        self.update_count = update_count
        self.fail_first_update = fail_first_update
        self.calls = []
        self.update_calls = []

    def execute_one(self, query, params):
        self.calls.append((query, params))
        return self.row

    def execute_update(self, query, params):
        self.update_calls.append((query, params))
        if self.fail_first_update and len(self.update_calls) == 1:
            raise Exception("missing column")
        return self.update_count


def test_find_existing_document_for_scrape_item_accepts_prefixed_and_legacy_ids(monkeypatch):
    fake_db = _FakeDb(row={"id": 12, "chunk_count": 3})
    monkeypatch.setattr(corpus_state, "db", fake_db)

    row = corpus_state.find_existing_document_for_scrape_item(
        33,
        {
            "id": "item-1",
            "source_url": "https://youtube.com/watch?v=abc",
            "metadata": {"content_id": "abc", "platform": "youtube"},
        },
    )

    assert row["id"] == 12
    params = fake_db.calls[0][1]
    assert "abc" in params[-1]
    assert "33:abc" in params[-1]


def test_scrape_item_needs_reingest_when_document_has_no_chunks(monkeypatch):
    monkeypatch.setattr(corpus_state, "db", _FakeDb(row={"id": 12, "chunk_count": 0}))

    assert not corpus_state.scrape_item_has_searchable_document(
        33,
        {
            "id": "item-1",
            "source_url": "https://youtube.com/watch?v=abc",
            "metadata": {"content_id": "abc"},
        },
    )


def test_scrape_item_is_searchable_when_document_has_chunks(monkeypatch):
    monkeypatch.setattr(corpus_state, "db", _FakeDb(row={"id": 12, "chunk_count": 2}))

    assert corpus_state.scrape_item_has_searchable_document(
        33,
        {
            "id": "item-1",
            "source_url": "https://youtube.com/watch?v=abc",
            "metadata": {"content_id": "abc"},
        },
    )


def test_compact_document_content_keeps_preview_not_full_transcript():
    long_text = " ".join(["actual transcript sentence"] * 200)

    compact = corpus_state.compact_document_content_for_storage(
        title="How to Scale",
        platform="youtube",
        source_url="https://youtube.com/watch?v=abc",
        text_content=long_text,
        limit=240,
    )

    assert compact.startswith("How to Scale | youtube | https://youtube.com/watch?v=abc")
    assert len(compact) < len(long_text)
    assert compact.endswith("...")


def test_apply_chunked_storage_metadata_records_storage_policy():
    metadata = corpus_state.apply_chunked_storage_metadata(
        {"ingest_checksum": "abc"},
        text_content="full text",
        document_content="preview",
        chunk_size=1000,
        chunk_overlap=80,
    )

    assert metadata["ingest_checksum"] == "abc"
    assert metadata["storage_policy"] == corpus_state.DOCUMENT_STORAGE_POLICY
    assert metadata["full_text_available_in"] == "chunks"
    assert metadata["source_text_char_count"] == len("full text")


def test_prune_scrape_item_transcripts_falls_back_without_duplicate_column(monkeypatch):
    fake_db = _FakeDb(update_count=3, fail_first_update=True)
    monkeypatch.setattr(corpus_state, "db", fake_db)

    assert corpus_state.prune_scrape_item_transcripts_after_review("search-1") == 3
    assert len(fake_db.update_calls) == 2
    assert "review_status = 'denied'" in fake_db.update_calls[1][0]
