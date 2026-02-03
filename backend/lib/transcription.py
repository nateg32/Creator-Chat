"""
Transcription fallback using OpenAI Whisper API
"""
import os
import requests
from typing import Optional
from .settings import settings

def transcribe_video(video_url: str) -> Optional[str]:
    """
    Transcribe video/audio from URL using OpenAI Whisper API.
    
    Args:
        video_url: URL to video/audio file
        
    Returns:
        Transcript text or None if transcription fails
    """
    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set")
    
    try:
        # Download video/audio file
        response = requests.get(video_url, timeout=30)
        response.raise_for_status()
        
        # Save to temporary file
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
            tmp_file.write(response.content)
            tmp_path = tmp_file.name
        
        try:
            # Use OpenAI Whisper API for transcription
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
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
                
    except Exception as e:
        print(f"Transcription error: {e}")
        return None
