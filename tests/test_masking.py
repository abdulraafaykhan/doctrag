import json

import pytest

from app.log import AppLog, mask_account, mask_mobile, mask_text


def test_specific_masks():
    assert mask_mobile("9876543221") == "98****21"
    assert mask_account("123456785511") == "****5511"


@pytest.mark.parametrize(
    "raw",
    [
        "9876543221",
        "+91 98765 43221",
        "98765-43221",
        "1234 5678 5511",
        "1234-5678-5511",
        "123456",
        "4111 1111 1111 1111",
        "OTP: 123456",
        "PIN 1234",
        "CVV=123",
        "card: 1234",
    ],
)
def test_pii_never_survives(raw):
    masked = mask_text(raw)
    assert masked != raw
    assert not any(
        value in masked
        for value in ("9876543221", "123456785511", "123456", "4111", "1234")
    )


def test_prose_and_money_survive():
    assert (
        mask_text("Interest is 8.50%, ₹1,25,000.50 over 12 months.")
        == "Interest is 8.50%, ₹1,25,000.50 over 12 months."
    )


def test_log_masks_nested_fields_and_truncates(tmp_path):
    import logging
    from logging.handlers import RotatingFileHandler

    from app.config import get_settings

    get_settings().logging.file = str(tmp_path / "masked.log")
    instance = AppLog()
    logger = logging.getLogger("harbourline")
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    instance.step(
        "AUTH",
        "candidate",
        args={
            "mobile": "9876543221",
            "otp": "123456",
            "card_number": "4111111111111111",
        },
        message="x" * 100,
    )
    for handler in logger.handlers:
        handler.flush()
    text = (tmp_path / "masked.log").read_text(encoding="utf-8")
    record = json.loads(text)
    assert (
        "9876543221" not in text
        and "123456" not in text
        and "4111111111111111" not in text
    )
    assert len(record["message"]) == 80
    assert any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers)
