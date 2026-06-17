"""
Whisper-first transcription helper with optional AssemblyAI enrichment.
"""
import os
import requests
from typing import Optional
from backend.settings import settings

def transcribe_video(video_url: str) -> Optional[str]:
    """
    Transcribe video/audio from URL. Platform URLs are resolved to media first;
    Whisper creates the raw transcript and AssemblyAI can enrich/caption it.
    
    Args:
        video_url: URL to video/audio file
        
    Returns:
        Transcript text or None if transcription fails
    """
    try:
        from backend.services.transcript_worker import (
            _looks_like_direct_media_url,
            synthesize_media_url,
            transcribe_with_hybrid,
        )

        media_url = video_url
        is_platform = any(p in str(video_url) for p in ["youtube.com", "youtu.be", "instagram.com", "tiktok.com"])
        if is_platform and not _looks_like_direct_media_url(str(video_url)):
            media_url = synthesize_media_url(str(video_url), "unknown") or ""
        if not media_url:
            return None
        result = transcribe_with_hybrid(media_url)
        transcript = str((result or {}).get("text") or "").strip()
        if transcript:
            return transcript
    except Exception as e:
        print(f"[TRANSCRIPTION] Hybrid transcription failed, falling back to legacy path: {e}")

    try:
        # Download video/audio file with a User-Agent to avoid blocks
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(video_url, headers=headers, timeout=30, stream=True)
        
        # Check Content-Type - skip if not audio/video
        content_type = response.headers.get("Content-Type", "").lower()
        if not ("audio" in content_type or "video" in content_type or "application/octet-stream" in content_type):
            print(f"[TRANSCRIPTION] Skipping URL {video_url} - unsupported Content-Type: {content_type}")
            return None
            
        response.raise_for_status()
        
        # Save to temporary file
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
            tmp_file.write(response.content)
            tmp_path = tmp_file.name
        
        try:
            if not (settings.TRANSCRIPTION_API_KEY or settings.OPENAI_API_KEY):
                return None

            # Legacy fallback: OpenAI Whisper API for direct media only.
            from openai import OpenAI
            kwargs = {"api_key": settings.TRANSCRIPTION_API_KEY or settings.OPENAI_API_KEY}
            if settings.TRANSCRIPTION_BASE_URL:
                kwargs["base_url"] = settings.TRANSCRIPTION_BASE_URL
            client = OpenAI(**kwargs)
            
            with open(tmp_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model=settings.TRANSCRIPTION_MODEL or "whisper-1",
                    file=audio_file,
                    response_format="text"
                )
            
            return transcript if isinstance(transcript, str) else transcript.text
        finally:
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
                
    except Exception as e:
        print(f"Transcription error: {e}")
        return None
