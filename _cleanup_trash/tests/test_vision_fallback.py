import base64
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors

from app.config import get_settings
from app.llm import LLMClient, QuotaExceeded


@pytest.mark.parametrize(
    "failure", ["429", "500", "503", "timeout", "exhausted", "401", "quota"]
)
def test_vision_fake_provider_fallback(monkeypatch, failure):
    cfg = get_settings()
    requests = []
    reservations = []
    logs = []
    image = b"synthetic image with no PII"

    def gemini_call(**kwargs):
        requests.append(("gemini", kwargs))
        assert (
            kwargs["config"].http_options.timeout
            == cfg.llm.vlm_ingest_timeout_seconds * 1000
        )
        assert kwargs["config"].response_mime_type == "application/json"
        if failure == "timeout":
            raise httpx.ReadTimeout("fake timeout")
        status = 503 if failure in {"exhausted", "quota"} else int(failure)
        exception = errors.ServerError if status >= 500 else errors.ClientError
        raise exception(status, {"error": {"message": "synthetic failure"}})

    def groq_call(**kwargs):
        requests.append(("groq", kwargs))
        assert kwargs["model"] == cfg.models.vlm_ingest_fallbacks[0].removeprefix(
            "groq/"
        )
        assert kwargs["timeout"] == cfg.llm.vlm_ingest_timeout_seconds
        assert kwargs["response_format"] == {"type": "json_object"}
        content = kwargs["messages"][0]["content"]
        assert "9876543221" not in content[0]["text"]
        assert (
            base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1]) == image
        )
        if failure == "exhausted":
            raise httpx.ReadTimeout("fake fallback timeout")
        return SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(message=SimpleNamespace(content='{"rate":"7.35%"}'))
            ],
        )

    def reserve(kind, **kwargs):
        reservations.append(kind)
        if failure == "quota":
            raise QuotaExceeded("fake local budget exhausted")

    fake_groq = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=groq_call))
    )
    fake_gemini = SimpleNamespace(models=SimpleNamespace(generate_content=gemini_call))
    monkeypatch.setattr("app.llm.groq.Groq", lambda **kwargs: fake_groq)
    monkeypatch.setattr("app.llm.genai.Client", lambda **kwargs: fake_gemini)
    monkeypatch.setattr(
        "app.llm.log.step",
        lambda area, action, **kwargs: logs.append((area, action, kwargs)),
    )
    client = LLMClient(quota=SimpleNamespace(reserve=reserve))
    if failure == "401":
        with pytest.raises(errors.ClientError):
            client.vision(
                image, "image/png", "Read this", mode="ingest", pii_redacted=True
            )
        assert len(requests) == 1
        assert not any(action == "fallback" for _, action, _ in logs)
        return
    result = client.vision(
        image,
        "image/png",
        "Read this for 9876543221",
        mode="ingest",
        pii_redacted=True,
        local_ocr="Local text 9876543221 rate 7.35%",
    )
    if failure in {"exhausted", "quota"}:
        assert result["confidence"] == "low"
        assert result["warning"] == cfg.messages["upload"]["vision_unavailable"]
        assert "9876543221" not in result["text"]
        assert len(requests) == (0 if failure == "quota" else 2)
    else:
        assert result["structured"] == {"rate": "7.35%"}
        assert result["status"] == "readable"
        assert result["confidence"] == "model"
        assert result["warning"] is None
        assert [provider for provider, _ in requests] == ["gemini", "groq"]
        assert reservations == ["vlm", "vlm"]
    if failure != "quota":
        switches = [fields for _, action, fields in logs if action == "fallback"]
        assert switches[0]["model"] == cfg.models.vlm_ingest
        assert switches[0]["next_model"] == cfg.models.vlm_ingest_fallbacks[0]
