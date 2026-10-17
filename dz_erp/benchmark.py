# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 6 — Synthetic Algerian Invoice Benchmark Generator & Empirical OCR Evaluation Runner."""

import io
import json
import statistics
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .decimal_utils import format_dzd, round_dzd, to_decimal
from .intake_ocr import extract_document_ocr, levenshtein_distance
from .localization import calculate_droit_de_timbre

DEJAVU_TTF = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_REGISTERED = False


def _ensure_font() -> None:
    global _FONT_REGISTERED
    if not _FONT_REGISTERED and Path(DEJAVU_TTF).exists():
        pdfmetrics.registerFont(TTFont("DejaVu", DEJAVU_TTF))
        _FONT_REGISTERED = True


def render_synthetic_invoice_pdf(spec: Dict[str, Any]) -> bytes:
    """Render a synthetic Algerian invoice PDF with French and optional Arabic headers."""
    _ensure_font()
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
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
    font = ImageFont.truetype(DEJAVU_TTF, 17 if not thermal_style else 14) if Path(DEJAVU_TTF).exists() else ImageFont.load_default()

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
    line_assoc_matches = 0
    line_assoc_total = len(specs)

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
                conf = float(extracted_obj["confidence"])
                is_match = int(pred_val == gt_val)
                field_stats[fname]["exact_match"] += is_match
                stratum_stats[s_name]["field_matches"] += is_match
                field_stats[fname]["char_edits"] += levenshtein_distance(gt_val, pred_val)
                conf_outcomes.append((conf, is_match))
            else:
                field_stats[fname]["char_edits"] += len(gt_val)
                conf_outcomes.append((0.0, 0))

        if len(ocr_res["lines"]) == len(spec["lines"]):
            pred_line = ocr_res["lines"][0]
            gt_line = spec["lines"][0]
            if pred_line["sku"] == gt_line["sku"] and pred_line["line_ht_dzd"] == gt_line["line_ht_dzd"]:
                line_assoc_matches += 1

    # Compute Expected Calibration Error (ECE) across 5 bins
    ece = 0.0
    if conf_outcomes:
        n_total = len(conf_outcomes)
        for b_idx in range(5):
            low, high = b_idx * 0.2, (b_idx + 1) * 0.2
            bucket = [x for x in conf_outcomes if (low <= x[0] < high) or (b_idx == 4 and x[0] == 1.0)]
            if bucket:
                avg_conf = sum(x[0] for x in bucket) / len(bucket)
                avg_acc = sum(x[1] for x in bucket) / len(bucket)
                ece += (len(bucket) / n_total) * abs(avg_acc - avg_conf)

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
        }

    summary_by_stratum: Dict[str, Any] = {}
    for s_name, st in stratum_stats.items():
        summary_by_stratum[s_name] = {
            "docs": st["docs"],
            "field_accuracy": round(st["field_matches"] / st["field_total"], 4),
            "mean_latency_ms": round(statistics.mean(st["latencies_ms"]), 2),
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
        "expected_calibration_error_ece": round(ece, 4),
        "latency_mean_ms": round(statistics.mean(latencies_ms), 2),
        "latency_p50_ms": round(statistics.median(latencies_ms), 2),
        "latency_max_ms": round(max(latencies_ms), 2),
        "by_field": summary_by_field,
        "by_stratum": summary_by_stratum,
    }
    (output_dir / "ground_truth_and_results.json").write_text(
        json.dumps({"specs": specs, "report": report}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return report
