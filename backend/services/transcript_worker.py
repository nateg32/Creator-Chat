import traceback
import os
import ipaddress
import socket
from typing import List, Dict, Any, Optional
from backend.db import db
import tempfile
import subprocess
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from backend.services.transcript_quality import assess_transcript_quality
from backend.services.transcript_assets import (
    apply_transcript_asset_to_metadata,
    get_usable_transcript_asset,
    upsert_transcript_asset,
)


def _ensure_search_progress_table():
    try:
        db.execute_update("""
            CREATE TABLE IF NOT EXISTS search_progress (
                search_id UUID PRIMARY KEY,
                progress_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    except Exception:
        pass


def _get_search_progress(search_id: str):
    try:
        row = db.execute_one(
            "SELECT progress_data FROM search_progress WHERE search_id = %s",
            (search_id,),
        )
        if not row:
            return None
        data = row.get("progress_data")
        if isinstance(data, str):
            data = json.loads(data)
        return dict(data) if isinstance(data, dict) else None
    except Exception:
        return None


def _set_search_progress(search_id: str, data: Dict[str, Any]):
    try:
        _ensure_search_progress_table()
        db.execute_update(
            """
            INSERT INTO search_progress (search_id, progress_data, updated_at)
            VALUES (%s::uuid, %s::jsonb, NOW())
            ON CONFLICT (search_id) DO UPDATE SET
                progress_data = EXCLUDED.progress_data,
                updated_at = NOW()
            """,
            (search_id, json.dumps(data, default=str)),
        )
    except Exception:
        pass


def _is_safe_remote_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    if host in {"localhost", "localhost.localdomain"}:
        return False
    try:
        return not ipaddress.ip_address(host).is_private
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False
        for item in resolved:
            raw_addr = (item[4] or [""])[0].split("%", 1)[0]
            try:
                addr = ipaddress.ip_address(raw_addr)
            except ValueError:
                continue
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_unspecified:
                return False
        return True

def synthesize_media_url(source_url: str, platform: str) -> Optional[str]:
    """Attempt to get an actual media URL if needed. For now, rely on yt-dlp if available."""
    if not _is_safe_remote_url(source_url):
        return None
    try:
        # Use yt-dlp to extract the actual direct media url
        result = subprocess.run(
            ["yt-dlp", "-f", "bestaudio/best", "-g", source_url],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            candidate = result.stdout.strip().split('\n')[0]
            return candidate if _is_safe_remote_url(candidate) else None
    except Exception as e:
        print(f"yt-dlp extract failed for {source_url}: {e}")
    return source_url


def _looks_like_direct_media_url(url: str) -> bool:
    lowered = (url or "").lower()
    if not lowered:
        return False
    if any(ext in lowered for ext in [".mp4", ".mp3", ".wav", ".m4a", ".ogg", ".webm", ".mov"]):
        return True
    if any(host in lowered for host in ["googlevideo.com", ".cdninstagram.com", ".fbcdn.net", "akamaized.net", "cloudfront.net"]):
        return True
    return False


_VIDEO_PLATFORMS = {"youtube", "tiktok", "instagram"}
_TRANSCRIBER_SOURCES = {"assemblyai", "assemblyai_asr", "whisper", "whisper_asr", "openai_whisper"}


def _metadata_dict(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata) if metadata else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return metadata if isinstance(metadata, dict) else {}


def _existing_transcript_is_transcriber_owned(metadata: Dict[str, Any], source: str) -> bool:
    lowered_source = str(source or metadata.get("transcript_source") or "").lower()
    if metadata.get("assemblyai_transcript_id"):
        return True
    return any(marker in lowered_source for marker in _TRANSCRIBER_SOURCES)


def _media_candidates(source_url: str, metadata: Dict[str, Any]) -> List[str]:
    candidates = [
        metadata.get("direct_video_url"),
        metadata.get("media_url"),
        metadata.get("video_url"),
        metadata.get("videoUrl"),
        metadata.get("video"),
        metadata.get("playAddr"),
        metadata.get("downloadAddr"),
        metadata.get("url"),
        source_url,
    ]
    seen = set()
    ordered = []
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value and value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered

def _download_media_to_temp(media_url_or_path: str) -> Optional[str]:
    """Download a remote media URL to a local temp file, or return a local path."""
    if not media_url_or_path:
        return None
    if not str(media_url_or_path).startswith("http"):
        return media_url_or_path if os.path.exists(media_url_or_path) else None

    if not _is_safe_remote_url(media_url_or_path):
        return None

    try:
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(media_url_or_path, headers=headers, timeout=45, stream=True)
        response.raise_for_status()

        suffix = ".mp4"
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "audio/mpeg" in content_type:
            suffix = ".mp3"
        elif "audio" in content_type:
            suffix = ".m4a"
        elif "webm" in content_type:
            suffix = ".webm"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    tmp_file.write(chunk)
            return tmp_file.name
    except Exception as e:
        print(f"[TRANSCRIPT] Media download failed: {e}")
        return None


def _transcribe_file_with_openai(file_path: str) -> Optional[str]:
    """Run OpenAI/Whisper transcription on a local media file."""
    from backend.settings import settings
    if not (settings.TRANSCRIPTION_API_KEY or settings.OPENAI_API_KEY):
        print("TRANSCRIPTION_API_KEY / OPENAI_API_KEY not set")
        return None

    try:
        from openai import OpenAI

        kwargs = {"api_key": settings.TRANSCRIPTION_API_KEY or settings.OPENAI_API_KEY}
        if settings.TRANSCRIPTION_BASE_URL:
            kwargs["base_url"] = settings.TRANSCRIPTION_BASE_URL
        client = OpenAI(**kwargs)
        with open(file_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model=settings.TRANSCRIPTION_MODEL or "whisper-1",
                file=audio_file,
                response_format="text",
            )
        return transcript if isinstance(transcript, str) else transcript.text
    except Exception as e:
        print(f"[TRANSCRIPT] OpenAI transcription error: {e}")
        return None


def _assemblyai_enrich_file(file_path: str) -> Dict[str, Any]:
    """Upload media to AssemblyAI and return transcript, captions, and enrichment metadata."""
    from backend.settings import settings

    api_key = (settings.ASSEMBLYAI_API_KEY or "").strip()
    if not api_key:
        return {"assemblyai_error": "missing_api_key"}

    try:
        import requests

        headers = {"authorization": api_key}
        with open(file_path, "rb") as audio_file:
            upload_response = requests.post(
                "https://api.assemblyai.com/v2/upload",
                headers=headers,
                data=audio_file,
                timeout=90,
            )
        upload_response.raise_for_status()
        upload_url = (upload_response.json() or {}).get("upload_url")
        if not upload_url:
            return {}

        transcript_payload = {
            "audio_url": upload_url,
            "punctuate": True,
            "format_text": True,
            "language_detection": True,
        }
        if settings.ASSEMBLYAI_ENRICHMENT_ENABLED:
            transcript_payload.update({
                "auto_chapters": True,
                "iab_categories": True,
                "entity_detection": True,
            })
        create_response = requests.post(
            "https://api.assemblyai.com/v2/transcript",
            headers={**headers, "content-type": "application/json"},
            json=transcript_payload,
            timeout=30,
        )
        create_response.raise_for_status()
        transcript_id = (create_response.json() or {}).get("id")
        if not transcript_id:
            return {}

        deadline = time.time() + float(settings.ASSEMBLYAI_TRANSCRIPT_TIMEOUT_SECONDS or 180)
        poll_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
        while time.time() < deadline:
            poll_response = requests.get(poll_url, headers=headers, timeout=20)
            poll_response.raise_for_status()
            data = poll_response.json() or {}
            status = data.get("status")
            if status == "completed":
                chapters = data.get("chapters") or []
                categories = ((data.get("iab_categories_result") or {}).get("summary") or {})
                result = {
                    "assemblyai_transcript_id": transcript_id,
                    "assemblyai_text": (data.get("text") or "").strip(),
                    "assemblyai_summary": data.get("summary") or "",
                    "detected_language": data.get("language_code") or "",
                    "assemblyai_chapters": chapters[:12],
                    "assemblyai_topics": sorted(categories.keys())[:12] if isinstance(categories, dict) else [],
                    "assemblyai_entities": (data.get("entities") or [])[:20],
                    "assemblyai_enriched": bool(settings.ASSEMBLYAI_ENRICHMENT_ENABLED),
                }
                if settings.ASSEMBLYAI_CAPTIONS_ENABLED:
                    chars_per_caption = max(1, int(settings.ASSEMBLYAI_CHARS_PER_CAPTION or 42))
                    for subtitle_format, metadata_key in (("srt", "assemblyai_srt"), ("vtt", "assemblyai_vtt")):
                        try:
                            subtitle_response = requests.get(
                                f"https://api.assemblyai.com/v2/transcript/{transcript_id}/{subtitle_format}",
                                headers=headers,
                                params={"chars_per_caption": chars_per_caption},
                                timeout=30,
                            )
                            if subtitle_response.ok and subtitle_response.text.strip():
                                result[metadata_key] = subtitle_response.text.strip()
                        except Exception as caption_error:
                            result[f"{metadata_key}_error"] = str(caption_error)
                    if result.get("assemblyai_srt") or result.get("assemblyai_vtt"):
                        result["caption_source"] = "assemblyai"
                return result
            if status == "error":
                print(f"[TRANSCRIPT] AssemblyAI error: {data.get('error')}")
                return {"assemblyai_error": data.get("error") or "unknown"}
            time.sleep(3)

        return {"assemblyai_error": "timeout"}
    except Exception as e:
        print(f"[TRANSCRIPT] AssemblyAI enrichment failed: {e}")
        return {"assemblyai_error": str(e)}


def transcribe_with_hybrid(media_url_or_path: str) -> Dict[str, Any]:
    """Whisper-first transcription with AssemblyAI enrichment/captions around the same media."""
    temp_path = _download_media_to_temp(media_url_or_path)
    if not temp_path:
        return {"text": "", "source": "none", "metadata": {}}

    should_delete = str(media_url_or_path).startswith("http")
    try:
        raw_text = (_transcribe_file_with_openai(temp_path) or "").strip()
        enrichment = _assemblyai_enrich_file(temp_path)
        assembly_text = str(enrichment.get("assemblyai_text") or "").strip()
        final_text = assembly_text or raw_text
        if not final_text:
            return {"text": "", "source": "none", "metadata": enrichment}

        if raw_text and assembly_text:
            source = "WHISPER_ASR_ASSEMBLYAI_FORMATTED"
        elif raw_text:
            source = "WHISPER_ASR"
        else:
            source = "ASSEMBLYAI_FALLBACK"
        return {
            "text": final_text,
            "source": source,
            "metadata": {
                **enrichment,
                "raw_transcript_source": "openai_whisper" if raw_text else "assemblyai",
                "formatted_transcript_source": "assemblyai" if assembly_text else "openai_whisper",
                "openai_raw_transcript_available": bool(raw_text),
            },
        }
    finally:
        if should_delete and temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def transcribe_with_whisper(media_url_or_path: str) -> Optional[str]:
    """Backward-compatible OpenAI-only helper."""
    result = transcribe_with_hybrid(media_url_or_path)
    return result.get("text") or None

def process_transcript_job(item_id: str, source_url: str, platform: str, caption: str = "", metadata: Optional[Dict[str, Any]] = None, existing_transcript: str = ""):
    """Processes a single item's transcript and updates DB."""
    print(f"[TRANSCRIPT] Starting job for {item_id} ({source_url})")
    
    transcript_text = None
    status = "missing"
    source = "NONE"
    metadata = _metadata_dict(metadata)
    platform_key = (platform or "unknown").lower()
    title = str(metadata.get("title") or "")
    last_transcription_metadata: Dict[str, Any] = {}
    best_diag = assess_transcript_quality(existing_transcript, caption=caption, title=title)
    existing_source = str(metadata.get("transcript_source") or "")
    keep_existing = (
        best_diag.get("usable")
        and (
            platform_key not in _VIDEO_PLATFORMS
            or _existing_transcript_is_transcriber_owned(metadata, existing_source)
        )
    )
    if keep_existing:
        transcript_text = existing_transcript
        source = existing_source or "EXISTING"
        status = "present"
    elif existing_transcript and platform_key in _VIDEO_PLATFORMS:
        metadata["previous_scraper_transcript_replaced"] = True

    if not transcript_text:
        cached_asset = get_usable_transcript_asset(
            source_url,
            platform_key,
            caption=caption,
            title=title,
        )
        if cached_asset:
            transcript_text = str(cached_asset.get("transcript") or "").strip()
            status = "present"
            source = str((cached_asset.get("metadata") or {}).get("transcript_source") or "TRANSCRIPT_ASSET")
            metadata = apply_transcript_asset_to_metadata(metadata, cached_asset)
            best_diag = cached_asset.get("quality") or assess_transcript_quality(
                transcript_text,
                caption=caption,
                title=title,
            )

    def consider(candidate_text: str, candidate_source: str):
        nonlocal transcript_text, best_diag, source, status
        diagnostics = assess_transcript_quality(candidate_text, caption=caption, title=title)
        if not diagnostics.get("usable"):
            return
        if transcript_text and diagnostics.get("score", 0.0) < best_diag.get("score", 0.0):
            return
        transcript_text = candidate_text
        best_diag = diagnostics
        source = candidate_source
        status = "present"
    
    try:
        if not transcript_text:
            for direct_url in _media_candidates(source_url, metadata):

                if not _looks_like_direct_media_url(direct_url):
                    resolved_url = synthesize_media_url(direct_url, platform_key)
                    if not resolved_url or (resolved_url == direct_url and not _looks_like_direct_media_url(resolved_url)):
                        continue
                    direct_url = resolved_url

                hybrid_result = transcribe_with_hybrid(direct_url)
                hybrid_transcript = str(hybrid_result.get("text") or "").strip()
                hybrid_metadata = hybrid_result.get("metadata") or {}
                if isinstance(hybrid_metadata, dict):
                    last_transcription_metadata = hybrid_metadata
                    metadata.update(hybrid_metadata)
                if hybrid_transcript:
                    prior_text = transcript_text
                    consider(hybrid_transcript, str(hybrid_result.get("source") or "WHISPER_ASR"))
                    if transcript_text and transcript_text != prior_text:
                        break
                    
        # Update DB
        if transcript_text:
            print(f"[TRANSCRIPT] Completed {item_id} via {source}")
            transcript_metadata = {
                "transcript_source": str(source).lower(),
                "transcript_quality_score": best_diag.get("score"),
                "transcript_quality_reason": best_diag.get("reason"),
                "transcript_coverage": best_diag.get("coverage"),
                "transcript_word_count": best_diag.get("word_count"),
            }
            for key in (
                "assemblyai_transcript_id",
                "assemblyai_summary",
                "assemblyai_chapters",
                "assemblyai_topics",
                "assemblyai_entities",
                "assemblyai_enriched",
                "assemblyai_error",
                "detected_language",
                "assemblyai_srt",
                "assemblyai_vtt",
                "assemblyai_srt_error",
                "assemblyai_vtt_error",
                "caption_source",
                "raw_transcript_source",
                "formatted_transcript_source",
                "openai_raw_transcript_available",
                "previous_scraper_transcript_replaced",
            ):
                if key in metadata:
                    transcript_metadata[key] = metadata[key]
            db.execute_update(
                """
                UPDATE scrape_items
                SET transcript = %s,
                    transcript_status = 'present',
                    metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                WHERE id = %s
                """,
                (
                    transcript_text,
                    json.dumps(transcript_metadata),
                    item_id,
                )
            )
            try:
                asset_id = upsert_transcript_asset(
                    source_url=source_url,
                    platform=platform_key,
                    title=title,
                    transcript=transcript_text,
                    transcript_status="present",
                    metadata=transcript_metadata,
                )
                if asset_id:
                    db.execute_update(
                        """
                        UPDATE scrape_items
                        SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                        WHERE id = %s
                        """,
                        (
                            json.dumps({
                                "transcript_asset_id": str(asset_id),
                                "transcript_cached_globally": True,
                            }),
                            item_id,
                        ),
                    )
            except Exception as cache_error:
                print(f"[TRANSCRIPT] Transcript asset cache skipped: {cache_error}")
        else:
            failure_diag = assess_transcript_quality(existing_transcript, caption=caption, title=title)
            failure_metadata = {
                "transcript_quality_score": failure_diag.get("score"),
                "transcript_quality_reason": failure_diag.get("reason"),
                "transcript_coverage": failure_diag.get("coverage"),
                "transcript_source": "whisper_assemblyai_failed",
            }
            for key in (
                "assemblyai_error",
                "assemblyai_transcript_id",
                "raw_transcript_source",
                "formatted_transcript_source",
            ):
                if key in last_transcription_metadata:
                    failure_metadata[key] = last_transcription_metadata[key]
            db.execute_update(
                """
                UPDATE scrape_items
                SET transcript_status = %s,
                    metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                WHERE id = %s
                """,
                (
                    status,
                    json.dumps(failure_metadata),
                    item_id,
                )
            )
            
    except Exception as e:
        print(f"[TRANSCRIPT] Error on {item_id}: {e}")
        traceback.print_exc()
        db.execute_update(
            "UPDATE scrape_items SET transcript_status = 'error' WHERE id = %s",
            (item_id,)
        )

def run_transcripts_for_search(search_run_id: str):
    """Orchestrates Whisper transcription plus AssemblyAI caption/enrichment jobs for a search run."""
    print(f"[TRANSCRIPT] Starting async pipeline for search {search_run_id}")
    try:
        query = """
            SELECT id, source_url, platform, caption, transcript, transcript_status, metadata
            FROM scrape_items
            WHERE scrape_run_id = %s
              AND COALESCE(is_primary, true) = true
              AND transcript_status IN ('not_started', 'queued', 'pending', 'missing')
        """
        items = db.execute_query(query, (search_run_id,))

        if not items:
            print("[TRANSCRIPT] No items need processing")
            prog = _get_search_progress(search_run_id)
            if prog:
                prog["transcript_job_status"] = "completed"
                prog["transcript_phase"] = "done"
                _set_search_progress(search_run_id, prog)
            return

        total = len(items)
        prog = _get_search_progress(search_run_id)
        if prog:
            prog["transcript_job_status"] = "running"
            prog["transcript_phase"] = "transcripts"
            prog["transcripts_total"] = total
            prog["transcripts_done"] = 0
            prog["message"] = "Processing video audio and captions..."
            _set_search_progress(search_run_id, prog)

        for item in items:
            db.execute_update("UPDATE scrape_items SET transcript_status = 'processing' WHERE id = %s", (item["id"],))

        completed = 0
        max_workers = max(1, int(os.getenv("TRANSCRIPT_CONCURRENCY", "4")))
        with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as executor:
            futures = {
                executor.submit(
                    process_transcript_job,
                    item["id"],
                    item.get("source_url") or "",
                    item.get("platform") or "unknown",
                    item.get("caption") or "",
                    item.get("metadata") or {},
                    item.get("transcript") or "",
                ): item for item in items
            }
            for future in as_completed(futures):
                future.result()
                completed += 1
                prog = _get_search_progress(search_run_id)
                if prog:
                    prog["transcripts_done"] = completed
                    prog["transcript_job_status"] = "running"
                    _set_search_progress(search_run_id, prog)

        prog = _get_search_progress(search_run_id)
        if prog:
            prog["transcript_job_status"] = "completed"
            prog["transcript_phase"] = "done"
            prog["transcripts_done"] = total
            prog["message"] = "Video audio and captions are ready."
            _set_search_progress(search_run_id, prog)

    except Exception as e:
        print(f"[TRANSCRIPT] Pipeline error: {e}")
        prog = _get_search_progress(search_run_id)
        if prog:
            prog["transcript_job_status"] = "error"
            prog["transcript_error"] = str(e)
            _set_search_progress(search_run_id, prog)
