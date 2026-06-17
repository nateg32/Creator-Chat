import pytest
from pydantic import ValidationError

from backend.models import AskRequest


_IMAGE = {"data_url": "data:image/png;base64,AA==", "detail": "auto"}


def test_ask_request_allows_one_image():
    request = AskRequest(creator_id=1, question="rate this", images=[_IMAGE])

    assert len(request.images) == 1


def test_ask_request_rejects_multiple_images():
    with pytest.raises(ValidationError):
        AskRequest(creator_id=1, question="compare these", images=[_IMAGE, _IMAGE])
