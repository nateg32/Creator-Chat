import traceback
import os
import re
from typing import List, Dict, Any, Optional
from backend.db import db
from backend.lib.transcription import transcribe_video
import tempfile
import subprocess
import json

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

def process_transcript_job(item_id: str, source_url: str, platform: str, caption: str = ""):
    """Processes a single item's transcript and updates DB."""
    print(f"[TRANSCRIPT] Starting job for {item_id} ({source_url})")
    
    transcript_text = None
    status = "error"
    source = "NONE"
    
    try:
        if platform == "youtube":
            video_id_match = re.search(r'(?:v=|youtu\.be/|/shorts/)([\w-]+)', source_url)
            if video_id_match:
                video_id = video_id_match.group(1)
                try:
                    from youtube_transcript_api import YouTubeTranscriptApi
                    yt_transcript = YouTubeTranscriptApi.get_transcript(video_id)
                    transcript_text = " ".join([t['text'] for t in yt_transcript])
                    status = "present"
                    source = "YT_CAPTIONS"
                except Exception as e:
                    print(f"[TRANSCRIPT] YT caption failed: {e}")
                    status = "missing"
            
            if not transcript_text:
                print(f"[TRANSCRIPT] Falling back to whisper for YT {source_url}")
                # Use yt-dlp to get media url
                direct_url = synthesize_media_url(source_url, "youtube")
                transcript_text = transcribe_with_whisper(direct_url)
                if transcript_text:
                    status = "present"
                    source = "WHISPER_ASR"
                else:
                    status = "error"
        else:
            if caption and len(caption.strip()) > 10:
                transcript_text = caption
                status = "present"
                source = "CAPTION"
            else:
                direct_url = synthesize_media_url(source_url, platform)
                transcript_text = transcribe_with_whisper(direct_url)
                if transcript_text:
                    status = "present"
                    source = "WHISPER_ASR"
                else:
                    status = "missing"
                    
        # Update DB
        if transcript_text:
            db.execute_update(
                "UPDATE scrape_items SET transcript = %s, transcript_status = 'present' WHERE id = %s",
                (transcript_text, item_id)
            )
        else:
            db.execute_update(
                "UPDATE scrape_items SET transcript_status = %s WHERE id = %s",
                (status, item_id)
            )
            
    except Exception as e:
        print(f"[TRANSCRIPT] Error on {item_id}: {e}")
        traceback.print_exc()
        db.execute_update(
            "UPDATE scrape_items SET transcript_status = 'error' WHERE id = %s",
            (item_id,)
        )

def run_transcripts_for_search(search_run_id: str):
    """Orchestrates transcripts for an entire search run."""
    print(f"[TRANSCRIPT] Starting async pipeline for search {search_run_id}")
    try:
        query = """
            SELECT id, source_url, platform, caption
            FROM scrape_items
            WHERE scrape_run_id = %s 
              AND is_primary = true 
              AND transcript_status IN ('not_started', 'queued', 'pending', 'missing')
        """
        items = db.execute_query(query, (search_run_id,))
        
        if not items:
            print("[TRANSCRIPT] No items need processing")
            from backend.app import _get_search_progress, _set_search_progress
            prog = _get_search_progress(search_run_id)
            if prog:
                prog["phase"] = "done"
                _set_search_progress(search_run_id, prog)
            return
            
        # Update progress to transcripts phase
        from backend.app import _get_search_progress, _set_search_progress
        prog = _get_search_progress(search_run_id)
        if prog:
            prog["phase"] = "transcripts"
            _set_search_progress(search_run_id, prog)
            
        total = len(items)
        for i, item in enumerate(items):
            db.execute_update("UPDATE scrape_items SET transcript_status = 'processing' WHERE id = %s", (item["id"],))
            process_transcript_job(item["id"], item["source_url"], item.get("platform") or "unknown", item.get("caption") or "")
            
            # Increment progress
            prog = _get_search_progress(search_run_id)
            if prog:
                prog["transcripts_done"] = i + 1
                prog["percent"] = 90.0 + ((i + 1) / total) * 10.0
                _set_search_progress(search_run_id, prog)
                
        # Done
        prog = _get_search_progress(search_run_id)
        if prog:
            prog["phase"] = "done"
            prog["percent"] = 100.0
            _set_search_progress(search_run_id, prog)
            
    except Exception as e:
        print(f"[TRANSCRIPT] Pipeline error: {e}")
