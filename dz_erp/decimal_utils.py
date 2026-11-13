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

# Every consumer of these helpers multiplies, sums and then ``quantize``s into a
# 28-digit decimal context. An input whose magnitude exceeds 10**18 cannot be a
# real DZD amount or quantity (Algeria's annual GDP is ~10**16 DZD), while
# ``quantize`` on anything near or above the context width raises
# ``decimal.InvalidOperation`` - which escaped the API as an unhandled 500.
MAX_ABSOLUTE_EXPONENT = 18


class FloatMonetaryError(TypeError):
    """Raised when a binary float is passed into a financial calculation."""


def _guarded(dec: Decimal, original: object) -> Decimal:
    """Reject non-finite and out-of-range decimals at the single conversion point.

    ``Decimal('NaN')``/``'sNaN'``/``'Infinity'`` are accepted by
    ``Decimal(str)`` and are *quiet* for NaN: ``quantize`` returns NaN rather
    than raising, so ``'NaN'`` used to be written into a TEXT money column and
    answered with HTTP 200.  ``sNaN``, ``Infinity`` and very large magnitudes
    raised ``InvalidOperation`` instead, escaping as an unhandled 500.  Both are
    data-integrity defects in a financial column, so they are refused here for
    every caller (API, decision service, backup verifier).
    """
    if not dec.is_finite():
        raise ValueError(f"Non-finite decimal input is not permitted: {original!r}")
    if dec != 0 and dec.adjusted() > MAX_ABSOLUTE_EXPONENT:
        raise ValueError(
            f"Decimal input magnitude exceeds the supported range "
            f"(1e{MAX_ABSOLUTE_EXPONENT}): {original!r}"
        )
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
        return _guarded(value, value)
    if isinstance(value, bool):
        # bool is an int subclass; True would silently become 1.00.
        raise TypeError("Boolean is not a valid decimal input.")
    if isinstance(value, int):
        return _guarded(Decimal(value), value)
    if isinstance(value, str):
        cleaned = value.strip().replace("\u00a0", "").replace(" ", "")
        # Normalize French comma decimal separator if no dot is present
        if "," in cleaned and "." not in cleaned:
            cleaned = cleaned.replace(",", ".")
        elif "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(",", "")
        try:
            return _guarded(Decimal(cleaned), value)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid decimal string: {value!r}") from exc
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
