import re
import emoji

def clean_response(text: str, strip_hyphens: bool = False) -> str:
    """
    Cleans up a complete response to remove artifacts, orphaned punctuation, 
    and optionally hyphens, without joining words incorrectly.
    """
    if not text:
        return text
        
    # 1. Strip transcript artifacts like [music], [Applause], timestamps (0:00)
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\b\d{1,2}:\d{2}\b', '', text)
    
    # 2. Strip Unicode Emojis
    text = emoji.replace_emoji(text, replace='')
    
    # 3. Optionally strip mid-sentence hyphens (but carefully!)
    if strip_hyphens:
        # Only replace hyphens between words. 
        # e.g., "well-known" -> "well known"
        text = re.sub(r'(?<=\w)-(?=\w)', ' ', text)
        
    # 4. Clean up double spaces created by modifications
    text = re.sub(r' {2,}', ' ', text)
    
    # 5. Clean up orphaned punctuation
    # Space before period or comma
    text = re.sub(r' \.', '.', text)
    text = re.sub(r' ,', ',', text)
    # Double dashes left behind
    text = re.sub(r'[-\u2013\u2014]\s*[-\u2013\u2014]', '', text)
    # Empty parens/brackets
    text = re.sub(r'\(\s*\)', '', text)
    text = re.sub(r'\[\s*\]', '', text)
    text = re.sub(r'\.\s*,', '.', text)
    text = re.sub(r',\s*\.', '.', text)
    text = re.sub(r'\s+([,\.\!\?])', r'\1', text)
    
    return text.strip()

def clean_for_stream_chunk(text: str) -> str:
    """
    Lightweight cleaning safe for incomplete stream chunks.
    Should NOT do regex replacements that cross word boundaries.
    """
    if not text:
        return text
    
    # Simple transcript artifact stripping that doesn't mess with word boundaries mid-stream
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\b\d{1,2}:\d{2}\b', '', text)
    
    return text

def should_strip_hyphens(config: dict) -> bool:
    """
    Determines whether to strip hyphens based on creator voice config.
    """
    if not config:
        return False
    return config.get('rhythm', {}).get('strip_hyphens', False)
