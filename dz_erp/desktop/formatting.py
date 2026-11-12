# SPDX-License-Identifier: LGPL-3.0-only
"""Display formatting for the desktop client (Phase 29).

The rule this module follows, without exception: **format, never compute.**

The backend returns money as a canonical decimal *string* produced by
``dz_erp.decimal_utils.format_dzd`` from exact :class:`~decimal.Decimal`
arithmetic. That string is the authoritative figure. This module only inserts
thousands separators and a decimal mark so a human can read it; it never
re-rounds, never sums, and never converts through ``float``. A dashboard tile
that showed a different number from the ledger would be a financial defect,
not a cosmetic one.

Quantities keep the six decimals the schema stores, but trailing zeros are
trimmed for display so ``3.000000`` reads ``3`` while ``0.250000`` reads
``0.25``.

Everything here is Qt-free and covered by the headless test matrix.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Optional, Union

NumberLike = Union[str, Decimal, int, None]

#: Non-breaking space used as the thousands separator (ISO 31-0 style used in
#: Algerian/French accounting) so a figure never wraps mid-number.
THIN_SPACE = "\u00a0"

#: Rendered where the API returned no value at all (never for a real zero).
NO_VALUE = "\u2014"

_AMOUNT_RE = re.compile(r"^(?P<sign>-)?(?P<int>\d+)(?:\.(?P<frac>\d+))?$")


def _as_text(value: NumberLike) -> Optional[str]:
    """Convert without going through float; ``None`` stays ``None``."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bool):  # bool is an int subclass; never a money value
        return None
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    return text or None


def _group(digits: str) -> str:
    """Insert non-breaking thousands separators into a digit run."""
    if len(digits) <= 3:
        return digits
    head = len(digits) % 3 or 3
    groups = [digits[:head]]
    for i in range(head, len(digits), 3):
        groups.append(digits[i : i + 3])
    return THIN_SPACE.join(groups)


def format_amount(value: NumberLike, *, currency: str = "") -> str:
    """Group the digits of a canonical decimal string; append ``currency``.

    ``"11900.00"`` → ``"11 900,00 DZD"`` (with a non-breaking separator).
    A value that is not a plain decimal string is returned unchanged rather
    than mangled, so an unexpected backend format is visible instead of
    silently wrong. ``None`` is the API's "no value" and renders as an
    em dash: an empty cell reads as zero at a glance, which is a lie.
    """
    text = _as_text(value)
    if text is None:
        return NO_VALUE
    match = _AMOUNT_RE.match(text)
    if not match:
        return f"{text} {currency}".strip()
    integer = _group(match.group("int"))
    fraction = match.group("frac")
    if fraction is not None:
        # The backend stores money at scale 2 and costs/rates at scale 6.
        # Money is always shown with exactly two decimals - a ledger cell
        # reading "11 900,0 DZD" beside "11 900,00 DZD" looks like two
        # different precisions. A non-money decimal keeps the scale it came
        # with, minus the padding zeros. No digit is ever rounded away.
        if currency and len(fraction) > 2:
            fraction = fraction[:2] if fraction[2] == "0" else fraction
        if not currency:
            trimmed = fraction.rstrip("0")
            fraction = trimmed if trimmed else ""
    body = f"{integer},{fraction}" if fraction else integer
    sign = "-" if match.group("sign") else ""
    rendered = f"{sign}{body}"
    return f"{rendered} {currency}".strip() if currency else rendered


def format_quantity(value: NumberLike, *, decimals: int = 6) -> str:
    """Group a quantity and trim insignificant trailing zeros."""
    text = _as_text(value)
    if text is None:
        return ""
    match = _AMOUNT_RE.match(text)
    if not match:
        return text
    integer = _group(match.group("int"))
    fraction = (match.group("frac") or "").rstrip("0")
    sign = "-" if match.group("sign") else ""
    return f"{sign}{integer},{fraction}" if fraction else f"{sign}{integer}"


def format_count(value: NumberLike) -> str:
    """Group a whole number (row counts, document counts)."""
    text = _as_text(value)
    if text is None:
        return ""
    return _group(text) if text.isdigit() else text


def format_percent(value: NumberLike) -> str:
    """Render a stored rate such as ``"19.0"`` as ``"19 %"``."""
    text = _as_text(value)
    if text is None:
        return ""
    match = _AMOUNT_RE.match(text)
    if not match:
        return f"{text} %"
    fraction = (match.group("frac") or "").rstrip("0")
    body = f"{match.group('int')},{fraction}" if fraction else match.group("int")
    return f"{body} %"


def format_date(value: NumberLike) -> str:
    """Trim an ISO timestamp to a date; leave anything else untouched."""
    text = _as_text(value)
    if not text:
        return ""
    return text.split("T", 1)[0]


def format_datetime(value: NumberLike) -> str:
    """``2026-10-27T10:00:01+00:00`` → ``2026-10-27 10:00`` (UTC as stored)."""
    text = _as_text(value)
    if not text:
        return ""
    if "T" not in text:
        return text
    day, rest = text.split("T", 1)
    clock = rest[:5] if len(rest) >= 5 else rest
    return f"{day} {clock}"


def format_confidence(value: NumberLike, *, calibrated: bool = False, lang: str = "fr") -> str:
    """Honest confidence: ``None`` means "not measured", never zero.

    The database stores a nullable confidence with a ``confidence_kind``; the
    review screen shows exactly what was measured. No review threshold exists
    in the schema, so none is invented here.
    """
    from .i18n import tr

    if value is None:
        return tr(lang, "review_not_measured")
    text = _as_text(value)
    if text is None:
        return tr(lang, "review_not_measured")
    suffix = tr(lang, "review_calibrated") if calibrated else ""
    return f"{text}{suffix}"


def format_row_count(count: int, lang: str = "fr") -> str:
    """``1 ligne`` / ``294 lignes`` - a count with a correctly inflected noun.

    French, Arabic and English all inflect the noun after one; a bare
    "{n} lignes" is wrong for n == 1 and looks machine-generated. Only the
    noun is localised: the number itself is formatted by :func:`format_count`.
    """
    from .i18n import tr

    key = "rows_count_one" if int(count) == 1 else "rows_count_many"
    return tr(lang, key, count=format_count(count))


def humanize_identifier(value: NumberLike) -> str:
    """Turn ``PendingApproval`` into ``Pending approval`` for display only.

    Technical identifiers (account codes, invoice numbers, field names) are
    never passed through this: only enum-ish workflow states are, and the
    underlying value is still what the API returned.
    """
    text = _as_text(value)
    if not text:
        return ""
    # Snake_case (`Asset_non_current`) and camelCase (`PendingApproval`) are
    # both enum spellings the API emits; neither is a display label.
    spaced = text.replace("_", " ")
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", spaced)
    spaced = re.sub(r"\s+", " ", spaced).strip()
    return spaced[:1].upper() + spaced[1:].lower() if spaced else text


def localized_name(language: str):
    """Pick a row's name in the UI language, falling back to French.

    The SCF chart ships an Arabic name for a subset of accounts (34 of 294 in
    the seeded plan), so "use Arabic" cannot mean "blank the rest": an empty
    cell in a ledger is unreadable. Each row falls back independently.
    """
    preferred = {"ar": "name_ar", "en": "name_en"}.get(language, "name_fr")

    def formatter(row: Any) -> str:
        if not isinstance(row, dict):
            return str(row or "")
        for key in (preferred, "name_fr", "name_en", "name_ar"):
            value = row.get(key)
            if value:
                return str(value)
        return ""

    return formatter


__all__ = [
    "THIN_SPACE",
    "format_amount",
    "format_confidence",
    "format_count",
    "format_date",
    "format_datetime",
    "format_percent",
    "format_quantity",
    "humanize_identifier",
    "localized_name",
]
