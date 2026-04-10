"""
Column classifier: uses Claude to map arbitrary payroll spreadsheet headers
to canonical payroll fields. Handles variations, abbreviations, and multi-row headers.
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ---------------------------------------------------------------------------
# Canonical field schema (~50 standard payroll fields)
# ---------------------------------------------------------------------------

CANONICAL_FIELDS: dict[str, dict[str, str]] = {
    # --- Employee identity ---
    "employee_id":       {"description": "Unique employee identifier / ID number", "category": "identity"},
    "employee_name":     {"description": "Full name of employee", "category": "identity"},
    "first_name":        {"description": "Employee first/given name", "category": "identity"},
    "last_name":         {"description": "Employee last/family name", "category": "identity"},
    "ssn_last4":         {"description": "Last 4 digits of Social Security Number", "category": "identity"},
    "hire_date":         {"description": "Date employee was hired", "category": "identity"},
    "termination_date":  {"description": "Date employee was terminated", "category": "identity"},
    "employment_type":   {"description": "Full-time, part-time, contractor, etc.", "category": "identity"},
    "flsa_status":       {"description": "FLSA exempt or non-exempt classification", "category": "identity"},

    # --- Organization ---
    "department":        {"description": "Department name or code", "category": "organization"},
    "department_code":   {"description": "Numeric/alpha department code", "category": "organization"},
    "cost_center":       {"description": "Cost center code for cost allocation", "category": "organization"},
    "location":          {"description": "Work location / office / site", "category": "organization"},
    "gl_account":        {"description": "General ledger account number", "category": "organization"},
    "job_title":         {"description": "Employee job title or position", "category": "organization"},
    "job_code":          {"description": "Job classification code", "category": "organization"},
    "manager_id":        {"description": "Manager employee ID", "category": "organization"},

    # --- Pay period ---
    "pay_period_start":  {"description": "Start date of the pay period", "category": "period"},
    "pay_period_end":    {"description": "End date of the pay period", "category": "period"},
    "pay_date":          {"description": "Date payment is issued", "category": "period"},
    "check_number":      {"description": "Paycheck or direct deposit trace number", "category": "period"},

    # --- Hours ---
    "hours_regular":     {"description": "Regular hours worked", "category": "hours"},
    "hours_overtime":    {"description": "Overtime hours worked (>40/week or >8/day)", "category": "hours"},
    "hours_doubletime":  {"description": "Double-time hours (CA 7th consecutive day, etc.)", "category": "hours"},
    "hours_pto":         {"description": "PTO / vacation hours taken", "category": "hours"},
    "hours_sick":        {"description": "Sick hours taken", "category": "hours"},
    "hours_holiday":     {"description": "Holiday hours paid", "category": "hours"},
    "hours_total":       {"description": "Total hours paid", "category": "hours"},
    "pay_rate":          {"description": "Hourly pay rate or salary rate", "category": "hours"},

    # --- Gross earnings ---
    "gross_regular":     {"description": "Regular earnings (regular hours × rate)", "category": "earnings"},
    "gross_overtime":    {"description": "Overtime earnings", "category": "earnings"},
    "gross_doubletime":  {"description": "Double-time earnings", "category": "earnings"},
    "gross_bonus":       {"description": "Bonus pay", "category": "earnings"},
    "gross_commission":  {"description": "Commission earnings", "category": "earnings"},
    "gross_pto":         {"description": "PTO payout earnings", "category": "earnings"},
    "gross_other":       {"description": "Other/miscellaneous gross earnings", "category": "earnings"},
    "gross_total":       {"description": "Total gross pay (all earnings combined)", "category": "earnings"},

    # --- Employee deductions (pre-tax) ---
    "deduct_401k_ee":    {"description": "Employee 401(k) / 403(b) contribution (pre-tax)", "category": "deductions_pretax"},
    "deduct_health_ee":  {"description": "Employee health insurance premium (pre-tax)", "category": "deductions_pretax"},
    "deduct_dental_ee":  {"description": "Employee dental insurance premium", "category": "deductions_pretax"},
    "deduct_vision_ee":  {"description": "Employee vision insurance premium", "category": "deductions_pretax"},
    "deduct_fsa":        {"description": "Flexible Spending Account (FSA) contribution", "category": "deductions_pretax"},
    "deduct_hsa_ee":     {"description": "Employee HSA contribution", "category": "deductions_pretax"},
    "deduct_transit":    {"description": "Pre-tax transit/commuter benefit", "category": "deductions_pretax"},

    # --- Employee deductions (post-tax) ---
    "deduct_roth_401k":  {"description": "Roth 401(k) contribution (post-tax)", "category": "deductions_posttax"},
    "deduct_life_ins":   {"description": "Supplemental life insurance (post-tax)", "category": "deductions_posttax"},
    "deduct_garnishment":{"description": "Wage garnishment (child support, levy, creditor)", "category": "deductions_posttax"},
    "deduct_other_post": {"description": "Other post-tax deductions", "category": "deductions_posttax"},

    # --- Employee taxes withheld ---
    "tax_federal_it":    {"description": "Federal income tax withheld (FIT)", "category": "taxes_employee"},
    "tax_state_it":      {"description": "State income tax withheld (SIT)", "category": "taxes_employee"},
    "tax_local_it":      {"description": "Local / city income tax withheld", "category": "taxes_employee"},
    "tax_ss_ee":         {"description": "Employee Social Security tax (6.2%)", "category": "taxes_employee"},
    "tax_medicare_ee":   {"description": "Employee Medicare tax (1.45%)", "category": "taxes_employee"},
    "tax_addl_medicare": {"description": "Additional Medicare tax (0.9% over threshold)", "category": "taxes_employee"},
    "tax_sdi_ee":        {"description": "State Disability Insurance withheld from employee", "category": "taxes_employee"},
    "tax_sui_ee":        {"description": "State Unemployment Insurance withheld (employee-paid states)", "category": "taxes_employee"},

    # --- Employer taxes / contributions ---
    "tax_ss_er":         {"description": "Employer Social Security match (6.2%)", "category": "taxes_employer"},
    "tax_medicare_er":   {"description": "Employer Medicare match (1.45%)", "category": "taxes_employer"},
    "tax_futa":          {"description": "Federal Unemployment Tax (FUTA) — employer only", "category": "taxes_employer"},
    "tax_suta_er":       {"description": "State Unemployment Tax (SUTA/SUI) — employer", "category": "taxes_employer"},
    "tax_workers_comp":  {"description": "Workers compensation insurance premium", "category": "taxes_employer"},
    "contrib_401k_er":   {"description": "Employer 401(k) match contribution", "category": "taxes_employer"},
    "contrib_hsa_er":    {"description": "Employer HSA contribution", "category": "taxes_employer"},
    "contrib_health_er": {"description": "Employer health insurance premium paid", "category": "taxes_employer"},

    # --- Net pay ---
    "net_pay":           {"description": "Employee net pay (take-home amount)", "category": "net"},
    "ytd_gross":         {"description": "Year-to-date gross earnings", "category": "ytd"},
    "ytd_federal_it":    {"description": "Year-to-date federal income tax withheld", "category": "ytd"},
    "ytd_ss":            {"description": "Year-to-date Social Security withheld", "category": "ytd"},
    "ytd_net":           {"description": "Year-to-date net pay", "category": "ytd"},
    "state_code":        {"description": "Two-letter state code for tax jurisdiction", "category": "jurisdiction"},
}


CANONICAL_FIELD_NAMES = sorted(CANONICAL_FIELDS.keys())

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ColumnMapping:
    source_column: str
    canonical_field: str | None
    confidence: float          # 0.0 – 1.0
    reasoning: str
    needs_review: bool         # True if confidence < threshold
    override: str | None = None  # human-provided override


@dataclass
class ClassificationResult:
    mappings: list[ColumnMapping]
    provider_detected: str | None   # "adp", "gusto", "paychex", "generic", etc.
    skipped_columns: list[str]      # columns that couldn't be mapped
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "provider_detected": self.provider_detected,
            "mappings": [
                {
                    "source_column": m.source_column,
                    "canonical_field": m.override or m.canonical_field,
                    "confidence": m.confidence,
                    "reasoning": m.reasoning,
                    "needs_review": m.needs_review,
                }
                for m in self.mappings
            ],
            "skipped_columns": self.skipped_columns,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a payroll data expert. Your job is to classify spreadsheet column headers
from payroll provider exports into canonical field names from a known schema.

## Canonical fields available:
{fields}

## Rules:
1. Map every source column to the BEST matching canonical field, or null if no match.
2. Return confidence 0.0–1.0. Use < 0.7 for ambiguous columns.
3. Mark needs_review: true when confidence < 0.7 or the column is ambiguous.
4. Detect the payroll provider if possible ("adp", "gusto", "paychex", "quickbooks", "generic").
5. Common abbreviations/variations to recognize:
   - "Fed W/H", "FIT", "Federal W/H", "Federal Income Tax" → tax_federal_it
   - "FICA EE", "SS EE", "Soc Sec EE", "OASDI" → tax_ss_ee
   - "FICA ER", "SS ER", "Soc Sec ER" → tax_ss_er
   - "Med EE", "Medicare EE", "Mcare" → tax_medicare_ee
   - "SIT", "State W/H", "State Tax" → tax_state_it
   - "SDI", "CA SDI", "NJ SDI" → tax_sdi_ee
   - "SUI", "SUTA", "State Unemployment" → tax_suta_er or tax_sui_ee
   - "401K EE", "401(k)", "Ret EE", "DFRR" → deduct_401k_ee
   - "401K ER", "Match", "Employer Match" → contrib_401k_er
   - "Net Pay", "Net Check", "Take Home", "Check Amount" → net_pay
   - "Reg Hrs", "Reg Hours", "Regular Hours" → hours_regular
   - "OT Hrs", "OT Hours", "Overtime Hours" → hours_overtime
   - "Gross", "Total Gross", "Gross Pay", "Gross Wages" → gross_total
   - "Dept", "Department", "Dept Code" → department or department_code
   - "CC", "Cost Ctr", "Cost Center" → cost_center
   - "Emp #", "Emp ID", "EE ID", "Employee Number" → employee_id
   - "YTD Gross", "YTD Earnings" → ytd_gross
   - "Reg Pay", "Regular Pay", "Reg Earnings" → gross_regular
   - "OT Pay", "Overtime Pay", "OT Earnings" → gross_overtime
6. Skip/null columns that are clearly not payroll data: row numbers, blank columns, notes, page numbers.
7. List any columns you could not map in skipped_columns.

## Output format (JSON only, no markdown):
{{
  "provider_detected": "adp" | "gusto" | "paychex" | "quickbooks" | "generic" | null,
  "mappings": [
    {{
      "source_column": "<original column name>",
      "canonical_field": "<canonical_field_name or null>",
      "confidence": 0.95,
      "reasoning": "brief explanation",
      "needs_review": false
    }}
  ],
  "skipped_columns": [],
  "warnings": []
}}
"""


def classify_columns(
    headers: list[str],
    sample_rows: list[list[str]] | None = None,
    review_threshold: float = 0.75,
    model: str = "claude-opus-4-6",
) -> ClassificationResult:
    """
    Classify a list of column headers into canonical payroll fields.

    Args:
        headers: Column header strings from the spreadsheet.
        sample_rows: Optional first 3 data rows (helps disambiguate).
        review_threshold: Confidence below this triggers needs_review=True.
        model: Claude model to use.

    Returns:
        ClassificationResult with per-column mappings and metadata.
    """
    fields_text = "\n".join(
        f"  {name}: {meta['description']} [{meta['category']}]"
        for name, meta in CANONICAL_FIELDS.items()
    )

    system = SYSTEM_PROMPT.format(fields=fields_text)

    user_content = f"Column headers to classify:\n{json.dumps(headers, indent=2)}"
    if sample_rows:
        user_content += f"\n\nSample data rows (first {len(sample_rows)}):\n"
        for row in sample_rows[:3]:
            user_content += json.dumps(row) + "\n"

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        result_data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned invalid JSON:\n{raw}") from exc

    mappings = []
    for m in result_data.get("mappings", []):
        confidence = float(m.get("confidence", 0.5))
        mappings.append(ColumnMapping(
            source_column=m["source_column"],
            canonical_field=m.get("canonical_field"),
            confidence=confidence,
            reasoning=m.get("reasoning", ""),
            needs_review=confidence < review_threshold or m.get("needs_review", False),
        ))

    return ClassificationResult(
        mappings=mappings,
        provider_detected=result_data.get("provider_detected"),
        skipped_columns=result_data.get("skipped_columns", []),
        warnings=result_data.get("warnings", []),
    )


def apply_overrides(result: ClassificationResult, overrides: dict[str, str]) -> ClassificationResult:
    """
    Apply human overrides to a ClassificationResult.

    Args:
        result: Original classification result.
        overrides: Dict of source_column -> canonical_field override.
    """
    for mapping in result.mappings:
        if mapping.source_column in overrides:
            mapping.override = overrides[mapping.source_column]
            mapping.needs_review = False
    return result


def build_field_map(result: ClassificationResult) -> dict[str, str]:
    """
    Return a flat dict of source_column -> canonical_field (using overrides where set).
    Only includes columns that have a mapping.
    """
    out = {}
    for m in result.mappings:
        canonical = m.override or m.canonical_field
        if canonical:
            out[m.source_column] = canonical
    return out


def normalize_row(row: dict[str, Any], field_map: dict[str, str]) -> dict[str, Any]:
    """
    Remap a data row dict from source column names to canonical field names.
    Drops unmapped columns.
    """
    return {
        field_map[col]: val
        for col, val in row.items()
        if col in field_map
    }


# ---------------------------------------------------------------------------
# CSV/Excel reading helpers
# ---------------------------------------------------------------------------

def read_csv_headers_and_samples(
    file_path: str,
    max_sample_rows: int = 3,
    encoding: str = "utf-8-sig",
) -> tuple[list[str], list[list[str]]]:
    """Read headers and sample rows from a CSV file."""
    import csv

    with open(file_path, newline="", encoding=encoding, errors="replace") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return [], []

    # Find header row: skip leading blank/summary rows
    header_idx = 0
    for i, row in enumerate(rows):
        non_empty = [c for c in row if c.strip()]
        if len(non_empty) >= 3:
            header_idx = i
            break

    headers = [h.strip() for h in rows[header_idx] if h.strip()]
    sample_data = []
    for row in rows[header_idx + 1: header_idx + 1 + max_sample_rows]:
        sample_data.append([c.strip() for c in row[:len(headers)]])

    return headers, sample_data


def read_excel_headers_and_samples(
    file_path: str,
    sheet_name: str | int = 0,
    max_sample_rows: int = 3,
) -> tuple[list[str], list[list[str]]]:
    """Read headers and sample rows from an Excel file."""
    try:
        import openpyxl
    except ImportError:
        raise ImportError("openpyxl is required to read Excel files: pip install openpyxl")

    wb = openpyxl.load_workbook(file_path, data_only=True)
    ws = wb[sheet_name] if isinstance(sheet_name, str) else wb.worksheets[sheet_name]

    rows = []
    for row in ws.iter_rows(values_only=True):
        rows.append([str(c).strip() if c is not None else "" for c in row])

    if not rows:
        return [], []

    # Find header row
    header_idx = 0
    for i, row in enumerate(rows):
        non_empty = [c for c in row if c]
        if len(non_empty) >= 3:
            header_idx = i
            break

    headers = [h for h in rows[header_idx] if h]
    sample_data = []
    for row in rows[header_idx + 1: header_idx + 1 + max_sample_rows]:
        sample_data.append(row[:len(headers)])

    return headers, sample_data


if __name__ == "__main__":
    import sys

    # Demo: classify the ADP-style headers
    adp_headers = [
        "Co Code", "Batch ID", "File #", "Last Name", "First Name",
        "Reg Hours", "O/T Hours", "Regular Earnings", "O/T Earnings",
        "Fed W/H", "FICA EE", "FICA ER", "Med EE", "Med ER",
        "St W/H", "SDI", "SUI ER", "401K EE", "401K ER",
        "Hlth EE", "Hlth ER", "Dental EE", "Garnish 1",
        "Net Pay", "YTD Gross", "Dept", "Cost Ctr",
    ]

    print("Classifying ADP-style headers...\n")
    result = classify_columns(adp_headers)
    print(f"Provider detected: {result.provider_detected}")
    print(f"Warnings: {result.warnings}")
    print("\nMappings:")
    for m in result.mappings:
        review = " ⚠ REVIEW" if m.needs_review else ""
        print(f"  {m.source_column:20s} → {str(m.canonical_field):25s} ({m.confidence:.0%}){review}")
