import traceback
import os
from typing import List, Dict, Any, Optional
from backend.db import db
import tempfile
import subprocess
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from backend.services.transcript_quality import assess_transcript_quality


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

def synthesize_media_url(source_url: str, platform: str) -> Optional[str]:
    """Attempt to get an actual media URL if needed. For now, rely on yt-dlp if available."""
    try:
        # Use yt-dlp to extract the actual direct media url
        result = subprocess.run(
            ["yt-dlp", "-f", "bestaudio/best", "-g", source_url],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split('\n')[0]
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

def transcribe_with_whisper(media_url_or_path: str) -> Optional[str]:
    """Helper to run whisper unconditionally"""
    from backend.settings import settings
    if not settings.OPENAI_API_KEY:
        print("OPENAI_API_KEY not set")
        return None
        
    try:
        # Download media first if it's a URL
        tmp_path = None
        if media_url_or_path.startswith("http"):
            import requests
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            # For direct media links
            response = requests.get(media_url_or_path, headers=headers, timeout=30, stream=True)
            response.raise_for_status()
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                tmp_path = tmp_file.name
        else:
            tmp_path = media_url_or_path
            
        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            with open(tmp_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text"
                )
            return transcript if isinstance(transcript, str) else transcript.text
        finally:
            if tmp_path and media_url_or_path.startswith("http"):
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
    except Exception as e:
        print(f"transcribe_with_whisper error: {e}")
        return None

def process_transcript_job(item_id: str, source_url: str, platform: str, caption: str = "", metadata: Optional[Dict[str, Any]] = None, existing_transcript: str = ""):
    """Processes a single item's transcript and updates DB."""
    print(f"[TRANSCRIPT] Starting job for {item_id} ({source_url})")
    
    transcript_text = None
    status = "missing"
    source = "NONE"
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata) if metadata else {}
        except Exception:
            metadata = {}
    else:
        metadata = metadata or {}
    platform_key = (platform or "unknown").lower()
    title = str(metadata.get("title") or "")
    best_diag = assess_transcript_quality(existing_transcript, caption=caption, title=title)
    if best_diag.get("usable"):
        transcript_text = existing_transcript
        source = str(metadata.get("transcript_source") or "SCRAPER")
        status = "present"

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
        if platform_key in {"youtube", "youtube_shorts"}:
            try:
                from backend.apify_service import _extract_youtube_transcripts, get_apify_token
                token = get_apify_token()
                youtube_transcript = _extract_youtube_transcripts([source_url], token).get(source_url, "")
                if youtube_transcript:
                    consider(youtube_transcript, "YOUTUBE_ACTOR")
            except Exception as e:
                print(f"[TRANSCRIPT] YouTube transcript recovery failed for {source_url}: {e}")
        elif platform_key in {"instagram", "tiktok"}:
            try:
                from backend.apify_service import _extract_social_transcripts, get_apify_token
                token = get_apify_token()
                social_transcript = _extract_social_transcripts([source_url], token, platform=platform_key).get(source_url, "")
                if social_transcript:
                    consider(social_transcript, "SOCIAL_ACTOR")
            except Exception as e:
                print(f"[TRANSCRIPT] Social transcript recovery failed for {source_url}: {e}")

        if not transcript_text:
            media_candidates = [
                metadata.get("video_url"),
                metadata.get("videoUrl"),
                metadata.get("video"),
                source_url,
            ]
            for candidate_url in media_candidates:
                if not candidate_url:
                    continue
                direct_url = str(candidate_url).strip()
                if not direct_url:
                    continue

                if not _looks_like_direct_media_url(direct_url):
                    resolved_url = synthesize_media_url(direct_url, platform_key)
                    if not resolved_url or (resolved_url == direct_url and not _looks_like_direct_media_url(resolved_url)):
                        continue
                    direct_url = resolved_url

                whisper_transcript = transcribe_with_whisper(direct_url)
                if whisper_transcript:
                    prior_text = transcript_text
                    consider(whisper_transcript, "WHISPER_ASR")
                    if transcript_text and transcript_text != prior_text:
                        break
                    
        # Update DB
        if transcript_text:
            print(f"[TRANSCRIPT] Completed {item_id} via {source}")
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
                    json.dumps({
                        "transcript_source": str(source).lower(),
                        "transcript_quality_score": best_diag.get("score"),
                        "transcript_quality_reason": best_diag.get("reason"),
                        "transcript_coverage": best_diag.get("coverage"),
                        "transcript_word_count": best_diag.get("word_count"),
                    }),
                    item_id,
                )
            )
        else:
            failure_diag = assess_transcript_quality(existing_transcript, caption=caption, title=title)
            db.execute_update(
                """
                UPDATE scrape_items
                SET transcript_status = %s,
                    metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                WHERE id = %s
                """,
                (
                    status,
                    json.dumps({
                        "transcript_quality_score": failure_diag.get("score"),
                        "transcript_quality_reason": failure_diag.get("reason"),
                        "transcript_coverage": failure_diag.get("coverage"),
                    }),
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
    """Orchestrates transcripts for an entire search run using batch-first, concurrent fallback processing."""
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
            prog["message"] = "Transcript enrichment running in background..."
            _set_search_progress(search_run_id, prog)

        for item in items:
            db.execute_update("UPDATE scrape_items SET transcript_status = 'processing' WHERE id = %s", (item["id"],))

        from backend.apify_service import batch_extract_all_transcripts
        batch_candidates = []
        for item in items:
            batch_candidates.append({
                "source_url": item.get("source_url"),
                "platform": item.get("platform") or "unknown",
                "caption": item.get("caption") or "",
                "transcript": item.get("transcript") or "",
                "transcript_status": item.get("transcript_status") or "missing",
                "metadata": item.get("metadata") or {},
                "_id": item.get("id"),
            })

        batch_extract_all_transcripts(batch_candidates)

        completed = 0
        pending_fallback = []
        for candidate in batch_candidates:
            transcript_text = (candidate.get("transcript") or "").strip()
            if transcript_text:
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
                        json.dumps(candidate.get("metadata") or {}),
                        candidate["_id"],
                    ),
                )
                completed += 1
            else:
                db.execute_update(
                    "UPDATE scrape_items SET transcript_status = 'queued' WHERE id = %s",
                    (candidate["_id"],),
                )
                pending_fallback.append(candidate)

            prog = _get_search_progress(search_run_id)
            if prog:
                prog["transcripts_done"] = completed
                prog["transcript_job_status"] = "running"
                _set_search_progress(search_run_id, prog)

        max_workers = max(1, int(os.getenv("TRANSCRIPT_CONCURRENCY", "4")))
        if pending_fallback:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(pending_fallback))) as executor:
                futures = {
                    executor.submit(
                        process_transcript_job,
                        item["_id"],
                        item.get("source_url") or "",
                        item.get("platform") or "unknown",
                        item.get("caption") or "",
                        item.get("metadata") or {},
                        item.get("transcript") or "",
                    ): item for item in pending_fallback
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
            prog["message"] = "Transcript enrichment finished."
            _set_search_progress(search_run_id, prog)

    except Exception as e:
        print(f"[TRANSCRIPT] Pipeline error: {e}")
        prog = _get_search_progress(search_run_id)
        if prog:
            prog["transcript_job_status"] = "error"
            prog["transcript_error"] = str(e)
            _set_search_progress(search_run_id, prog)
