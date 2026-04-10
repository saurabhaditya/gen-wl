"""
Flask web app for the payroll workflow generator.
"""

import csv
import io
import json
import os
import tempfile
from typing import Any

from flask import Flask, jsonify, render_template, request, Response, stream_with_context, send_from_directory

from components import REGISTRY, list_components
from column_classifier import (
    classify_columns, read_csv_headers_and_samples,
    CANONICAL_FIELDS, build_field_map
)
from generator import generate_workflow, generate_workflow_stream
from journal_entry_generator import (
    PayrollSummary, generate_all_entries, to_csv, to_json, to_xero_csv,
    DEFAULT_GL_ACCOUNTS
)
from validator import validate_workflow

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload limit

ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Workflow generation
# ---------------------------------------------------------------------------

@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json(force=True)
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    try:
        workflow = generate_workflow(prompt)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    result = validate_workflow(workflow)
    return jsonify({
        "workflow": workflow,
        "validation": {
            "valid": result.valid,
            "errors": result.errors,
            "warnings": result.warnings,
        },
    })


@app.route("/api/generate/stream", methods=["POST"])
def api_generate_stream():
    data = request.get_json(force=True)
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    def event_stream():
        workflow = None
        for chunk in generate_workflow_stream(prompt):
            if isinstance(chunk, str):
                payload = json.dumps({"type": "chunk", "text": chunk})
                yield f"data: {payload}\n\n"
            elif isinstance(chunk, dict):
                workflow = chunk

        if workflow:
            result = validate_workflow(workflow)
            payload = json.dumps({
                "type": "done",
                "workflow": workflow,
                "validation": {
                    "valid": result.valid,
                    "errors": result.errors,
                    "warnings": result.warnings,
                },
            })
            yield f"data: {payload}\n\n"

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/validate", methods=["POST"])
def api_validate():
    data = request.get_json(force=True)
    workflow = data.get("workflow")
    if not workflow:
        return jsonify({"error": "workflow is required"}), 400

    result = validate_workflow(workflow)
    return jsonify({
        "valid": result.valid,
        "errors": result.errors,
        "warnings": result.warnings,
    })


# ---------------------------------------------------------------------------
# File upload & column classification
# ---------------------------------------------------------------------------

@app.route("/api/classify-columns", methods=["POST"])
def api_classify_columns():
    """
    Upload a CSV/Excel file and classify its columns into canonical payroll fields.
    Returns column mappings with confidence scores.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename or not allowed_file(file.filename):
        return jsonify({"error": "Invalid file type. Supported: CSV, XLSX"}), 400

    provider_hint = request.form.get("provider_hint", "")
    review_threshold = float(request.form.get("review_threshold", 0.75))

    # Save to temp file
    suffix = "." + file.filename.rsplit(".", 1)[1].lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        if suffix == ".csv":
            headers, sample_rows = read_csv_headers_and_samples(tmp_path)
        else:
            from column_classifier import read_excel_headers_and_samples
            headers, sample_rows = read_excel_headers_and_samples(tmp_path)

        result = classify_columns(
            headers,
            sample_rows=sample_rows,
            review_threshold=review_threshold,
        )
        return jsonify(result.to_dict())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        os.unlink(tmp_path)


@app.route("/api/classify-columns/demo", methods=["GET"])
def api_classify_columns_demo():
    """Return a pre-built classification demo using ADP headers (no file upload needed)."""
    adp_headers = [
        "Co Code", "Batch #", "File #", "Last Name", "First Name",
        "Reg Hours", "O/T Hours", "Reg Earnings", "O/T Earnings",
        "Fed W/H", "SS EE", "Med EE", "SS ER", "Med ER",
        "St W/H", "SDI", "SUI ER", "401K EE", "401K ER",
        "Hlth EE", "Hlth ER", "Dental EE", "Garnish 1",
        "Net Pay", "YTD Gross", "Dept", "Cost Ctr",
    ]
    sample_rows = [
        ["ABC", "001", "10042", "Hernandez", "Maria",
         "80.00", "4.50", "5200.00", "438.75",
         "902.20", "349.60", "81.76", "349.60", "81.76",
         "282.00", "56.39", "107.14", "338.33", "169.16",
         "150.00", "300.00", "25.00", "0.00",
         "3096.13", "16916.25", "ENG", "CC-100"],
    ]
    try:
        result = classify_columns(adp_headers, sample_rows=sample_rows)
        return jsonify(result.to_dict())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Journal entry generation
# ---------------------------------------------------------------------------

@app.route("/api/journal-entries", methods=["POST"])
def api_journal_entries():
    """
    Generate journal entries from a payroll summary.
    Accepts JSON body with payroll totals.
    """
    data = request.get_json(force=True)

    try:
        summary = PayrollSummary(
            pay_date=data.get("pay_date", "2026-03-31"),
            period_start=data.get("period_start", "2026-03-16"),
            period_end=data.get("period_end", "2026-03-31"),
            reference=data.get("reference", "PR-001"),
            gross_regular=float(data.get("gross_regular", 0)),
            gross_overtime=float(data.get("gross_overtime", 0)),
            gross_bonus=float(data.get("gross_bonus", 0)),
            gross_pto=float(data.get("gross_pto", 0)),
            tax_federal_it=float(data.get("tax_federal_it", 0)),
            tax_state_it=float(data.get("tax_state_it", 0)),
            tax_local_it=float(data.get("tax_local_it", 0)),
            tax_ss_ee=float(data.get("tax_ss_ee", 0)),
            tax_ss_er=float(data.get("tax_ss_er", 0)),
            tax_medicare_ee=float(data.get("tax_medicare_ee", 0)),
            tax_medicare_er=float(data.get("tax_medicare_er", 0)),
            tax_futa=float(data.get("tax_futa", 0)),
            tax_suta_er=float(data.get("tax_suta_er", 0)),
            tax_workers_comp=float(data.get("tax_workers_comp", 0)),
            deduct_401k_ee=float(data.get("deduct_401k_ee", 0)),
            contrib_401k_er=float(data.get("contrib_401k_er", 0)),
            deduct_health_ee=float(data.get("deduct_health_ee", 0)),
            contrib_health_er=float(data.get("contrib_health_er", 0)),
            deduct_hsa_ee=float(data.get("deduct_hsa_ee", 0)),
            deduct_garnishment=float(data.get("deduct_garnishment", 0)),
            net_pay=float(data.get("net_pay", 0)),
            by_department=data.get("by_department", {}),
        )

        entries_by_type = generate_all_entries(
            summary,
            include_accrual=data.get("include_accrual", False),
            accrual_date=data.get("accrual_date"),
            accrual_days=int(data.get("accrual_days", 0)),
            work_days_in_period=int(data.get("work_days_in_period", 10)),
            deposit_date=data.get("deposit_date"),
            remit_date=data.get("remit_date"),
        )

        all_entries = [e for group in entries_by_type.values() for e in group]

        return jsonify({
            "entries": [e.to_dict() for e in all_entries],
            "by_type": {k: [e.to_dict() for e in v] for k, v in entries_by_type.items()},
            "balanced": all(e.is_balanced() for e in all_entries),
            "total_debits": sum(e.total_debits() for e in all_entries),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/journal-entries/demo", methods=["GET"])
def api_journal_entries_demo():
    """Return demo journal entries for the UI demo."""
    summary = PayrollSummary(
        pay_date="2026-03-31",
        period_start="2026-03-16",
        period_end="2026-03-31",
        reference="PR-2026-03B",
        gross_regular=120000.00,
        gross_overtime=8500.00,
        gross_bonus=5000.00,
        tax_federal_it=28000.00,
        tax_state_it=9500.00,
        tax_ss_ee=8091.00,
        tax_ss_er=8091.00,
        tax_medicare_ee=1892.25,
        tax_medicare_er=1892.25,
        tax_futa=252.00,
        tax_suta_er=756.00,
        deduct_401k_ee=6765.00,
        contrib_401k_er=3382.50,
        deduct_health_ee=3200.00,
        contrib_health_er=6400.00,
        net_pay=76101.75,
        by_department={
            "Engineering": {"gross_total": 85000.0, "net_pay": 54000.0},
            "Finance": {"gross_total": 30000.0, "net_pay": 19000.0},
            "Operations": {"gross_total": 18500.0, "net_pay": 12000.0},
        },
    )
    entries_by_type = generate_all_entries(
        summary,
        include_accrual=True,
        accrual_date="2026-03-31",
        accrual_days=3,
        deposit_date="2026-04-15",
        remit_date="2026-04-05",
    )
    all_entries = [e for group in entries_by_type.values() for e in group]
    return jsonify({
        "entries": [e.to_dict() for e in all_entries],
        "by_type": {k: [e.to_dict() for e in v] for k, v in entries_by_type.items()},
        "balanced": all(e.is_balanced() for e in all_entries),
    })


@app.route("/api/journal-entries/download")
def download_journal_entries():
    """Download journal entries as CSV."""
    fmt = request.args.get("format", "generic")

    summary = PayrollSummary(
        pay_date="2026-03-31",
        period_start="2026-03-16",
        period_end="2026-03-31",
        reference="PR-2026-03B",
        gross_regular=120000.00,
        gross_overtime=8500.00,
        gross_bonus=5000.00,
        tax_federal_it=28000.00,
        tax_state_it=9500.00,
        tax_ss_ee=8091.00,
        tax_ss_er=8091.00,
        tax_medicare_ee=1892.25,
        tax_medicare_er=1892.25,
        tax_futa=252.00,
        tax_suta_er=756.00,
        deduct_401k_ee=6765.00,
        contrib_401k_er=3382.50,
        deduct_health_ee=3200.00,
        contrib_health_er=6400.00,
        net_pay=76101.75,
    )
    entries_by_type = generate_all_entries(summary)
    all_entries = [e for group in entries_by_type.values() for e in group]

    if fmt == "xero":
        content = to_xero_csv(all_entries)
        filename = "journal_entries_xero.csv"
    else:
        content = to_csv(all_entries)
        filename = "journal_entries.csv"

    return Response(
        content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Component & example endpoints
# ---------------------------------------------------------------------------

@app.route("/api/components")
def api_components():
    category = request.args.get("category")
    components = list_components(category=category)
    return jsonify([
        {
            "name": c.name,
            "description": c.description,
            "category": c.category,
            "required_inputs": c.required_inputs,
            "outputs": c.outputs,
            "tags": c.tags,
        }
        for c in components
    ])


@app.route("/api/canonical-fields")
def api_canonical_fields():
    return jsonify([
        {"field": name, **meta}
        for name, meta in CANONICAL_FIELDS.items()
    ])


@app.route("/api/examples")
def api_examples():
    examples_dir = os.path.join(os.path.dirname(__file__), "examples")
    examples = []
    if os.path.isdir(examples_dir):
        for fname in sorted(os.listdir(examples_dir)):
            if fname.endswith(".json"):
                with open(os.path.join(examples_dir, fname)) as f:
                    try:
                        ex = json.load(f)
                        examples.append(ex)
                    except Exception:
                        pass
    return jsonify(examples)


@app.route("/api/sample-files")
def api_sample_files():
    """List available sample CSV files."""
    sample_dir = os.path.join(os.path.dirname(__file__), "examples", "sample_data")
    files = []
    if os.path.isdir(sample_dir):
        for fname in sorted(os.listdir(sample_dir)):
            if fname.endswith(".csv"):
                files.append({"name": fname, "path": f"examples/sample_data/{fname}"})
    return jsonify(files)


@app.route("/examples/sample_data/<path:filename>")
def serve_sample_file(filename):
    sample_dir = os.path.join(os.path.dirname(__file__), "examples", "sample_data")
    return send_from_directory(sample_dir, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
