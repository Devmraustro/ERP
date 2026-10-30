# SPDX-License-Identifier: LGPL-3.0-only
"""Exact decimal arithmetic helpers for Algerian DZD financial integrity.

Binary floating-point (float) inputs are strictly rejected for monetary calculations.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Union

MONEY_QUANT = Decimal("0.01")
QTY_QUANT = Decimal("0.000001")
RATE_QUANT = Decimal("0.0001")

DecimalInput = Union[Decimal, str, int]


class FloatMonetaryError(TypeError):
    """Raised when a binary float is passed into a financial calculation."""


def _require_finite(dec: Decimal, original: object) -> Decimal:
    """Refuse NaN and ±Infinity, which ``Decimal`` would otherwise carry silently.

    ``Decimal('NaN')`` propagates through ``quantize`` without signalling, so an
    unchecked conversion lets a non-numeric string reach a money column (it is
    accepted and stored as the TEXT ``'NaN'``) instead of being refused. A
    financial value that is not a finite number is never a value: refuse it at
    the single conversion point every arithmetic helper goes through, so callers
    see the ordinary ``ValueError`` contract of this module.
    """
    if not dec.is_finite():
        raise ValueError(f"Non-finite decimal is prohibited in financial calculations: {original!r}")
    return dec


def to_decimal(value: DecimalInput, allow_float: bool = False) -> Decimal:
    """Convert value to exact Decimal, rejecting binary floats by default."""
    if isinstance(value, float):
        if not allow_float:
            raise FloatMonetaryError(
                f"Binary float ({value!r}) is prohibited in financial calculations; pass Decimal or str."
            )
        value = str(value)
    if isinstance(value, Decimal):
        return _require_finite(value, value)
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        cleaned = value.strip().replace("\u00a0", "").replace(" ", "")
        # Normalize French comma decimal separator if no dot is present
        if "," in cleaned and "." not in cleaned:
            cleaned = cleaned.replace(",", ".")
        elif "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(",", "")
        try:
            dec = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid decimal string: {value!r}") from exc
        return _require_finite(dec, value)
    raise TypeError(f"Unsupported type for Decimal conversion: {type(value).__name__}")


def round_dzd(value: DecimalInput) -> Decimal:
    """Round a monetary amount to 0.01 DZD using commercial ROUND_HALF_UP."""
    dec = to_decimal(value)
    return dec.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def round_qty(value: DecimalInput) -> Decimal:
    """Round a stock or invoice quantity to 6 decimal places."""
    dec = to_decimal(value)
    return dec.quantize(QTY_QUANT, rounding=ROUND_HALF_UP)


def format_dzd(value: DecimalInput) -> str:
    """Format a Decimal as a canonical 2-decimal string (e.g. '119000.00')."""
    return f"{round_dzd(value):.2f}"
