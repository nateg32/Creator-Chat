"""
Transcription fallback using OpenAI Whisper API
"""
import os
import requests
from typing import Optional
from backend.settings import settings

def transcribe_video(video_url: str) -> Optional[str]:
    """
    Transcribe video/audio from URL using OpenAI Whisper API.
    
    Args:
        video_url: URL to video/audio file
        
    Returns:
        Transcript text or None if transcription fails
    """
    if not (settings.TRANSCRIPTION_API_KEY or settings.OPENAI_API_KEY):
        raise ValueError("TRANSCRIPTION_API_KEY / OPENAI_API_KEY is not set")
    
    # Skip transcription if it's a platform URL but not a direct media link
    # These URLs return HTML and cause OpenAI "Invalid file format" 400 errors.
    is_platform = any(p in video_url for p in ["youtube.com", "youtu.be", "instagram.com", "tiktok.com"])
    if is_platform:
        # We only transcribe if we have a direct media link (later enrichment might add these)
        if not any(ext in video_url.lower() for ext in [".mp4", ".mp3", ".wav", ".m4a", ".ogg"]):
            print(f"[TRANSCRIPTION] Skipping platform URL {video_url} - requires specialized scraper")
            return None

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
            # Use OpenAI Whisper API for transcription
            from openai import OpenAI
            kwargs = {"api_key": settings.TRANSCRIPTION_API_KEY or settings.OPENAI_API_KEY}
            if settings.TRANSCRIPTION_BASE_URL:
                kwargs["base_url"] = settings.TRANSCRIPTION_BASE_URL
            client = OpenAI(**kwargs)
            
            with open(tmp_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
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
