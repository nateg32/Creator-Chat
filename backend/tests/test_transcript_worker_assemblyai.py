import json
import os
import tempfile
import unittest
from unittest.mock import patch

from backend.services import transcript_worker


class AssemblyAITranscriptWorkerTests(unittest.TestCase):
    def _temp_media_file(self):
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        handle.write(b"fake media")
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        return handle.name

    def test_hybrid_transcription_uses_assemblyai_formatted_text_with_whisper_available(self):
        media_path = self._temp_media_file()
        with patch.object(
            transcript_worker,
            "_assemblyai_enrich_file",
            return_value={
                "assemblyai_transcript_id": "asm_123",
                "assemblyai_text": "assembly transcript text",
                "assemblyai_srt": "1\n00:00:00,000 --> 00:00:01,000\nassembly transcript text",
                "assemblyai_vtt": "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nassembly transcript text",
                "caption_source": "assemblyai",
            },
        ) as assembly_mock, patch.object(
            transcript_worker,
            "_transcribe_file_with_openai",
            return_value="whisper transcript text",
        ) as openai_mock:
            result = transcript_worker.transcribe_with_hybrid(media_path)

        self.assertEqual(result["source"], "WHISPER_ASR_ASSEMBLYAI_FORMATTED")
        self.assertEqual(result["text"], "assembly transcript text")
        self.assertEqual(result["metadata"]["raw_transcript_source"], "openai_whisper")
        self.assertEqual(result["metadata"]["formatted_transcript_source"], "assemblyai")
        self.assertEqual(result["metadata"]["caption_source"], "assemblyai")
        assembly_mock.assert_called_once_with(media_path)
        openai_mock.assert_called_once_with(media_path)

    def test_hybrid_transcription_keeps_whisper_when_assemblyai_has_no_text(self):
        media_path = self._temp_media_file()
        with patch.object(
            transcript_worker,
            "_assemblyai_enrich_file",
            return_value={"assemblyai_error": "timeout"},
        ), patch.object(
            transcript_worker,
            "_transcribe_file_with_openai",
            return_value="openai fallback transcript",
        ) as openai_mock:
            result = transcript_worker.transcribe_with_hybrid(media_path)

        self.assertEqual(result["source"], "WHISPER_ASR")
        self.assertEqual(result["text"], "openai fallback transcript")
        self.assertEqual(result["metadata"]["assemblyai_error"], "timeout")
        openai_mock.assert_called_once_with(media_path)

    def test_process_job_replaces_scraper_transcript_with_assemblyai_metadata(self):
        good_transcript = " ".join(["actual spoken content"] * 80)
        with patch.object(transcript_worker, "synthesize_media_url", return_value="https://cdn.example.com/video.mp4"), \
             patch.object(
                 transcript_worker,
                 "transcribe_with_hybrid",
                 return_value={
                     "text": good_transcript,
                     "source": "WHISPER_ASR_ASSEMBLYAI_FORMATTED",
                     "metadata": {
                         "assemblyai_transcript_id": "asm_456",
                         "assemblyai_srt": "1\n00:00:00,000 --> 00:00:01,000\nactual spoken content",
                         "assemblyai_vtt": "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nactual spoken content",
                         "caption_source": "assemblyai",
                     },
                 },
             ), patch.object(transcript_worker, "get_usable_transcript_asset", return_value=None), \
             patch.object(transcript_worker, "upsert_transcript_asset", return_value=None), \
             patch.object(transcript_worker.db, "execute_update") as update_mock:
            transcript_worker.process_transcript_job(
                "00000000-0000-0000-0000-000000000001",
                "https://www.youtube.com/watch?v=abcdefghijk",
                "youtube",
                caption="",
                metadata={"title": "Test video", "transcript_source": "scraper"},
                existing_transcript=" ".join(["scraped caption"] * 80),
            )

        update_mock.assert_called_once()
        params = update_mock.call_args.args[1]
        self.assertEqual(params[0], good_transcript)
        persisted_metadata = json.loads(params[1])
        self.assertEqual(persisted_metadata["transcript_source"], "whisper_asr_assemblyai_formatted")
        self.assertEqual(persisted_metadata["assemblyai_transcript_id"], "asm_456")
        self.assertEqual(persisted_metadata["caption_source"], "assemblyai")
        self.assertTrue(persisted_metadata["previous_scraper_transcript_replaced"])


if __name__ == "__main__":
    unittest.main()
