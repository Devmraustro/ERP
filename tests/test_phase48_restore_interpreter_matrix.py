# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 48 — the safe-backup/restore interpreter matrix (finding R-07).

The problem this phase exists to close is not "does restore work on my machine",
it is "which interpreters are *allowed* to claim it works, and is that rule the
same rule the code enforces". Before this phase the rule existed four times:

* in ``security_audit.restore_and_verify_backup``;
* in ``test_phase32`` as ``>= (3, 11, 17) and < (3, 13)`` — **wrong**: it
  admits 3.12.0-3.12.9, where restore correctly refuses;
* in ``test_phase41`` as a 3.11/3.12 pair of the correct shape;
* in ``test_phase12`` and elsewhere, implicitly, as individual ``monkeypatch``
  literals.

The wrong copy was invisible in CI only because ``actions/setup-python`` with
``3.12`` resolves the newest 3.12. That is exactly the "green CI, untrue claim"
failure mode this remediation is supposed to prevent, so the rule was moved into
one predicate — :func:`dz_erp.security_audit.safe_restore_supported` — and this
phase tests *the predicate over its whole version space*, not merely whatever
interpreter the suite happens to run on. A test that can only ever observe one
point of a version matrix is not evidence about the matrix.

Everything the removal of the interpreter gate would buy is refused: the gate is
asserted to be *fail-closed* on every unsupported tuple, and the restore path is
asserted to raise rather than degrade to an unsafe extraction.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import dz_erp.security_audit as security_audit
from dz_erp.database import ERPDatabase, FinancialIntegrityError
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.security_audit import (
    SAFE_RESTORE_MAX_EXCLUSIVE,
    SAFE_RESTORE_MINIMUMS,
    SAFE_RESTORE_UNSUPPORTED_MESSAGE,
    create_encrypted_backup,
    restore_and_verify_backup,
    safe_restore_supported,
)

#: The supported lines, verbatim from the requirement: Python 3.11.17+ (the
#: supported 3.11 line) and 3.12; 3.13+ is excluded because the pinned OCR
#: dependency does not support it, and is refused rather than guessed at.
SUPPORTED_MATRIX = [
    (3, 11, 17),
    (3, 11, 18),
    (3, 11, 29),
    (3, 12, 0),
    (3, 12, 9),
    (3, 12, 10),
    (3, 12, 16),
    (3, 12, 99),
]
EXPECTED_SUPPORTED = {
    (3, 11, 17): True,
    (3, 11, 18): True,
    (3, 11, 29): True,
    (3, 12, 0): False,   # the bypasses are present until 3.12.10
    (3, 12, 9): False,
    (3, 12, 10): True,
    (3, 12, 16): True,
    (3, 12, 99): True,
}
UNSUPPORTED_MATRIX = [
    (3, 8, 20),
    (3, 9, 19),
    (3, 10, 14),
    (3, 11, 0),
    (3, 11, 16),
    (3, 13, 0),
    (3, 13, 7),
    (3, 14, 0),
    (4, 0, 0),
]


def test_the_matrix_is_the_supported_lines_and_nothing_else() -> None:
    assert set(SAFE_RESTORE_MINIMUMS) == {(3, 11), (3, 12)}
    assert SAFE_RESTORE_MINIMUMS[(3, 11)] == (3, 11, 17)
    assert SAFE_RESTORE_MINIMUMS[(3, 12)] == (3, 12, 10)
    assert SAFE_RESTORE_MAX_EXCLUSIVE == (3, 13, 0)


@pytest.mark.parametrize("version", SUPPORTED_MATRIX)
def test_supported_points_are_exactly_where_the_requirement_puts_them(
    version: tuple[int, int, int],
) -> None:
    assert safe_restore_supported(version) is EXPECTED_SUPPORTED[version], version


@pytest.mark.parametrize("version", UNSUPPORTED_MATRIX)
def test_every_unsupported_point_is_refused(version: tuple[int, int, int]) -> None:
    assert safe_restore_supported(version) is False, version


def test_the_boundary_is_inclusive_at_the_patch_and_exclusive_at_313() -> None:
    """Both edges, stated separately so a future edit cannot move one quietly."""
    # Just below / exactly at each line's first patched release.
    assert safe_restore_supported((3, 11, 16)) is False
    assert safe_restore_supported((3, 11, 17)) is True
    assert safe_restore_supported((3, 12, 9)) is False
    assert safe_restore_supported((3, 12, 10)) is True
    # The upper edge: supported below 3.13, refused from it on.
    assert safe_restore_supported((3, 12, 99)) is True
    assert safe_restore_supported((3, 13, 0)) is False


def test_a_patch_free_version_tuple_is_handled() -> None:
    """``sys.version_info[:2]`` style input must not be treated as "modern"."""
    assert safe_restore_supported((3, 11)) is False
    assert safe_restore_supported((3, 13)) is False


# ---------------------------------------------------------------------------
# 2. the gate is the one the restore path uses, and it fails closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", UNSUPPORTED_MATRIX)
def test_restore_refuses_on_every_unsupported_interpreter(
    version: tuple[int, int, int], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate must raise, never fall back to an unfiltered extraction."""
    monkeypatch.setattr(security_audit.sys, "version_info", version)

    with pytest.raises(FinancialIntegrityError) as refused:
        restore_and_verify_backup(
            backup_path=tmp_path / "does-not-exist.enc",
            backup_encryption_key=b"0" * 32,
            restore_target_dir=tmp_path / "restore",
        )

    # Refused *for the interpreter*, before any file was touched: the message is
    # the gate's, not a "backup not found" error, and no target was created.
    assert SAFE_RESTORE_UNSUPPORTED_MESSAGE in str(refused.value)
    assert not (tmp_path / "restore").exists()


def _is_int_tuple(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Tuple)
        and len(node.elts) in {2, 3}
        and all(isinstance(e, ast.Constant) and isinstance(e.value, int) for e in node.elts)
    )


def _version_comparisons(tree: ast.AST) -> list[ast.Compare]:
    """Every *interpreter-version gate* in a syntax tree.

    The shape is narrow on purpose, because a broad "compares something called
    version" heuristic flags ordinary domain code (a vault key version, a
    dependency's pinned version). A gate is one of exactly two things:

    * a comparison against ``<something>.version_info``, or
    * a comparison of a ``version``-ish name against a literal integer tuple
      ``(3, 11, 17)`` / ``(3, 12)``.

    A ``monkeypatch.setattr(..., version_info=(3, 12, 10))`` literal is a
    fixture value, not a policy decision, so it is deliberately not matched:
    only the *decision* is the duplicate that R-07 is about.
    """
    found: list[ast.Compare] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        if any(isinstance(o, ast.Attribute) and o.attr == "version_info" for o in operands):
            found.append(node)
            continue
        touches_version_name = any(
            isinstance(o, ast.Name) and "version" in o.id.lower() for o in operands
        )
        if touches_version_name and any(_is_int_tuple(o) for o in operands):
            found.append(node)
    return found


def test_the_library_gate_has_exactly_one_rule_and_it_is_the_shared_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The restore path must call the predicate, and hold no rule of its own."""
    source = Path(security_audit.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    # The rule lives in data (SAFE_RESTORE_MINIMUMS) + one predicate; a
    # comparison against a version attribute anywhere else is a second copy.
    assert _version_comparisons(tree) == [], [
        ast.dump(node)[:90] for node in _version_comparisons(tree)
    ]

    restore_fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "restore_and_verify_backup"
    )
    called = {
        node.func.id
        for node in ast.walk(restore_fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "safe_restore_supported" in called, (
        "restore_and_verify_backup must ask the shared predicate, not re-derive the rule"
    )

    # And the predicate itself follows the monkeypatched interpreter.
    monkeypatch.setattr(security_audit.sys, "version_info", (3, 11, 16))
    assert safe_restore_supported() is False
    monkeypatch.setattr(security_audit.sys, "version_info", (3, 11, 17))
    assert safe_restore_supported() is True
    monkeypatch.setattr(security_audit.sys, "version_info", (3, 13, 0))
    assert safe_restore_supported() is False


def test_no_test_module_reimplements_the_interpreter_rule() -> None:
    """The drift that caused R-07 is the *duplicate*, so the duplicate is banned.

    This is the regression test for the actual defect: ``test_phase32`` carried
    its own ``>= (3, 11, 17) and < (3, 13)`` gate, which admits 3.12.0-3.12.9.
    Every module that needs the rule must import it.
    """
    tests_dir = Path(__file__).parent
    offenders: list[str] = []
    for path in sorted(tests_dir.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        duplicates = _version_comparisons(tree)
        if duplicates:
            offenders.append(
                f"{path.name}: "
                + ", ".join(ast.unparse(node) for node in duplicates)
            )
    assert offenders == [], (
        "these tests hand-roll the interpreter rule instead of calling "
        f"dz_erp.security_audit.safe_restore_supported: {offenders}"
    )


# ---------------------------------------------------------------------------
# 3. a real round trip, on whatever supported interpreter is running
# ---------------------------------------------------------------------------

HOST_IS_SUPPORTED = safe_restore_supported()


def test_a_real_backup_restore_round_trip_is_verified_on_this_interpreter(
    tmp_path: Path,
) -> None:
    """The same assertions on 3.11.17+ and 3.12.10+ — or an explicit skip.

    This is deliberately an *environment-gated* test with the verdict named, not
    a test that quietly passes by doing nothing: on an unsupported interpreter
    the evidence is recorded as environment-blocked (and the fail-closed test
    above still runs), never as a pass.

    The assertions are the ones the requirement names, and nothing weaker: the
    restored database is verified by content hash, its audit chain verifies, the
    vault round-trips, and the restored artefacts reopen without the original
    process. An earlier version of this test guessed at the report's keys and
    would have failed on the interpreter where the path actually runs — the exact
    failure mode R-07 is about, so the keys are asserted explicitly.
    """
    if not HOST_IS_SUPPORTED:
        # A skip is honest on a developer's host (this sandbox runs 3.11.2, where
        # restore correctly refuses). On CI it would be a silent hole: the job
        # would be green while the requirement's central path never executed.
        # CI is *supposed* to run on a supported interpreter, so there it fails.
        if os.environ.get("CI"):
            pytest.fail(
                "CI is running the safe-restore round trip on an interpreter that "
                f"cannot perform it ({sys.version_info.major}.{sys.version_info.minor}."
                f"{sys.version_info.micro}). The gate is deliberate and must not be "
                "disabled; fix the matrix instead of skipping the evidence."
            )
        pytest.skip(
            "safe restore is refused on this interpreter by design; the fail-closed "
            "behaviour is asserted above and the round trip is exercised by CI on "
            "both supported lines"
        )

    from cryptography.fernet import Fernet

    db = ERPDatabase(str(tmp_path / "p48.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )

    key = Fernet.generate_key()
    backup = tmp_path / "backup.enc"
    meta = create_encrypted_backup(db, vault, backup, key)
    assert backup.exists() and meta

    report = restore_and_verify_backup(
        backup_path=Path(meta["backup_path"]),
        backup_encryption_key=key,
        restore_target_dir=tmp_path / "restored",
    )

    # The report's contract, named explicitly rather than probed optimistically.
    for expected_key in ("restored_db", "restored_vault", "db_sha256_verified",
                         "vault_files_verified", "audit_chains_valid"):
        assert expected_key in report, f"restore report lost {expected_key}: {sorted(report)}"
    assert report["db_sha256_verified"] is True
    assert report["audit_chains_valid"] is True
    # No document was filed in this vault, so the manifest lists none: the count
    # is the number of vault hashes that verified, not a constant.
    assert report["vault_files_verified"] == 0

    # ``restored_db`` is a live object (this is what the R-07 defect got wrong);
    # the data must be readable through it, not through a guessed path.
    restored_db = report["restored_db"]
    with restored_db.transaction() as conn:
        row = conn.execute("SELECT name FROM companies WHERE id = 1").fetchone()
    assert row["name"] == "Tenant Alpha", "the restored tenant row must round-trip"

    # The restore is isolated: the live source database is untouched by it.
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM companies").fetchone()["n"] == 1


def test_the_host_interpreter_is_recorded_rather_than_assumed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Printing the verdict makes a silent matrix change visible in CI logs."""
    import sys

    verdict = (
        f"R-07 interpreter={sys.version_info.major}.{sys.version_info.minor}."
        f"{sys.version_info.micro} safe_restore_supported={HOST_IS_SUPPORTED}"
    )
    print(verdict)
    captured = capsys.readouterr().out
    assert "safe_restore_supported=" in captured
    assert isinstance(HOST_IS_SUPPORTED, bool)


def _unused(_value: object) -> None:  # pragma: no cover - keeps SimpleNamespace import honest
    SimpleNamespace()
