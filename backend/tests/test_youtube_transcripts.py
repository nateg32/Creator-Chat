import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import types


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    module_path = BACKEND_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.modules.setdefault("backend.settings", SimpleNamespace(settings=SimpleNamespace(APIFY_TOKEN="test-token")))
# If backend.services isn't loaded yet, install a lightweight package stub but
# point __path__ at the REAL services directory so submodule auto-imports keep
# working for downstream tests (otherwise test_security_hardening can't find
# backend.services.transcript_worker).
if "backend.services" not in sys.modules:
    backend_services_pkg = types.ModuleType("backend.services")
    backend_services_pkg.__path__ = [str(BACKEND_ROOT / "services")]  # type: ignore[attr-defined]
    sys.modules["backend.services"] = backend_services_pkg
transcript_quality_module = _load_module("backend.services.transcript_quality", "services/transcript_quality.py")
sys.modules.setdefault("backend.services.transcript_quality", transcript_quality_module)
apify_service = _load_module("apify_service_youtube_tests", "apify_service.py")


class YouTubeTranscriptBatchTests(unittest.TestCase):
    def test_batch_defers_video_transcripts_to_assemblyai(self):
        items = [
            {
                "platform": "youtube",
                "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
                "transcript_status": "missing",
                "transcript": "",
            },
            {
                "platform": "tiktok",
                "source_url": "https://www.tiktok.com/@creator/video/123",
                "transcript_status": "missing",
                "transcript": "",
            },
        ]

        with patch.object(apify_service, "get_apify_token", return_value="token") as token_mock, \
             patch.object(
                 apify_service,
                 "_extract_youtube_native_transcripts",
                 return_value={"https://www.youtube.com/watch?v=abcdefghijk": "native youtube transcript with the actual spoken steps and enough detail to count as a real caption track"},
             ) as native_mock, \
             patch.object(
                 apify_service,
                 "_extract_transcripts_invideoiq",
                 return_value={"https://www.tiktok.com/@creator/video/123": "tiktok transcript with enough detail to count as a real recovered transcript for the video"},
             ) as actor_mock:
            result = apify_service.batch_extract_all_transcripts(items)

        self.assertEqual(result[0]["transcript"], "")
        self.assertEqual(result[0]["transcript_status"], "pending")
        self.assertEqual(result[0]["metadata"]["transcript_source"], "whisper_assemblyai_deferred")
        self.assertEqual(result[1]["transcript"], "")
        self.assertEqual(result[1]["transcript_status"], "pending")
        token_mock.assert_not_called()
        native_mock.assert_not_called()
        actor_mock.assert_not_called()

    def test_youtube_transcripts_are_disabled_for_apify(self):
        urls = [
            "https://www.youtube.com/watch?v=abcdefghijk",
            "https://www.youtube.com/shorts/lmnopqrstuv",
        ]

        with patch.object(
            apify_service,
            "_extract_youtube_native_transcripts",
            return_value={urls[0]: "native one", urls[1]: "native two"},
        ) as native_mock, patch.object(apify_service, "_extract_transcripts_invideoiq") as actor_mock:
            result = apify_service._extract_youtube_transcripts(urls, "token")

        self.assertEqual(result, {})
        native_mock.assert_not_called()
        actor_mock.assert_not_called()

    def test_transcript_alias_matching_handles_canonicalized_social_urls(self):
        alias_map = apify_service._build_transcript_alias_map(
            ["https://www.instagram.com/reel/ABC123/?utm_source=ig_web_copy_link"],
            "instagram",
        )

        matches = apify_service._resolve_transcript_matches(
            alias_map,
            ["https://instagram.com/reel/ABC123"],
            "instagram",
        )

        self.assertEqual(matches, ["https://www.instagram.com/reel/ABC123/?utm_source=ig_web_copy_link"])

    def test_batch_defers_instagram_transcripts_to_assemblyai(self):
        instagram_url = "https://www.instagram.com/reel/ABC123/"
        items = [
            {
                "platform": "instagram",
                "source_url": instagram_url,
                "transcript_status": "missing",
                "transcript": "",
            },
        ]

        with patch.object(apify_service, "get_apify_token", return_value="token"), \
             patch.object(apify_service, "_extract_youtube_native_transcripts", return_value={}), \
             patch.object(apify_service, "_extract_transcripts_invideoiq", return_value={}) as actor_mock, \
             patch.object(
                 apify_service,
                 "_extract_social_transcripts",
                 return_value={instagram_url: "instagram transcript with enough detail to count as a usable recovered reel transcript, including the actual spoken points from the clip"},
             ) as social_mock:
            result = apify_service.batch_extract_all_transcripts(items)

        self.assertEqual(result[0]["transcript"], "")
        self.assertEqual(result[0]["transcript_status"], "pending")
        self.assertEqual(result[0]["metadata"]["transcript_source"], "whisper_assemblyai_deferred")
        actor_mock.assert_not_called()
        social_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
