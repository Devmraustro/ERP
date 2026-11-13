# SPDX-License-Identifier: LGPL-3.0-only
"""Correlation identifiers for the HTTP surface.

Every request is traced by a correlation id that appears in the response header
(``X-Request-Id``) and in every durable log record for that request. The client
may *propose* one so that a user's bug report maps to a log line without a
search, but a proposed value is input from an untrusted source and cannot be
used as-is:

* it is written verbatim into a JSON log line and echoed in a response header,
  so an unbounded or unusual value is a log-hygiene and observability problem
  (a 4 KB header, embedded newlines, RTL override characters, terminal escapes);
* two clients can propose the same id, which must never cause a *request* to be
  dropped — collisions are a tracing inconvenience, not a correctness rule.

The rule implemented here is deliberately narrow: accept only a bounded,
printable, ASCII token of the shape every tracing system already produces
(uuid4, W3C ``traceparent``-adjacent ids, ULIDs, ``req-123``), and otherwise
generate a server-side uuid4 instead. A rejected value is never echoed, never
logged and never partially absorbed — the response carries the generated id, so
the client learns immediately which id to quote.

No personal data is involved: the value is an opaque token, and the generated
fallback is random (:func:`uuid.uuid4`), not derived from the user, the client
address or anything else about the request.
"""

from __future__ import annotations

import re
import uuid
from typing import Callable, Optional, Tuple

#: Longest supplied id accepted. uuid4 is 36 characters and every id this
#: application generates fits well inside this; 128 leaves room for a caller's
#: own trace ids without offering an attacker an unbounded header.
MAX_REQUEST_ID_LENGTH = 128

#: The accepted character set: ASCII letters, digits, and the four separators
#: used by uuid, W3C trace context, ULIDs and common gateway ids. Notably this
#: excludes whitespace, control characters, quotes, angle brackets, and every
#: non-ASCII code point (which includes the Unicode direction-override and
#: zero-width characters used to disguise log lines in a terminal).
_ALLOWED = re.compile(r"^[A-Za-z0-9._:-]+$")


def is_valid_request_id(value: Optional[str]) -> bool:
    """True when *value* is safe to adopt as a correlation id."""
    if not value or not isinstance(value, str):
        return False
    if len(value) > MAX_REQUEST_ID_LENGTH:
        return False
    return _ALLOWED.match(value) is not None


def sanitize_request_id(
    supplied: Optional[str],
    *,
    generator: Optional[Callable[[], str]] = None,
) -> Tuple[str, bool]:
    """Return ``(request_id, accepted)`` for a client-supplied id.

    ``accepted`` is ``False`` when the supplied value was missing, empty,
    over-long or outside the allowed character set; the returned id is then a
    freshly generated one. When ``supplied`` is valid it is returned unchanged,
    so a caller's own tracing still joins up.
    """
    if is_valid_request_id(supplied):
        return str(supplied), True
    make = generator or (lambda: str(uuid.uuid4()))
    return make(), False


__all__ = [
    "MAX_REQUEST_ID_LENGTH",
    "is_valid_request_id",
    "sanitize_request_id",
]
