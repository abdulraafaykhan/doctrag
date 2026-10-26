"""Integer-paise money operations; models never do arithmetic."""

from decimal import Decimal

from app.config import get_settings


def rupees_to_paise(value: str | Decimal | int) -> int:
    if isinstance(value, float):
        raise TypeError("Use a decimal string for rupees")
    return int(
        (Decimal(value) * 100).quantize(
            Decimal("1"), rounding=get_settings().money["rounding"]
        )
    )


def paise_to_rupees(paise: int) -> Decimal:
    if type(paise) is not int:
        raise TypeError("Paise must be an integer")
    return Decimal(paise) / 100


def format_money(paise: int) -> str:
    value = paise_to_rupees(paise)
    cfg = get_settings().money
    whole, fraction = f"{abs(value):.{cfg['decimal_places']}f}".split(".")
    tail, head = whole[-3:], whole[:-3]
    groups = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    grouped = ",".join([*groups, tail])
    return f"{'-' if paise < 0 else ''}{cfg['symbol']}{grouped}.{fraction}"


def calculate_emi(
    principal_paise: int, annual_rate_percent: str | Decimal, months: int
) -> int:
    paise_to_rupees(principal_paise)
    rate = Decimal(annual_rate_percent)
    if principal_paise < 0 or rate < 0 or type(months) is not int or months <= 0:
        raise ValueError(
            "Principal and rate must be nonnegative; months must be positive"
        )
    monthly = rate / 100 / get_settings().money["months_per_year"]
    # Decimal avoids binary rounding errors in financial calculations.
    amount = (
        Decimal(principal_paise) / months
        if not monthly
        else Decimal(principal_paise) * monthly / (1 - (1 + monthly) ** -months)
    )
    return int(amount.quantize(Decimal("1"), rounding=get_settings().money["rounding"]))
