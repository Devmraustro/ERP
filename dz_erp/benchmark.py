# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 6 — Synthetic Algerian Invoice Benchmark Generator & Empirical OCR Evaluation Runner."""

import io
import json
import statistics
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .decimal_utils import format_dzd, round_dzd, to_decimal
from .intake_ocr import extract_document_ocr, levenshtein_distance
from .localization import calculate_droit_de_timbre

DEJAVU_TTF = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# The benchmark must render **identically** on every machine, because the OCR
# accuracy assertions below are measured on the rendered output.  Depending on
# a system font at a hardcoded path makes the result a function of the host
# image: a different DejaVu build (or its absence, silently falling back to
# Helvetica) shifts glyph shapes and therefore OCR scores, so the same commit
# passes on one runner and fails on another.
#
# The font is therefore vendored under dz_erp/assets/fonts/ and takes
# precedence.  The system path is kept only as a fallback for environments that
# predate the vendored copy.
BUNDLED_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
BUNDLED_DEJAVU_TTF = BUNDLED_FONT_DIR / "DejaVuSans.ttf"


def _resolve_font_path() -> Optional[Path]:
    """Resolve the font used to render benchmark documents, deterministically."""
    if BUNDLED_DEJAVU_TTF.is_file():
        return BUNDLED_DEJAVU_TTF
    system = Path(DEJAVU_TTF)
    if system.is_file():
        return system
    return None


_FONT_REGISTERED = False


def _ensure_font() -> None:
    global _FONT_REGISTERED
    if not _FONT_REGISTERED:
        resolved = _resolve_font_path()
        if resolved is not None:
            pdfmetrics.registerFont(TTFont("DejaVu", str(resolved)))
            _FONT_REGISTERED = True


def render_synthetic_invoice_pdf(spec: Dict[str, Any]) -> bytes:
    """Render a synthetic Algerian invoice PDF with French and optional Arabic headers."""
    _ensure_font()
    buf = io.BytesIO()
    # `invariant=True` fixes the document ID and timestamps, so regenerating the
    # corpus produces byte-identical files. Without it every run rewrote the
    # tracked PDFs with a new /CreationDate, which turned a reproducible
    # benchmark into a source of spurious diffs in committed evidence.
    c = canvas.Canvas(buf, invariant=True)
    font_name = "DejaVu" if _FONT_REGISTERED else "Helvetica"
    c.setFont(font_name, 11)

    y = 780
    header_lines = [
        f"FOURNISSEUR: {spec['supplier_name']}",
        f"FACTURE N: {spec['invoice_number']}   DATE: {spec['invoice_date']}",
        f"NIF: {spec['supplier_nif']}   RC: {spec['supplier_rc']}   AI: {spec['supplier_ai']}",
        f"PAIEMENT: {spec['payment_method']}   DEVISE: DZD",
    ]
    if spec.get("arabic_header"):
        header_lines.insert(0, spec["arabic_header"])
    if spec.get("po_number"):
        header_lines.append(f"PO: {spec['po_number']}   GRN: {spec.get('grn_number', 'GRN-2026-0001')}")

    for hl in header_lines:
        c.drawString(45, y, hl)
        y -= 20

    y -= 10
    for item in spec["lines"]:
        line_str = (
            f"ITEM {item['sku']} {item['description']} "
            f"QTY: {item['qty']} PU: {item['unit_price_dzd']} "
            f"TVA: {item['tax_rate']}% HT: {item['line_ht_dzd']}"
        )
        c.drawString(45, y, line_str)
        y -= 18

    y -= 10
    c.drawString(45, y, f"TOTAL HT: {spec['amount_ht_dzd']} DZD")
    y -= 18
    c.drawString(45, y, f"TOTAL TVA: {spec['amount_tva_dzd']} DZD")
    y -= 18
    if to_decimal(spec["amount_timbre_dzd"]) > Decimal("0.00"):
        c.drawString(45, y, f"TIMBRE: {spec['amount_timbre_dzd']} DZD")
        y -= 18
    c.drawString(45, y, f"TOTAL TTC: {spec['amount_ttc_dzd']} DZD")
    c.save()
    return buf.getvalue()


def render_synthetic_invoice_image(
    spec: Dict[str, Any],
    skew_deg: float = 0.0,
    blur_radius: float = 0.0,
    add_wet_stamp: bool = False,
    thermal_style: bool = False,
) -> bytes:
    """Render a synthetic Algerian invoice as a PNG scan (clean, skewed/stamped, or thermal)."""
    width = 780 if thermal_style else 960
    height = 520
    bg_color = (248, 246, 240) if thermal_style else (255, 255, 255)
    img = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)
    resolved_font = _resolve_font_path()
    font = (
        ImageFont.truetype(str(resolved_font), 17 if not thermal_style else 14)
        if resolved_font is not None
        else ImageFont.load_default()
    )

    y = 25
    lines_text = [
        f"FOURNISSEUR: {spec['supplier_name']}",
        f"FACTURE N: {spec['invoice_number']} DATE: {spec['invoice_date']}",
        f"NIF: {spec['supplier_nif']}",
        f"RC: {spec['supplier_rc']} AI: {spec['supplier_ai']}",
        f"PAIEMENT: {spec['payment_method']} DZD",
    ]
    for item in spec["lines"]:
        lines_text.append(
            f"ITEM {item['sku']} {item['description']} QTY: {item['qty']} PU: {item['unit_price_dzd']} TVA: {item['tax_rate']}% HT: {item['line_ht_dzd']}"
        )
    lines_text.append(f"TOTAL HT: {spec['amount_ht_dzd']}")
    lines_text.append(f"TOTAL TVA: {spec['amount_tva_dzd']}")
    if to_decimal(spec["amount_timbre_dzd"]) > Decimal("0.00"):
        lines_text.append(f"TIMBRE: {spec['amount_timbre_dzd']}")
    lines_text.append(f"TOTAL TTC: {spec['amount_ttc_dzd']}")

    for lt in lines_text:
        draw.text((30, y), lt, fill=(15, 15, 15), font=font)
        y += 34

    if add_wet_stamp:
        # Simulate blue wet-ink company stamp ("CACHET HUMIDE") in bottom-right margin
        draw.ellipse((width - 240, height - 180, width - 50, height - 40), outline=(30, 70, 190), width=3)
        draw.text((width - 215, height - 120), "CACHET PAYE", fill=(30, 70, 190), font=font)

    if abs(skew_deg) > 0.01:
        img = img.rotate(skew_deg, expand=False, fillcolor=bg_color)
    if blur_radius > 0.01:
        img = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def build_synthetic_algerian_benchmark_specs() -> List[Dict[str, Any]]:
    """Build 18 diverse, deterministic Algerian invoice benchmark specifications across 6 strata."""
    strata = [
        ("S1_FRENCH_DIGITAL_PDF", "application/pdf", False, 0.0, 0.0, False, False, "BANK_TRANSFER"),
        ("S2_BILINGUAL_FR_AR_DIGITAL_PDF", "application/pdf", True, 0.0, 0.0, False, False, "BANK_TRANSFER"),
        ("S3_CASH_TIMBRE_DIGITAL_PDF", "application/pdf", False, 0.0, 0.0, False, False, "CASH"),
        ("S4_CLEAN_RASTER_SCAN_PNG", "image/png", False, 0.0, 0.0, False, False, "BANK_TRANSFER"),
        ("S5_STAMPED_SKEWED_SCAN_PNG", "image/png", False, 0.8, 0.3, True, False, "BANK_TRANSFER"),
        ("S6_THERMAL_CASH_RECEIPT_PNG", "image/png", False, 0.0, 0.0, False, True, "CASH"),
    ]
    specs: List[Dict[str, Any]] = []
    doc_idx = 1
    for stratum_name, mime, has_ar, skew, blur, stamp, thermal, pay_method in strata:
        for i in range(1, 4):
            qty = Decimal(str(i * 5))
            pu = Decimal("1200.00") if i % 2 == 1 else Decimal("2500.00")
            rate = Decimal("19.0") if i < 3 else Decimal("9.0")
            ht = round_dzd(qty * pu)
            tva = round_dzd(ht * (rate / Decimal("100.0")))
            timbre = calculate_droit_de_timbre(ht + tva, payment_method=pay_method)
            ttc = round_dzd(ht + tva + timbre)

            spec: Dict[str, Any] = {
                "doc_id": f"BENCH-{doc_idx:03d}",
                "stratum": stratum_name,
                "mime_type": mime,
                "supplier_name": f"SARL ATLAS EQUIPEMENT {doc_idx}",
                "arabic_header": "شركة أطلس للتجهيز الصناعي - فاتورة" if has_ar else None,
                "invoice_number": f"FAC-2026-{doc_idx:04d}",
                "invoice_date": f"2026-10-{min(28, doc_idx):02d}",
                "supplier_nif": f"0012160991234{doc_idx:02d}",
                "supplier_rc": f"16/00-09912{doc_idx:02d}B12",
                "supplier_ai": f"160199123{doc_idx:02d}",
                "payment_method": pay_method,
                "amount_ht_dzd": format_dzd(ht),
                "amount_tva_dzd": format_dzd(tva),
                "amount_timbre_dzd": format_dzd(timbre),
                "amount_ttc_dzd": format_dzd(ttc),
                "lines": [
                    {
                        "sku": f"PRD-{doc_idx:03d}",
                        "description": f"COMPOSANT INDUSTRIEL {doc_idx}",
                        "qty": f"{qty:.2f}",
                        "unit_price_dzd": f"{pu:.2f}",
                        "tax_rate": f"{rate:.1f}",
                        "line_ht_dzd": format_dzd(ht),
                    }
                ],
                "skew_deg": skew,
                "blur_radius": blur,
                "add_wet_stamp": stamp,
                "thermal_style": thermal,
            }
            specs.append(spec)
            doc_idx += 1
    return specs


def _parse_money(text: Any) -> Optional[Decimal]:
    """Parse an extracted monetary string into an exact Decimal, or None."""
    if text is None:
        return None
    try:
        return to_decimal(str(text).strip())
    except (ValueError, TypeError):
        return None


# Language groups for the per-language roll-up. The synthetic dataset contains
# French and bilingual FR/AR documents; a *scanned* Arabic-only stratum is
# deliberately absent because the bundled recognition head cannot read it
# (protocol §7, risk O3). The roll-up must show that absence, not hide it.
LANGUAGE_GROUP_BY_STRATUM: Dict[str, str] = {
    "S1_FRENCH_DIGITAL_PDF": "french_digital",
    "S2_BILINGUAL_FR_AR_DIGITAL_PDF": "bilingual_fr_ar",
    "S3_CASH_TIMBRE_DIGITAL_PDF": "french_digital",
    "S4_CLEAN_RASTER_SCAN_PNG": "french_raster",
    "S5_STAMPED_SKEWED_SCAN_PNG": "french_raster",
    "S6_THERMAL_CASH_RECEIPT_PNG": "french_raster",
}


def run_empirical_ocr_benchmark(output_dir: Path) -> Dict[str, Any]:
    """Generate benchmark dataset files and execute real extraction across all strata."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    specs = build_synthetic_algerian_benchmark_specs()

    tracked_fields = [
        "invoice_number",
        "supplier_nif",
        "supplier_rc",
        "supplier_ai",
        "invoice_date",
        "amount_ht_dzd",
        "amount_tva_dzd",
        "amount_ttc_dzd",
    ]
    field_stats: Dict[str, Dict[str, int]] = {
        f: {"total": 0, "extracted": 0, "exact_match": 0, "char_edits": 0, "char_total": 0}
        for f in tracked_fields
    }
    stratum_stats: Dict[str, Dict[str, Any]] = {}
    latencies_ms: List[float] = []
    conf_outcomes: List[Tuple[float, int]] = []
    calibration_excluded = 0
    line_assoc_matches = 0
    line_assoc_total = len(specs)
    # Keep the per-document extraction results so the arithmetic-consistency
    # axis below can be computed from the *extracted* values without re-running
    # OCR (the runner is deliberately deterministic but not free).
    ocr_by_doc: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

    for spec in specs:
        if spec["mime_type"] == "application/pdf":
            raw_bytes = render_synthetic_invoice_pdf(spec)
            ext = "pdf"
        else:
            raw_bytes = render_synthetic_invoice_image(
                spec,
                skew_deg=spec["skew_deg"],
                blur_radius=spec["blur_radius"],
                add_wet_stamp=spec["add_wet_stamp"],
                thermal_style=spec["thermal_style"],
            )
            ext = "png"

        doc_path = output_dir / f"{spec['doc_id']}.{ext}"
        doc_path.write_bytes(raw_bytes)

        ocr_res = extract_document_ocr(raw_bytes, spec["mime_type"])
        latencies_ms.append(ocr_res["latency_ms"])
        ocr_by_doc.append((spec, ocr_res))

        s_name = spec["stratum"]
        if s_name not in stratum_stats:
            stratum_stats[s_name] = {"docs": 0, "field_matches": 0, "field_total": 0, "latencies_ms": []}
        stratum_stats[s_name]["docs"] += 1
        stratum_stats[s_name]["latencies_ms"].append(ocr_res["latency_ms"])

        for fname in tracked_fields:
            gt_val = str(spec[fname]).strip()
            field_stats[fname]["total"] += 1
            field_stats[fname]["char_total"] += len(gt_val)
            stratum_stats[s_name]["field_total"] += 1

            extracted_obj = ocr_res["fields"].get(fname)
            if extracted_obj:
                field_stats[fname]["extracted"] += 1
                pred_val = str(extracted_obj["normalized_value"]).strip()
                is_match = int(pred_val == gt_val)
                field_stats[fname]["exact_match"] += is_match
                stratum_stats[s_name]["field_matches"] += is_match
                field_stats[fname]["char_edits"] += levenshtein_distance(gt_val, pred_val)
                # Calibration is only meaningful where a confidence was actually
                # measured. Native PDF extraction reports none, and imputing a
                # value (a constant, or 0.0 for a miss) would manufacture the very
                # number the metric is supposed to test. Those fields are counted
                # as excluded instead.
                conf = extracted_obj.get("confidence")
                if conf is None:
                    calibration_excluded += 1
                else:
                    conf_outcomes.append((float(conf), is_match))
            else:
                field_stats[fname]["char_edits"] += len(gt_val)
                # An absent field has no confidence either; scoring it at 0.0
                # would put it in the lowest bin and improve the apparent
                # calibration of the fields that were extracted.
                calibration_excluded += 1

        if len(ocr_res["lines"]) == len(spec["lines"]):
            pred_line = ocr_res["lines"][0]
            gt_line = spec["lines"][0]
            if pred_line["sku"] == gt_line["sku"] and pred_line["line_ht_dzd"] == gt_line["line_ht_dzd"]:
                line_assoc_matches += 1

    # Compute Expected Calibration Error (ECE) across 5 bins, over measured
    # confidences only. `ece` is None - not 0.0 - when nothing was measured,
    # because "no data" and "perfectly calibrated" are opposite conclusions.
    ece = None
    if conf_outcomes:
        ece = 0.0
        n_total = len(conf_outcomes)
        for b_idx in range(5):
            low, high = b_idx * 0.2, (b_idx + 1) * 0.2
            bucket = [x for x in conf_outcomes if (low <= x[0] < high) or (b_idx == 4 and x[0] == 1.0)]
            if bucket:
                avg_conf = sum(x[0] for x in bucket) / len(bucket)
                avg_acc = sum(x[1] for x in bucket) / len(bucket)
                ece += (len(bucket) / n_total) * abs(avg_acc - avg_conf)

    # Arithmetic / tax consistency of the *extracted* values. This is a
    # separate axis from character accuracy: a document whose digits are all
    # slightly wrong can still balance, and a single wrong digit can break
    # balance, so both must be reported independently. A header counts as
    # consistent only when HT + TVA (+ a recognised timbre) reproduces the
    # extracted TTC under 2-decimal DZD rounding; a document is counted in the
    # evaluated set only when the needed fields were actually extracted.
    headers_evaluated = headers_consistent = 0
    line_sums_evaluated = line_sums_consistent = 0
    for spec, ocr_res in ocr_by_doc:
        fields = ocr_res["fields"]

        def _value(name: str) -> Optional[Decimal]:
            obj = fields.get(name)
            return _parse_money(obj["normalized_value"]) if obj else None

        ht_dec, tva_dec, ttc_dec = _value("amount_ht_dzd"), _value("amount_tva_dzd"), _value("amount_ttc_dzd")
        if None not in (ht_dec, tva_dec, ttc_dec):
            headers_evaluated += 1
            timbre_dec = _value("amount_timbre_dzd") or Decimal("0.00")
            if round_dzd(ht_dec + tva_dec + timbre_dec) == round_dzd(ttc_dec):
                headers_consistent += 1

        extracted_lines = ocr_res.get("lines") or []
        if (
            len(extracted_lines) == len(spec["lines"])
            and ht_dec is not None
            and tva_dec is not None
            and extracted_lines
        ):
            line_ht = [_parse_money(line.get("line_ht_dzd")) for line in extracted_lines]
            if all(v is not None for v in line_ht):
                line_sums_evaluated += 1
                if round_dzd(sum(line_ht)) == round_dzd(ht_dec):
                    line_sums_consistent += 1

    # Failure categories. "missing" means the field was not extracted at all;
    # "mismatch" means it was extracted but does not equal the ground truth.
    # Both are reported separately because they demand different remedies
    # (review routing vs. human correction of a plausible-looking value).
    failures_missing = sum(s["total"] - s["extracted"] for s in field_stats.values())
    failures_mismatch = sum(s["extracted"] - s["exact_match"] for s in field_stats.values())

    summary_by_field: Dict[str, Any] = {}
    for fname, st in field_stats.items():
        tot = st["total"]
        ext_c = st["extracted"]
        em = st["exact_match"]
        precision = (em / ext_c) if ext_c > 0 else 0.0
        recall = (em / tot) if tot > 0 else 0.0
        cer = (st["char_edits"] / st["char_total"]) if st["char_total"] > 0 else 0.0
        summary_by_field[fname] = {
            "total": tot,
            "extracted": ext_c,
            "exact_match": em,
            "exact_match_rate": round(em / tot, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "cer": round(cer, 4),
            "failures": {
                "missing": tot - ext_c,
                "mismatch": ext_c - em,
            },
        }

    summary_by_stratum: Dict[str, Any] = {}
    for s_name, st in stratum_stats.items():
        summary_by_stratum[s_name] = {
            "docs": st["docs"],
            "field_accuracy": round(st["field_matches"] / st["field_total"], 4),
            "mean_latency_ms": round(statistics.mean(st["latencies_ms"]), 2),
        }

    # Per-language roll-up over the strata above. Groups with no documents are
    # omitted here and explained by `language_coverage_note`, so an absent
    # group cannot be mistaken for a measured 0%.
    language_groups: Dict[str, Dict[str, Any]] = {}
    for s_name, st in stratum_stats.items():
        group = LANGUAGE_GROUP_BY_STRATUM.get(s_name)
        if group is None:
            continue
        agg = language_groups.setdefault(
            group, {"docs": 0, "field_matches": 0, "field_total": 0, "latencies_ms": []}
        )
        agg["docs"] += st["docs"]
        agg["field_matches"] += st["field_matches"]
        agg["field_total"] += st["field_total"]
        agg["latencies_ms"].extend(st["latencies_ms"])
    summary_by_language: Dict[str, Any] = {}
    for group, agg in language_groups.items():
        summary_by_language[group] = {
            "docs": agg["docs"],
            "field_accuracy": round(agg["field_matches"] / agg["field_total"], 4),
            "mean_latency_ms": round(statistics.mean(agg["latencies_ms"]), 2),
        }

    report = {
        "dataset_size_documents": len(specs),
        "dataset_provenance": "Synthetic authorized Algerian commercial invoices generated via ReportLab/Pillow with ground-truth manifest",
        "overall_field_exact_match_rate": round(
            sum(s["exact_match"] for s in field_stats.values()) / sum(s["total"] for s in field_stats.values()),
            4,
        ),
        "overall_cer": round(
            sum(s["char_edits"] for s in field_stats.values()) / sum(s["char_total"] for s in field_stats.values()),
            4,
        ),
        "table_line_association_accuracy": round(line_assoc_matches / line_assoc_total, 4),
        # Consistency of the extracted totals under the SCF/DZD rules. These
        # are the same invariants the posting path enforces; measuring them
        # here shows how often an *extraction error alone* would break balance.
        "arithmetic_consistency": {
            "documents": len(specs),
            "headers_evaluated": headers_evaluated,
            "headers_consistent": headers_consistent,
            "line_sums_evaluated": line_sums_evaluated,
            "line_sums_consistent": line_sums_consistent,
            "note": (
                "Header consistency requires HT + TVA (+ a recognised droit de "
                "timbre) to reproduce TTC under 2-decimal DZD rounding; "
                "documents missing a needed field are excluded from the "
                "evaluated count, not scored as failures."
            ),
        },
        "failure_categories": {
            "missing": failures_missing,
            "mismatch": failures_mismatch,
            "note": (
                "missing = field not extracted at all; mismatch = extracted "
                "value differs from the ground truth. Per-field counts are in "
                "by_field[<field>]['failures']."
            ),
        },
        # None when no extracted field carried a measured confidence, and
        # reported with its own observation count so it cannot be read as a
        # statement about the whole dataset.
        "expected_calibration_error_ece": None if ece is None else round(ece, 4),
        "calibration_observations": len(conf_outcomes),
        "calibration_excluded_no_confidence": calibration_excluded,
        "calibration_note": (
            "ECE is computed only over fields whose extraction path measured a "
            "recognition confidence. Native PDF extraction measures none, and "
            "fields with no confidence are excluded rather than imputed."
        ),
        "latency_mean_ms": round(statistics.mean(latencies_ms), 2),
        "latency_p50_ms": round(statistics.median(latencies_ms), 2),
        "latency_max_ms": round(max(latencies_ms), 2),
        "by_field": summary_by_field,
        "by_stratum": summary_by_stratum,
        "by_language": summary_by_language,
        "language_coverage_note": (
            "Groups measured on this synthetic set: french_digital, "
            "bilingual_fr_ar and french_raster. A scanned Arabic-only stratum "
            "is intentionally absent: the bundled recognition head cannot read "
            "it (docs/OCR_EVALUATION_PROTOCOL.md §7, risk O3); such documents "
            "are routed to manual transcription rather than mis-read."
        ),
    }
    (output_dir / "ground_truth_and_results.json").write_text(
        json.dumps({"specs": specs, "report": report}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return report


def _cli(argv: Optional[List[str]] = None) -> int:
    """Repeatable entry point: `python -m dz_erp.benchmark <output_dir>`."""
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Run the 18-document synthetic Algerian invoice OCR benchmark "
            "(field accuracy, totals/tax consistency, confidence calibration, "
            "failure categories, language roll-up)."
        )
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Directory that receives the rendered documents and ground_truth_and_results.json.",
    )
    args = parser.parse_args(argv)
    report = run_empirical_ocr_benchmark(args.output_dir)
    print(
        f"overall field exact-match: {report['overall_field_exact_match_rate']}\n"
        f"totals consistent (headers): "
        f"{report['arithmetic_consistency']['headers_consistent']}/{report['arithmetic_consistency']['headers_evaluated']}\n"
        f"ECE: {report['expected_calibration_error_ece']}\n"
        f"report: {(Path(args.output_dir) / 'ground_truth_and_results.json')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
