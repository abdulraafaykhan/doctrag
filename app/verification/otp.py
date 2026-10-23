"""Demo-only OTP adapter; replacing this seam must not change the chat flow."""

from app.config import get_settings


def send_otp() -> None:
    if get_settings().otp_mode == "real":
        # A real provider needs secure delivery, secret storage and validation first.
        raise NotImplementedError("Real OTP delivery is not implemented")


def accepts_otp(value: str) -> bool:
    if get_settings().otp_mode == "real":
        raise NotImplementedError("Real OTP verification is not implemented")
    return len(value) == 6 and value.isascii() and value.isdigit()
