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


#: Largest decimal exponent accepted as a financial input. Algeria's annual GDP
#: is of the order of 10**16 DZD, so no real amount or quantity in this system
#: reaches 10**18; anything wider is either an attack or a client bug. It also
#: keeps every quantize() inside the 28-digit decimal context: a value at this
#: bound needs 18 integer + 2 fractional digits, comfortably below the width at
#: which ``InvalidOperation`` starts escaping as an unhandled 500.
MAX_ABSOLUTE_EXPONENT = 18


def _require_supported_decimal(dec: Decimal, original: object) -> Decimal:
    """Refuse values that are not finite or not representable as money.

    ``Decimal('NaN')`` propagates through ``quantize`` without signalling, so an
    unchecked conversion lets a non-numeric value reach a money column (it is
    accepted and stored as the TEXT ``'NaN'``) instead of being refused;
    ``sNaN``/``Infinity`` and magnitudes near or above the decimal context width
    raise ``InvalidOperation`` instead, which is not a ``ValueError`` and used to
    escape the API as an unhandled 500. Both are data-integrity defects in a
    financial column, so both are refused here - at the single conversion point
    every arithmetic helper goes through - and callers see this module's ordinary
    ``ValueError`` contract.
    """
    if not dec.is_finite():
        raise ValueError(f"Non-finite decimal is prohibited in financial calculations: {original!r}")
    if dec != 0 and dec.adjusted() > MAX_ABSOLUTE_EXPONENT:
        raise ValueError(
            "Decimal magnitude exceeds the supported range "
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
    if isinstance(value, bool):
        # bool is an int subclass: True would silently become the amount 1.00.
        # A yes/no flag is not a financial value, so refuse it like a float.
        raise TypeError("Boolean is not a valid decimal input.")
    if isinstance(value, Decimal):
        return _require_supported_decimal(value, value)
    if isinstance(value, int):
        return _require_supported_decimal(Decimal(value), value)
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
        return _require_supported_decimal(dec, value)
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
