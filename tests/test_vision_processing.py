from types import SimpleNamespace

from app.config import get_settings
from app.llm import LLMClient
from app.vision import normalize_numeric_tokens, parse_vision_json, postprocess_vision


def fake_client(monkeypatch, replies):
    replies = iter(replies)
    calls = []
    reservations = []
    cfg = get_settings()

    def complete(**kwargs):
        calls.append(kwargs)
        assert kwargs["timeout"] == cfg.llm.vlm_online_timeout_seconds
        assert kwargs["model"] == cfg.models.vlm_online.removeprefix("groq/")
        assert kwargs["response_format"] == {"type": "json_object"}
        return SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content=next(replies)))],
        )

    monkeypatch.setattr(
        "app.llm.groq.Groq",
        lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=complete))
        ),
    )
    monkeypatch.setattr("app.llm.genai.Client", lambda **kwargs: SimpleNamespace())
    client = LLMClient(
        quota=SimpleNamespace(reserve=lambda kind, **kwargs: reservations.append(kind))
    )
    return client, calls, reservations


def test_fence_stripping_and_one_json_retry(monkeypatch):
    fenced = '```json\n{"rate":"7.10%"}\n```'
    assert parse_vision_json(fenced) == {"rate": "7.10%"}
    client, calls, quotas = fake_client(monkeypatch, ["not JSON", fenced])
    result = client.vision(
        b"synthetic",
        "image/png",
        "Read JSON",
        mode="online",
        pii_redacted=True,
        local_ocr="7.10%",
    )
    assert result["structured"] == {"rate": "7.10%"}
    assert result["status"] == "readable"
    assert len(calls) == len(quotas) == 2
    client, calls, quotas = fake_client(monkeypatch, ["invalid", "still invalid"])
    result = client.vision(
        b"synthetic", "image/png", "Read JSON", mode="online", pii_redacted=True
    )
    assert result["status"] == "unreadable"
    assert result["structured"] is None
    assert len(calls) == len(quotas) == 2


def test_notes_need_same_crop_ocr_and_numeric_normalization(monkeypatch):
    logs = []
    monkeypatch.setattr(
        "app.vision.log.step", lambda area, action, **kwargs: logs.append(action)
    )
    value = {
        "notes": ["Special tenure rate", "Premature withdrawal allowed"],
        "rate": "7.10%",
        "fee": "9.99%",
        "label": "Office loan",
    }
    result = postprocess_vision(value, "Special tenure rate 7.lO% Office loan")
    assert result["notes"] == ["Special tenure rate"]
    assert result["rate"] == "7.10%"
    assert result["fee"] is None
    assert result["label"] == "Office loan"
    assert "dropped vision notes absent from crop OCR" in logs
    assert postprocess_vision(value, "")["notes"] == []
    assert (
        normalize_numeric_tokens("Office loan O l 7.lO% 1O0")
        == "Office loan O l 7.10% 100"
    )


def test_cut_off_row_stays_null_even_when_model_fills_it():
    value = {
        "columns": ["Tenure", "General"],
        "rows": [["12 months", "7.10%"], ["24 months", "7.35%"]],
        "truncated": False,
    }
    result = postprocess_vision(
        value, "12 months 7.10% 24 months 7.35%", cut_off_rows=(1,)
    )
    assert result["truncated"] is True
    assert result["rows"] == [["12 months", "7.10%"], [None, None]]
    assert value["rows"][1] == ["24 months", "7.35%"]
    value["truncated"] = True
    assert postprocess_vision(value, "7.10% 7.35%")["rows"][-1] == [None, None]
    value["rows"].pop()
    value["truncated"] = False
    assert postprocess_vision(value, "7.10%", cut_off_rows=(1,))["rows"][-1] == [
        None,
        None,
    ]
