# Phase 39 — Desktop & API acceptance

Environment: CPython **3.12.10** (built from source, per `pyproject.toml` requirement), PySide6 6.11.2,
`QT_QPA_PLATFORM=offscreen` with the Phase 30 stub libraries. Debian 12.

## 1. Results

| Suite | Result |
| --- | --- |
| Desktop suites (`phase21`, `phase26`, `phase27`, `phase28`, `phase29_desktop_redesign`) | **69 passed** |
| API suites (`phase8`, `phase25`, `phase47`, `phase39`) | **263 passed** |
| RTL assertions specifically | **2 passed** |
| Arabic review routing | **19 passed** |
| Whole repository | **946 passed, 3 skipped, 0 failed** |

## 2. Arabic RTL — verified, not asserted from memory

```
languages: ('fr', 'ar', 'en')
is_rtl('ar'): True   |  is_rtl('fr'): False
```

The interface direction is asserted at the **application** level, which is the only place that
actually proves the mirrored layout rather than just translated strings:

```
tests/test_phase26_desktop.py:301   assert app.layoutDirection() == Qt.LayoutDirection.RightToLeft
tests/test_phase29_desktop_redesign.py:814
```

Both pass. This is the requirement "Arabic UI must be genuinely RTL (mirrored layout, right-aligned
text, mirrored tables), not just translated strings" — the assertion is on Qt's layout direction,
which is what mirrors child widgets and tables.

## 3. What was NOT tested

Recorded explicitly so the report cannot be read as claiming more than was done:

| Item | Status | Why |
| --- | --- | --- |
| Windows-native desktop smoke test | **NOT EXECUTED** | this sandbox is Linux; no Windows host is available |
| WCAG / screen-reader conformance | **NOT TESTED** | requires a real assistive-technology harness; no claim is made |
| Visual screenshot comparison | **NOT EXECUTED** | offscreen rendering only; no fabricated screenshots |
| Real display / DPI matrix | **NOT EXECUTED** | `offscreen` platform only |

The 3 skipped tests in the full run are all Windows-only:

```
SKIPPED tests/test_phase12_key_migration_and_backup.py:697: Windows junction regression
SKIPPED tests/test_phase17_key_providers.py:97:  exercises the Windows reparse-point open path
SKIPPED tests/test_phase17_key_providers.py:111: exercises the Windows reparse-point API path
```

They are correctly skipped on Linux rather than silently passing.

## 4. Empty-state rule

The standing constraint "no blank/placeholder UI: an empty cell or blank field must explain itself"
is covered by the desktop suites above (`phase26`/`phase27`/`phase28`/`phase29_desktop_redesign`,
69 tests). No new blank state was introduced by Phase 35: the only UI-adjacent change is the API
`/api/v1/system/rate-limit-policy` endpoint, which is not surfaced in the desktop client.

## 5. API acceptance

`GET /api/v1/system/rate-limit-policy` (new, admin-only) is covered by
`test_the_policy_endpoint_reports_what_the_process_is_doing`, which asserts it returns the active
`auth_failure_limit` and the honest `max_keys_per_scope` bound — not a claim that the limit is
distributed.

The OpenAPI boundary invariant (`test_openapi_contract_declares_exactly_the_public_operations`) was
updated from 26 to 27 protected operations, with a comment, because the new endpoint is protected.
The public/protected split itself is still asserted exactly, so the guard keeps its purpose: it
would catch a route that silently became public.
