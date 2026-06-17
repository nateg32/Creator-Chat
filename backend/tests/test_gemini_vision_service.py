import base64
import json

from backend.services.gemini_vision_service import GeminiVisionService


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(json.dumps({
            "summary": "Candlestick chart with a diagonal trendline and rejection near a marked level.",
            "visual_domain": "trading_chart",
            "primary_subject": "annotated chart",
            "visible_text": ["TRADES"],
            "direct_answer_hint": "This looks like a trendline break and retest area.",
            "chart_analysis": {
                "chart_type": "candlestick",
                "visible_setup": "break and retest",
                "trend_context": "price is moving down after rejecting a level",
                "key_levels": ["descending trendline", "marked rejection zone"],
                "possible_trade_read": "wait for confirmation around the retest",
                "invalidation_or_risk": "setup fails if price reclaims the level",
                "confidence": 0.71,
            },
            "uncertainties": ["ticker and timeframe are not readable"],
            "confidence": 0.74,
        }))


class _FakeClient:
    def __init__(self):
        self.models = _FakeModels()


def _data_url():
    raw = b"fake-png-bytes"
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def test_gemini_vision_analyzes_inline_image_payload():
    client = _FakeClient()
    service = GeminiVisionService(client=client)

    result = service.analyze_images(
        question="whats this setup right here?",
        images=[{"data_url": _data_url(), "detail": "high"}],
        creator_profile={"name": "Alex G", "creator_category": "trading"},
    )

    assert result["provider"] == "gemini"
    assert result["visual_domain"] == "trading_chart"
    assert result["chart_analysis"]["visible_setup"] == "break and retest"
    call = client.models.calls[0]
    assert call["model"].startswith("gemini-")
    assert "contents" in call


def test_gemini_vision_returns_safe_fallback_for_bad_data_url():
    service = GeminiVisionService(client=_FakeClient())

    result = service.analyze_images(
        question="what is this?",
        images=[{"data_url": "not-an-image"}],
    )

    assert result["provider"] == "gemini"
    assert result["confidence"] < 0.2
    assert "could not read" in result["summary"].lower()
