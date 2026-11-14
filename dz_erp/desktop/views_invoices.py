# SPDX-License-Identifier: LGPL-3.0-only
"""Supplier invoices, the OCR review workspace and invoice creation (Phase 29).

The workflow is unchanged - and that is the point. Every state transition is
still the server's decision:

    Draft ──submit-approval──▶ PendingApproval ──decide-approval──▶ Approved
      │                              │                                │
      └──────────── Rejected ◀───────┘                                ▼
                                                              post ──▶ Posted
                                                                      │
                                                        reverse ◀─────┘

The UI never infers a transition. It calls the endpoint, reloads the invoice
and renders the state the server returns; a refusal (segregation of duties,
duplicate posting, an unreviewed OCR value, a role denial) is displayed as
the server's own message and the invoice stays where it was.

Surfaced-but-not-invented values
--------------------------------
* **Extracted vs verified.** A field with ``human_override_value`` was
  corrected by a person: it is badged *verified* and the raw OCR text is
  still shown beside it. Untouched fields are badged *extracted*.
* **Confidence** is printed exactly as stored, or the literal "not measured".
  No threshold, score bar or "safe/unsafe" verdict is invented - the schema
  has no review threshold to render.
* **Validation warnings** come from the server's ``validation_errors_json``.
* Buttons are enabled from the *stored* workflow state, so an action the
  server would refuse is not offered as if it would work.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import tokens
from .api_client import ErpApiError, ErpClient
from .components import (
    Banner,
    Button,
    Card,
    Column,
    DataTable,
    PageHeader,
    Pill,
    label,
    muted,
    section_title,
)
from .formatting import (
    format_amount,
    format_confidence,
    format_quantity,
    format_row_count,
    humanize_identifier,
)
from .i18n import STATE_KEYS, state_label, tr
from .workers import WorkerOwner


def _message_box():
    """The window module's ``QMessageBox``.

    The Phase 27/28 suites replace ``ui.QMessageBox`` to run modal boxes
    headless; routing through it keeps a single interception point, so a
    refusal can never escape the harness as a real modal window.
    """
    from . import ui as ui_module

    return ui_module.QMessageBox


#: Allowed transitions, read off the server state machine
#: (``procurement_stock.request_approval`` / ``decide_approval`` /
#: ``post_invoice``) **for button enablement only**. The server remains the
#: authority; this table exists so the UI does not offer an action that is
#: certain to fail, and so it cannot offer one the server would allow for a
#: state it does not list.
ACTIONS_BY_STATE: Dict[str, Tuple[str, ...]] = {
    # a freshly created invoice is "Validated" once its arithmetic checks pass
    "Validated": ("submit", "review"),
    "NeedsReview": ("review",),
    "PendingApproval": ("approve", "reject", "review"),
    "Approved": ("post", "review"),
    "Posted": ("review",),
    "Rejected": ("review",),
    "Reversed": ("review",),
    "DuplicateRejected": ("review",),
}

_STATE_TONE: Dict[str, str] = {
    "Draft": "neutral",
    "Validated": "success",
    "DuplicateRejected": "danger",
    "NeedsReview": "warning",
    "PendingApproval": "info",
    "Approved": "accent",
    "Posted": "success",
    "Rejected": "danger",
    "Reversed": "neutral",
}


def state_pill(language: str, state: str) -> Pill:
    """A pill carrying the localised state label and a semantic tone."""
    pill = Pill(state_label(language, state) or humanize_identifier(state), _STATE_TONE.get(state, "neutral"))
    return pill


def _parse_validation_errors(raw: Any) -> List[str]:
    """Server-side validation warnings, as text; never re-interpreted."""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return [str(raw)]
    if isinstance(payload, dict):
        return [f"{key}: {value}" for key, value in payload.items()]
    if isinstance(payload, (list, tuple)):
        return [str(item) for item in payload]
    return [str(payload)]


# ----------------------------------------------------------------------
# invoices list
# ----------------------------------------------------------------------


class InvoicesView(QWidget, WorkerOwner):
    """Invoice list with state filter, search and the review/create actions."""

    def __init__(self, theme_manager, client) -> None:
        super().__init__()
        self._theme = theme_manager
        self._client = client
        self._init_workers()
        self._on_review = None
        self._on_create = None
        self._partner_names: Dict[int, str] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            tokens.SPACING["xxl"], tokens.SPACING["xl"], tokens.SPACING["xxl"], tokens.SPACING["lg"]
        )
        outer.setSpacing(tokens.SPACING["md"])

        self.header = PageHeader("")
        self.review_button = Button(
            "",
            variant="primary",
            icon=theme_manager.icon("review", "text_inverse", tokens.ICON_SIZE["md"]),
            on_click=lambda: self._forward(self._on_review),
        )
        self.create_button = Button(
            "",
            variant="default",
            icon=theme_manager.icon("plus", "text", tokens.ICON_SIZE["md"]),
            on_click=lambda: self._forward(self._on_create),
        )
        self.header.add_action(self.create_button)
        self.header.add_action(self.review_button)
        outer.addWidget(self.header)

        self.banner = Banner("", "warning")
        self.banner.setVisible(False)
        outer.addWidget(self.banner)

        self.table = DataTable(
            [],
            search_keys=("supplier_invoice_no", "supplier_name", "workflow_state"),
            filter_key="workflow_state",
            page_size=100,
        )
        self.table.filter.setMinimumWidth(180)
        self.table.rowActivated.connect(lambda _row: self._forward(self._on_review))
        self.table.set_auto_widths()
        outer.addWidget(self.table, 1)

        self.footer = muted("")
        outer.addWidget(self.footer)

        self.retranslate()

    def set_actions(self, on_review, on_create) -> None:
        self._on_review = on_review
        self._on_create = on_create

    def _forward(self, handler) -> None:
        if handler is not None:
            handler()

    def selected_invoice_id(self) -> Optional[int]:
        row = self.table.selected_row()
        if row is None:
            return None
        try:
            return int(row.get("id"))
        except (TypeError, ValueError):
            return None

    # -- i18n ----------------------------------------------------------

    def retranslate(self) -> None:
        language = self._theme.language
        self.header.set_heading(tr(language, "nav_invoices"), tr(language, "page_invoices_sub"))
        self.review_button.setText(tr(language, "review_button"))
        self.create_button.setText(tr(language, "invoice_create"))
        columns = [
            # No fixed widths: identifiers and DZD amounts are measured, so
            # neither an invoice number nor a ledger figure is ever elided.
            Column("supplier_invoice_no", tr(language, "col_invoice_no"), mono=True),
            Column("supplier_name", tr(language, "col_supplier")),
            Column("invoice_date", tr(language, "col_date")),
            Column("workflow_state", tr(language, "col_state"), formatter=lambda v: state_label(language, v)),
            Column("amount_ht_dzd", tr(language, "col_amount_ht"), align="right", formatter=lambda v: format_amount(v, currency="DZD")),
            Column("amount_tva_dzd", tr(language, "col_amount_tva"), align="right", formatter=lambda v: format_amount(v, currency="DZD")),
            Column("amount_ttc_dzd", tr(language, "col_amount_ttc"), align="right", formatter=lambda v: format_amount(v, currency="DZD")),
        ]
        self.table.table.setColumnCount(len(columns))
        self.table.table.setHorizontalHeaderLabels([column.header for column in columns])
        self.table._columns = columns
        self.table.set_placeholders(tr(language, "shell_search"), tr(language, "state_empty_invoices"))
        self.table.set_language(language)
        self.table.set_direction(self._theme.rtl)
        self.footer.setText(tr(language, "dash_source_note"))
        self._refresh_filter_labels()

    def _refresh_filter_labels(self) -> None:
        language = self._theme.language
        current = self.table.filter.currentData()
        self.table.filter.blockSignals(True)
        self.table.filter.clear()
        self.table.filter.addItem(tr(language, "button_filter_all"), "")
        for state in STATE_KEYS:
            if state in ("CLEAN",):
                continue
            self.table.filter.addItem(state_label(language, state), state)
        index = self.table.filter.findData(current) if current else 0
        self.table.filter.setCurrentIndex(max(0, index))
        self.table.filter.blockSignals(False)

    # -- loading -------------------------------------------------------

    def refresh(self) -> None:
        if not self._client.is_logged_in:
            return
        self.table.set_loading(tr(self._theme.language, "loading"))
        self.run_async(self._load, on_success=self._apply, on_error=self._failed)

    def _load(self) -> Dict[str, Any]:
        invoices = self._client.invoices(limit=100)
        names: Dict[int, str] = {}
        try:
            for partner in self._client.partners(limit=500).get("partners", []):
                if partner.get("id") is not None:
                    names[int(partner["id"])] = str(partner.get("name", ""))
        except ErpApiError:
            names = {}
        return {"invoices": invoices, "partner_names": names}

    def _apply(self, payload: Dict[str, Any]) -> None:
        language = self._theme.language
        self._partner_names = payload.get("partner_names") or {}
        rows = []
        for row in (payload.get("invoices") or {}).get("invoices", []):
            item = dict(row)
            supplier_id = item.get("supplier_id")
            try:
                item["supplier_name"] = self._partner_names.get(int(supplier_id), str(supplier_id or ""))
            except (TypeError, ValueError):
                item["supplier_name"] = str(supplier_id or "")
            rows.append(item)
        self.table.set_rows(rows)
        self.banner.setVisible(False)
        pending = sum(1 for row in rows if row.get("workflow_state") in ("Draft", "NeedsReview", "PendingApproval"))
        if pending:
            self.banner.set_text(tr(language, "pending_review_count", count=format_row_count(pending, language)))
            self.banner.setVisible(True)

    def _failed(self, exc: object) -> None:
        language = self._theme.language
        message = tr(language, "state_load_failed", detail=str(exc))
        self.table.set_error(message, on_retry=self.refresh)
        if isinstance(exc, ErpApiError) and exc.status_code in (401, 403):
            self.banner.set_text(message)
            self.banner.setVisible(True)


# ----------------------------------------------------------------------
# review workspace
# ----------------------------------------------------------------------


class InvoiceReviewDialog(QDialog, WorkerOwner):
    """Full review workspace: header, OCR fields, lines, preview and actions.

    Kept as a ``QDialog`` (as in Phases 27-28) so the existing tests and the
    window→dialog seam keep working; the *content* is the redesigned
    workspace: a three-pane layout with the extracted fields and their
    provenance, the invoice lines, the source-document preview, the
    validation warnings, and only the actions the stored state allows.
    """

    # Server-evaluated review policy for this document, replaced wholesale on
    # every refresh. Declared at class level as well as in ``__init__`` so a
    # provenance lookup always reads "unknown" instead of raising
    # AttributeError - including in tests that assemble the dialog without
    # running the constructor.
    _policy: Dict[str, Any] = {}

    def __init__(
        self,
        lang: str,
        client: ErpClient,
        invoice_row: Dict[str, Any],
        parent: Any = None,
        *,
        theme_manager=None,
    ) -> None:
        super().__init__(parent)
        self._lang = lang
        self._client = client
        self._invoice_id = int(invoice_row["id"])
        self._detail: Dict[str, Any] = {}
        # Per-instance copy: the class-level default is shared state, so each
        # dialog must own its own policy dict.
        self._policy = {}
        self._theme = theme_manager
        self._init_workers()
        self.setWindowTitle(tr(lang, "review_title"))
        self.resize(1180, 760)
        self.setMinimumSize(940, 620)
        self._state = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(tokens.SPACING["xl"], tokens.SPACING["lg"], tokens.SPACING["xl"], tokens.SPACING["lg"])
        root.setSpacing(tokens.SPACING["md"])

        # -- header row: title, state pill, warnings
        head = QHBoxLayout()
        head.setSpacing(tokens.SPACING["sm"])
        title_column = QVBoxLayout()
        title_column.setSpacing(tokens.SPACING["xxs"])
        self.title_label = label(tr(lang, "review_title"), object_name="PageTitle")
        self.subtitle_label = label("", object_name="PageSubtitle", wrap=True)
        title_column.addWidget(self.title_label)
        title_column.addWidget(self.subtitle_label)
        head.addLayout(title_column, 1)
        self.state_badge = Pill("", "neutral")
        self.state_badge.setMinimumWidth(120)
        head.addWidget(self.state_badge, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        self.warning_banner = Banner("", "warning")
        self.warning_banner.setVisible(False)
        root.addWidget(self.warning_banner)

        # Phase 59B: an operator cannot fix what the screen does not name. The
        # server already evaluates the review policy; until now the dialog let
        # a reviewer press "submit" and answered only with a modal refusal that
        # listed blockers in raw API vocabulary. The banner names them up
        # front, in the operator's language, from the server's own verdict.
        self.blockers_banner = Banner("", "danger")
        self.blockers_banner.setVisible(False)
        root.addWidget(self.blockers_banner)

        # -- body: fields | (lines + preview)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(tokens.SPACING["sm"])
        self.fields_title = section_title(tr(lang, "review_fields"))
        left_layout.addWidget(self.fields_title)
        self.fields_table = DataTable(
            [
                # No fixed widths: this table measures its content so a full
                # invoice number, NIF or confidence is never elided. The
                # reviewer must see raw OCR, current value and correction side
                # by side, not scroll between them. Provenance ("verified by a
                # human, when, by whom") is prose, so it is shown in the
                # selected-field panel below rather than stealing width here.
                Column("field_name", tr(lang, "review_field"), mono=True),
                Column("raw_text", tr(lang, "review_raw_text")),
                Column("current_value", tr(lang, "review_value"), mono=True),
                Column("confidence_text", tr(lang, "review_confidence")),
                Column("origin", tr(lang, "review_origin")),
            ],
            search_keys=("field_name", "raw_text", "current_value"),
            page_size=200,
        )
        self.fields_table.set_compact(True)
        self.fields_table.set_auto_widths(min_width=90, max_width=230)
        left_layout.addWidget(self.fields_table, 1)

        correction_row = QHBoxLayout()
        correction_row.setSpacing(tokens.SPACING["sm"])
        self.provenance_label = label("", object_name="Muted", wrap=True)
        left_layout.addWidget(self.provenance_label)
        # Shown only when the selected policy field has no extracted value:
        # typing a value for a field the engine missed is manual
        # transcription, and the operator has to be told that is what it is.
        self.missing_hint = label("", object_name="Muted", wrap=True)
        self.missing_hint.setVisible(False)
        left_layout.addWidget(self.missing_hint)
        self.fields_table.on_selection(self._show_provenance)

        self.correction_edit = QLineEdit()
        self.correction_edit.setPlaceholderText(tr(lang, "review_value"))
        self.correction_button = Button(
            tr(lang, "review_correct_button"), on_click=self._correct_selected_field
        )
        correction_row.addWidget(self.correction_edit, 1)
        self.correction_button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        correction_row.addWidget(self.correction_button, 0)
        left_layout.addLayout(correction_row)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(tokens.SPACING["sm"])
        self.lines_title = section_title(tr(lang, "review_lines"))
        right_layout.addWidget(self.lines_title)
        self.lines_table = DataTable(
            [
                Column("description", tr(lang, "col_name")),
                Column("qty", tr(lang, "col_qty"), align="right", formatter=format_quantity),
                Column(
                    "unit_price_dzd",
                    tr(lang, "col_unit_price"),
                    align="right",
                    formatter=lambda v: format_amount(v, currency="DZD"),
                ),
            ],
            page_size=50,
        )
        self.lines_table.set_compact(True)
        self.lines_table.set_auto_widths(min_width=80, max_width=280)
        right_layout.addWidget(self.lines_table, 1)

        self.preview_title = section_title(tr(lang, "review_preview"))
        right_layout.addWidget(self.preview_title)
        self.preview_label = label(tr(lang, "doc_preview_none"), object_name="Muted")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(220)
        self.preview_label.setScaledContents(False)
        right_layout.addWidget(self.preview_label, 1)

        header_card = Card(tr(lang, "review_header"), object_name="CardFlat", body_margins=(12, 12, 12, 12))
        header_form = QFormLayout()
        header_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        header_form.setHorizontalSpacing(tokens.SPACING["lg"])
        header_form.setVerticalSpacing(tokens.SPACING["xs"])
        self.header_values: Dict[str, QWidget] = {}
        for key in ("col_supplier", "col_invoice_no", "col_date", "col_amount_ht", "col_amount_tva", "col_amount_ttc"):
            value = label("", object_name="FieldValue")
            self.header_values[key] = value
            header_form.addRow(label(tr(lang, key), object_name="FieldLabel"), value)
        header_card.add_layout(header_form)
        right_layout.addWidget(header_card)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([740, 460])
        root.addWidget(splitter, 1)

        # -- actions
        root.addWidget(label(tr(lang, "review_actions"), object_name="FieldLabel"))
        action_row = QHBoxLayout()
        action_row.setSpacing(tokens.SPACING["sm"])
        self.reason_edit = QLineEdit()
        self.reason_edit.setPlaceholderText(tr(lang, "decision_reason"))
        action_row.addWidget(self.reason_edit, 1)
        self.submit_button = Button(tr(lang, "submit_approval"), on_click=self._submit_approval)
        self.approve_button = Button(
            tr(lang, "approve_button"), variant="primary", on_click=lambda: self._decide(True)
        )
        self.reject_button = Button(
            tr(lang, "reject_button"), variant="danger", on_click=lambda: self._decide(False)
        )
        self.post_button = Button(
            tr(lang, "post_button"), variant="accent", on_click=self._post_invoice
        )
        for button in (self.submit_button, self.approve_button, self.reject_button, self.post_button):
            # Elided action labels ("Corriger…", "Soumettre à validation") are
            # unreadable and hide which control does what: size to the text.
            button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            action_row.addWidget(button, 0)
        close_button = Button(tr(lang, "button_close"), variant="ghost", on_click=self.reject)
        action_row.addWidget(close_button, 0)
        root.addLayout(action_row)

        self.refresh()

    # -- review policy blockers (Phase 59B) -----------------------------

    def _render_review_blockers(self, policy: Optional[Dict[str, Any]], language: str) -> None:
        """Show the server's unsatisfied review requirements, in operator words.

        The dialog used to offer "submit" and answer only with a modal that
        repeated the API's raw reason string. A reviewer then had to guess
        which of several fields still needed attention. The verdict is
        already computed server-side (``GET .../review-policy``); this renders
        it and hides the banner the moment the policy is satisfied, so the
        screen never claims a blocker that no longer exists.
        """
        self._policy = policy or {}
        blocking = [str(item) for item in (self._policy.get("blocking") or [])]
        if not blocking or self._policy.get("satisfied"):
            self.blockers_banner.setVisible(False)
            return
        lines: List[str] = []
        for item in blocking:
            kind, _, detail = item.partition(":")
            detail = detail.strip()
            if kind == "REQUIRED_FIELD_NOT_DECIDED":
                lines.append(tr(language, "review_blocker_required", name=detail))
            elif kind == "FIELD_FLAGGED":
                lines.append(tr(language, "review_blocker_flagged", name=detail))
            elif kind == "DUPLICATE_EXTRACTION_ROWS":
                lines.append(tr(language, "review_blocker_duplicate", name=detail))
            elif kind == "INCONSISTENT_EXTRACTED_TOTALS":
                lines.append(tr(language, "review_blocker_totals", detail=detail))
            else:
                lines.append(tr(language, "review_blocker_other", detail=item))
        self.blockers_banner.set_text(
            tr(language, "review_blockers_title") + " " + " · ".join(lines)
        )
        self.blockers_banner.setVisible(True)

    def _selected_policy_field(self, field_name: str) -> Optional[Dict[str, Any]]:
        for row in self._policy.get("fields") or []:
            if str(row.get("field_name")) == field_name:
                return dict(row)
        return None

    # -- selection -----------------------------------------------------

    def _show_provenance(self, row: Optional[Dict[str, Any]]) -> None:
        """Full provenance for the selected field: who corrected it, when."""
        if not row:
            self.provenance_label.setText("")
            language = self._theme.language if self._theme else self._lang
            self.correction_edit.clear()
            self.correction_edit.setPlaceholderText(tr(language, "review_value"))
            return
        name = str(row.get("field_name", ""))
        origin = str(row.get("origin", ""))
        detail = str(row.get("provenance", ""))
        self.provenance_label.setText(f"{name} · {origin}" + (f" · {detail}" if detail and detail != "—" else ""))
        # Prefill the correction box with the current value: correcting a
        # digit should not require retyping a 15-digit NIF. An empty value is
        # not an empty state: the box says which field has no value yet, so a
        # blank input next to "Corriger…" is never mistaken for a broken read.
        value = str(row.get("current_value", "") or "")
        self.correction_edit.setText(value)
        language = self._theme.language if self._theme else self._lang
        self.correction_edit.setPlaceholderText(
            tr(language, "review_value") if value else tr(language, "review_value_missing", field=name)
        )
        # A policy field the engine never extracted has no row to click, so the
        # operator cannot pre-select it in the table. Tell them, on the field
        # that is selected, that manual transcription is the expected action.
        policy_row = self._selected_policy_field(name)
        missing = policy_row is not None and policy_row.get("value") in (None, "")
        self.missing_hint.setText(tr(language, "review_missing_hint") if missing else "")
        self.missing_hint.setVisible(bool(missing))

    # -- state ---------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        for button in (
            self.correction_button,
            self.submit_button,
            self.approve_button,
            self.reject_button,
            self.post_button,
        ):
            button.setEnabled(not busy)
        self.correction_edit.setEnabled(not busy)
        self.reason_edit.setEnabled(not busy)
        # Clearing "busy" re-arms every button; the workflow state may still
        # forbid some of them, so the gate is re-applied here too. A busy
        # toggle must never resurrect an action the state does not allow.
        # Before the first load ``_state`` is empty and the gate would hide
        # every action, which ``refresh()`` would then undo a moment later;
        # skip it there and let the load's own call apply the real state.
        if not busy and self._state:
            self._apply_action_state()

    def _apply_action_state(self) -> None:
        """Enable only the actions the stored workflow state allows."""
        # An unknown state offers no workflow action at all: guessing would
        # arm a button the server is likely to refuse.
        allowed = ACTIONS_BY_STATE.get(self._state, ("review",))
        self.submit_button.setEnabled("submit" in allowed)
        self.approve_button.setEnabled("approve" in allowed)
        self.reject_button.setEnabled("reject" in allowed)
        self.post_button.setEnabled("post" in allowed)
        # A disabled control must say why, never look broken.
        for button, action, key in (
            (self.submit_button, "submit", "submit_approval"),
            (self.approve_button, "approve", "approve_button"),
            (self.reject_button, "reject", "reject_button"),
            (self.post_button, "post", "post_button"),
        ):
            button.setToolTip("" if action in allowed else tr(self._lang, "review_state", state=self._state))

    # -- loading -------------------------------------------------------

    def refresh(self) -> None:
        self._set_busy(True)

        def load() -> Dict[str, Any]:
            detail = self._client.invoice(self._invoice_id)
            document_id = detail.get("document_id")
            partner_names = {
                int(p["id"]): str(p.get("name", ""))
                for p in self._client.partners(limit=500).get("partners", [])
                if p.get("id") is not None
            }
            fields: Dict[str, Any] = {"fields": [], "count": 0}
            doc_state: Optional[str] = None
            preview: Optional[Dict[str, Any]] = None
            policy: Optional[Dict[str, Any]] = None
            if document_id:
                try:
                    fields = self._client.document_fields(str(document_id))
                    try:
                        policy = self._client.document_review_policy(str(document_id))
                    except ErpApiError as exc:
                        # A role that may read the document but not the policy
                        # still gets the fields (Phase 27 behaviour); the
                        # blocker banner then simply stays hidden.
                        if exc.status_code in (401, 403):
                            raise
                        policy = None
                    for row in self._client.documents(limit=500).get("documents", []):
                        if row.get("id") == str(document_id):
                            doc_state = str(row.get("workflow_state", ""))
                            break
                    preview = self._client.document_content(str(document_id))
                except ErpApiError as exc:
                    if exc.status_code in (401, 403):
                        raise
                    preview = {"error": exc.detail}
            return {
                "detail": detail,
                "fields": fields,
                "doc_state": doc_state,
                "preview": preview,
                "partner_names": partner_names,
                "policy": policy,
            }

        self.run_async(load, on_success=self._apply_loaded, on_error=self._show_action_error)

    def _document_id(self) -> Optional[str]:
        value = self._detail.get("document_id")
        return str(value) if value else None

    def _apply_loaded(self, data: Dict[str, Any]) -> None:
        language = self._lang
        self._detail = data["detail"]
        invoice = self._detail.get("invoice", self._detail)
        partner_names = data.get("partner_names") or {}
        try:
            supplier_label = partner_names.get(int(invoice.get("supplier_id")))
        except (TypeError, ValueError):
            supplier_label = None
        self._state = str(invoice.get("workflow_state", ""))
        # The badge carries the *document* state (that is what the Phase 27
        # contract reads) while the subtitle names both it and the invoice
        # workflow state, so neither is hidden.
        badge_state = str(data.get("doc_state") or self._state)
        # The badge shows the server's state identifier verbatim and, beside
        # it, the localised label - never a translated substitute. The
        # identifier is what an operator quotes to support; the label is what
        # they read. (Phase 27's contract asserts the identifier is present.)
        localized = state_label(language, badge_state)
        badge_text = tr(language, "review_state", state=badge_state)
        if localized and localized != badge_state:
            badge_text = f"{badge_text} · {localized}"
        self.state_badge.setText(badge_text)
        self.state_badge.set_state(_STATE_TONE.get(badge_state, _STATE_TONE.get(self._state, "neutral")))
        self.subtitle_label.setText(
            f"{tr(language, 'col_state')}: {state_label(language, self._state) or self._state}"
            f" · {invoice.get('supplier_invoice_no', '')}"
        )

        self.header_values["col_supplier"].setText(supplier_label or str(invoice.get("supplier_id", "")))
        self.header_values["col_invoice_no"].setText(str(invoice.get("supplier_invoice_no", "")))
        self.header_values["col_date"].setText(str(invoice.get("invoice_date", "")))
        self.header_values["col_amount_ht"].setText(format_amount(invoice.get("amount_ht_dzd"), currency="DZD"))
        self.header_values["col_amount_tva"].setText(format_amount(invoice.get("amount_tva_dzd"), currency="DZD"))
        self.header_values["col_amount_ttc"].setText(format_amount(invoice.get("amount_ttc_dzd"), currency="DZD"))

        warnings = _parse_validation_errors(invoice.get("validation_errors_json"))
        if warnings:
            self.warning_banner.set_text(" · ".join(warnings))
            self.warning_banner.setVisible(True)
        else:
            self.warning_banner.setVisible(False)

        self._render_review_blockers(data.get("policy"), language)

        field_rows = []
        for field in data["fields"].get("fields", []):
            override = field.get("human_override_value")
            # Column order is the Phase 27 contract: field, raw OCR, current
            # value, confidence, override provenance ("—" when untouched).
            field_rows.append(
                {
                    "field_name": str(field.get("field_name", "")),
                    "raw_text": str(field.get("raw_text", "")),
                    "current_value": str(override or field.get("normalized_value") or ""),
                    "confidence_text": format_confidence(
                        field.get("confidence"),
                        calibrated=bool(field.get("confidence_calibrated")),
                        lang=language,
                    ),
                    # Short marker only: the full sentence ("verified by a
                    # human, when, by whom") is prose and belongs in the
                    # detail line under the table where it can wrap.
                    "origin": tr(language, "review_origin_verified") if override else tr(language, "review_origin_auto"),
                    "provenance": tr(language, "review_edited_by",
                                      editor=str(field.get("edited_by_id") or "—"),
                                      at=str(field.get("edited_at") or "—"))
                    if override
                    else tr(language, "review_no_override"),
                }
            )
        if field_rows:
            self.fields_table.set_placeholder_title(tr(language, "review_no_fields"))
        else:
            # No extraction for this document (e.g. an invoice typed by hand):
            # say so in the resource's own words rather than "no data".
            self.fields_table.set_placeholder_title(tr(language, "review_no_fields"))
        self.fields_table.set_rows(field_rows)

        self.lines_table.set_rows(list(self._detail.get("lines", [])))

        preview = data.get("preview")
        if preview is None:
            # A label keeps its last pixmap until told otherwise; clear it or
            # the previous document stays on screen and misleads the reviewer.
            self.preview_label.clear()
            self.preview_label.setText(tr(language, "doc_preview_none"))
        elif preview.get("error"):
            self.preview_label.setText(tr(language, "doc_preview_failed", detail=str(preview["error"])))
        else:
            from .ui import render_document_preview

            pixmap = render_document_preview(preview["content"], str(preview["media_type"]))
            if pixmap is None:
                self.preview_label.setText(
                    tr(language, "doc_preview_unsupported", media=str(preview["media_type"]))
                )
            else:
                self.preview_label.setPixmap(
                    pixmap.scaled(
                        QSize(max(self.preview_label.width(), 320), max(self.preview_label.height(), 220)),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        self._set_busy(False)
        self._apply_action_state()

    # -- actions -------------------------------------------------------

    def _show_action_error(self, exc: object) -> None:
        self._set_busy(False)
        self._apply_action_state()
        _message_box().critical(
            self,
            tr(self._lang, "error_title"),
            tr(self._lang, "action_failed", detail=str(exc)),
        )

    def _require_selected_field(self) -> Optional[str]:
        row = self.fields_table.selected_row()
        if row is None:
            rows = self.fields_table.visible_rows()
            if not rows:
                _message_box().information(
                    self, tr(self._lang, "review_title"), tr(self._lang, "review_empty")
                )
                return None
            row = rows[0]
        name = str(row.get("field_name", ""))
        return name or None

    def _correct_selected_field(self) -> None:
        """Edit an extracted value, or transcribe a field the engine missed."""
        field_name = self._require_selected_field()
        if not field_name:
            return
        new_value = self.correction_edit.text().strip()
        if not new_value:
            _message_box().information(
                self, tr(self._lang, "review_title"), tr(self._lang, "review_edit_empty")
            )
            return
        document_id = self._document_id()
        if not document_id:
            _message_box().information(
                self, tr(self._lang, "review_title"), tr(self._lang, "doc_preview_none")
            )
            return
        self._set_busy(True)
        policy_row = self._selected_policy_field(field_name)
        if policy_row is not None and policy_row.get("value") in (None, ""):
            # Phase 59B: manual transcription. PATCH /fields can only modify a
            # row that exists, so a required field the OCR engine never read
            # used to answer 404 - the document could then never satisfy its
            # own review policy and the invoice could never be submitted, with
            # no screen explaining why. The documented entry point for this
            # case is a CORRECTED decision, which creates the row with honest
            # provenance ("[manual entry]", no measured confidence). No value
            # is ever invented on the operator's behalf.
            # ``expected_value`` is the emptiness the operator is looking at:
            # if extraction lands a value between the read and this click, the
            # service refuses instead of silently overwriting a machine
            # reading the reviewer never saw.
            self._decide_field(
                document_id,
                {
                    "field_name": field_name,
                    "decision": "CORRECTED",
                    "value": new_value,
                    "expected_value": "",
                },
            )
            return
        self.run_async(
            self._client.edit_extracted_field,
            document_id,
            field_name,
            new_value,
            on_success=lambda _result: self.refresh(),
            on_error=self._show_action_error,
        )

    def _decide_field(self, document_id: str, payload: Dict[str, Any]) -> None:
        """Post one review decision and reload; refusals stay on screen."""
        self.run_async(
            self._client.decide_document_field,
            document_id,
            payload,
            on_success=lambda _result: self.refresh(),
            on_error=self._decide_failed,
        )

    def _decide_failed(self, exc: object) -> None:
        self._set_busy(False)
        self._apply_action_state()
        _message_box().critical(
            self,
            tr(self._lang, "review_title"),
            tr(self._lang, "review_decide_failed", detail=str(exc)),
        )

    def _submit_approval(self) -> None:
        self._set_busy(True)
        self.run_async(
            self._client.submit_approval,
            self._invoice_id,
            on_success=lambda _result: self.refresh(),
            on_error=self._show_action_error,
        )

    def _decide(self, approve: bool) -> None:
        reason = self.reason_edit.text().strip()
        if not approve and not reason:
            _message_box().information(
                self, tr(self._lang, "review_title"), tr(self._lang, "decision_reason")
            )
            return
        self._set_busy(True)
        self.run_async(
            self._client.decide_approval,
            self._invoice_id,
            approve=approve,
            reason=reason,
            on_success=lambda _result: self.refresh(),
            on_error=self._show_action_error,
        )

    def _post_invoice(self) -> None:
        self._set_busy(True)
        self.run_async(
            self._client.post_invoice,
            self._invoice_id,
            on_success=lambda _result: self.refresh(),
            on_error=self._show_action_error,
        )


# ----------------------------------------------------------------------
# creation
# ----------------------------------------------------------------------


class CreateInvoiceDialog(QDialog, WorkerOwner):
    """Header-amount supplier invoice creation (single-line MVP, unchanged).

    Totals are validated by the service; this form only collects them. The
    supplier list comes from the tenant-scoped partners read, and a partner
    created elsewhere appears after reopening the dialog - the form never
    caches a stale tenant list.
    """

    def __init__(self, lang: str, client: ErpClient, parent: Any = None) -> None:
        super().__init__(parent)
        self._lang = lang
        self._client = client
        self._init_workers()
        self.setWindowTitle(tr(lang, "invoice_create_title"))
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(tokens.SPACING["xl"], tokens.SPACING["lg"], tokens.SPACING["xl"], tokens.SPACING["lg"])
        root.setSpacing(tokens.SPACING["md"])
        root.addWidget(label(tr(lang, "invoice_create_title"), object_name="DialogTitle"))

        card = Card("", object_name="CardFlat", body_margins=(16, 16, 16, 16))
        form = QFormLayout()
        form.setHorizontalSpacing(tokens.SPACING["lg"])
        form.setVerticalSpacing(tokens.SPACING["sm"])
        self.supplier_combo = _Combined()
        suppliers = [
            partner
            for partner in client.partners().get("partners", [])
            if partner.get("partner_type") in ("supplier", "both")
        ]
        for partner in suppliers:
            self.supplier_combo.addItem(
                f"{partner.get('name', '')} ({partner.get('nif') or '—'})", int(partner.get("id"))
            )
        form.addRow(label(tr(lang, "invoice_create_supplier"), object_name="FieldLabel"), self.supplier_combo)
        self.invoice_no = QLineEdit()
        form.addRow(label(tr(lang, "invoice_create_no"), object_name="FieldLabel"), self.invoice_no)
        self.invoice_date = QLineEdit()
        self.invoice_date.setPlaceholderText("AAAA-MM-JJ")
        form.addRow(label(tr(lang, "invoice_create_date"), object_name="FieldLabel"), self.invoice_date)
        self.payment_combo = _Combined()
        for method in ("BANK_TRANSFER", "CASH", "CHEQUE"):
            self.payment_combo.addItem(method, method)
        form.addRow(label(tr(lang, "invoice_create_payment"), object_name="FieldLabel"), self.payment_combo)
        self.amount_ht = QLineEdit()
        self.amount_ht.setPlaceholderText("0.00")
        form.addRow(label(tr(lang, "invoice_create_ht"), object_name="FieldLabel"), self.amount_ht)
        self.amount_tva = QLineEdit()
        self.amount_tva.setPlaceholderText("0.00")
        form.addRow(label(tr(lang, "invoice_create_tva"), object_name="FieldLabel"), self.amount_tva)
        self.amount_timbre = QLineEdit("0.00")
        form.addRow(label(tr(lang, "invoice_create_timbre"), object_name="FieldLabel"), self.amount_timbre)
        self.amount_ttc = QLineEdit()
        self.amount_ttc.setPlaceholderText("0.00")
        form.addRow(label(tr(lang, "invoice_create_ttc"), object_name="FieldLabel"), self.amount_ttc)
        card.add_layout(form)
        root.addWidget(card)

        self.error_label = label("", object_name="ErrorLabel", wrap=True)
        root.addWidget(self.error_label)

        actions = QHBoxLayout()
        actions.setSpacing(tokens.SPACING["sm"])
        actions.addStretch(1)
        cancel = Button(tr(lang, "button_cancel"), variant="ghost", on_click=self.reject)
        self.create_button = Button(
            tr(lang, "invoice_create_button"), variant="primary", on_click=self._submit
        )
        self.create_button.setDefault(True)
        actions.addWidget(cancel)
        actions.addWidget(self.create_button)
        root.addLayout(actions)
        if self.supplier_combo.count() == 0:
            self.error_label.setText(tr(lang, "review_empty"))

    def _submit(self) -> None:
        supplier_id = self.supplier_combo.currentData()
        if supplier_id is None:
            self.error_label.setText(tr(self._lang, "review_empty"))
            return
        amount_ht = self.amount_ht.text().strip()
        payload = {
            "supplier_id": int(supplier_id),
            "supplier_invoice_no": self.invoice_no.text().strip(),
            "invoice_date": self.invoice_date.text().strip(),
            "payment_method": str(self.payment_combo.currentData()),
            "amount_ht_dzd": amount_ht,
            "amount_tva_dzd": self.amount_tva.text().strip(),
            "amount_timbre_dzd": self.amount_timbre.text().strip() or "0.00",
            "amount_ttc_dzd": self.amount_ttc.text().strip(),
            # Single-line MVP: the header HT as one validated line, matching
            # the invoice-line contract the service already verifies.
            "lines": [
                {
                    "description": self.invoice_no.text().strip() or "Achat",
                    "qty": "1",
                    "unit_price_dzd": amount_ht,
                    "tax_rate": "19.0",
                    "line_ht_dzd": amount_ht,
                }
            ],
        }
        self.create_button.setEnabled(False)
        self.run_async(
            self._client.create_invoice,
            payload,
            on_success=lambda _result: self.accept(),
            on_error=self._on_failed,
        )

    def _on_failed(self, exc: object) -> None:
        self.create_button.setEnabled(True)
        self.error_label.setText(tr(self._lang, "invoice_create_failed", detail=str(exc)))


def _Combined():
    from PySide6.QtWidgets import QComboBox

    return QComboBox()


__all__ = [
    "ACTIONS_BY_STATE",
    "CreateInvoiceDialog",
    "InvoiceReviewDialog",
    "InvoicesView",
    "state_pill",
]
