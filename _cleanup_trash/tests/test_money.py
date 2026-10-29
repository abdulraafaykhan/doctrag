from decimal import Decimal

import pytest

from app.money import calculate_emi, format_money, paise_to_rupees, rupees_to_paise


@pytest.mark.parametrize(
    ("paise", "display"),
    [
        (0, "₹0.00"),
        (1, "₹0.01"),
        (12500050, "₹1,25,000.50"),
        (12345678901, "₹12,34,56,789.01"),
        (-101, "-₹1.01"),
    ],
)
def test_indian_format(paise, display):
    assert format_money(paise) == display


def test_decimal_conversion():
    assert rupees_to_paise("1.005") == 101
    assert rupees_to_paise("-1.005") == -101
    assert paise_to_rupees(12345) == Decimal("123.45")
    with pytest.raises(TypeError):
        rupees_to_paise(1.01)
    with pytest.raises(TypeError):
        format_money(1.1)


def test_emi():
    assert calculate_emi(120000, "0", 12) == 10000
    assert calculate_emi(10000000, "12", 12) == 888488
    with pytest.raises(ValueError):
        calculate_emi(100, "12", 0)
    with pytest.raises(ValueError):
        calculate_emi(100, "-1", 12)
