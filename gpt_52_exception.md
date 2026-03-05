# GPT-5.2 Responses API Implementation & Exception Trace

## 1. The Function that Calls GPT-5.2 for Search

This function (`_search_responses_api`) uses the `openai.responses.create()` endpoint, which is required for `gpt-5.2` to use web search (since it does not support `web_search_options` in the standard Chat Completions API).

```python
    def _search_responses_api(self, prompt: str, creator_name: str) -> List[Dict[str, Any]]:
        """Use OpenAI Responses API with web_search_preview tool (supports GPT-5.2)."""
        import openai
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        
        logger.info(f"OpenAISearch: Using Responses API with {self.model}")
        try:
            # This is the exact payload that triggers the bug
            # The API call itself succeeds on the server side, but the Python SDK
            # crashes when trying to deserialize the response object.
            response = client.responses.create(
                model=self.model, # Evaluates to "gpt-5.2" or "gpt-4.5-preview"
                tools=[{"type": "web_search_preview"}],
                input=prompt,
            )
        except Exception as create_err:
            # We catch the SDK crash here and trigger the graceful fallback to Chat Completions
            logger.warning(f"OpenAISearch: Responses API create() failed, triggering fallback: {create_err}")
            return []
        
        # Safely extract text — prefer model_dump() first to bypass SDK type annotation bugs
        text = ""
        try:
            raw = response.model_dump() if hasattr(response, 'model_dump') else {}
            for item in raw.get('output', []):
                if isinstance(item, dict):
                    # Direct text on the item
                    if item.get('text'):
                        text += item['text']
                    # Content blocks (message items)
                    for block in item.get('content', []):
                        if isinstance(block, dict) and block.get('text'):
                            text += block['text']
        except Exception as dump_err:
            logger.warning(f"OpenAISearch: model_dump extraction failed: {dump_err}")
            
        # ... (URL extraction logic follows)
```

## 2. The Exact Exception Trace

This is the exact error trace produced when calling the `client.responses.create()` method using the standard `openai` python package. 

The error occurs during response deserialization inside Pydantic/typing because the API returns a response format that the SDK's type hints (`typing.Union`) cannot resolve an exact mapping for (`__discriminator__` missing).

```
WARNING:services.research_provider:OpenAISearch: Responses API create() failed, triggering fallback: 'typing.Union' object has no attribute '__discriminator__' and no __dict__ for setting new attributes
```

**Why this happens:**
1. We send a valid request to `https://api.openai.com/v1/responses`
2. OpenAI's servers process it successfully and return an `HTTP 200 OK` with JSON
3. The `openai` Python SDK intercepts the JSON and tries to map it to its internal Pydantic models (specifically the `Response` object which contains a `Union` of different output types)
4. The SDK bug crashes the script before the `response` variable is ever assigned, throwing the `AttributeError` on `typing.Union`.
5. We catch it and gracefully fall back to `gpt-4o-search-preview` via Chat Completions.
