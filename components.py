"""
Payroll workflow component registry.
Each component defines its metadata, I/O contract, and optional config schema.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Component:
    name: str
    description: str
    category: str
    required_inputs: list[str]
    outputs: list[str]
    config_schema: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


REGISTRY: dict[str, Component] = {}


def register(component: Component) -> Component:
    REGISTRY[component.name] = component
    return component


# ---------------------------------------------------------------------------
# DATA INGESTION
# ---------------------------------------------------------------------------

register(Component(
    name="read_spreadsheet",
    description="Read employee and hours data from a spreadsheet file (CSV, XLSX).",
    category="ingestion",
    required_inputs=[],
    outputs=["raw_rows", "column_headers"],
    config_schema={
        "file_path": {"type": "string", "description": "Path or URL to the spreadsheet"},
        "sheet_name": {"type": "string", "default": "Sheet1"},
        "header_row": {"type": "integer", "default": 0},
    },
    tags=["data", "input"],
))

register(Component(
    name="map_columns",
    description="Map spreadsheet columns to canonical payroll field names.",
    category="ingestion",
    required_inputs=["raw_rows", "column_headers"],
    outputs=["mapped_rows"],
    config_schema={
        "mapping": {"type": "object", "description": "Dict of source_col -> canonical_field"},
        "auto_detect": {"type": "boolean", "default": True},
    },
    tags=["data", "mapping"],
))

register(Component(
    name="normalize_rows",
    description="Clean and normalize row data: trim whitespace, coerce types, fill defaults.",
    category="ingestion",
    required_inputs=["mapped_rows"],
    outputs=["normalized_rows"],
    config_schema={
        "date_format": {"type": "string", "default": "%Y-%m-%d"},
        "currency": {"type": "string", "default": "USD"},
    },
    tags=["data", "cleaning"],
))

# ---------------------------------------------------------------------------
# VALIDATION
# ---------------------------------------------------------------------------

register(Component(
    name="validate_payroll_data",
    description="Validate employee records: required fields, data types, value ranges.",
    category="validation",
    required_inputs=["normalized_rows"],
    outputs=["validated_rows", "validation_errors"],
    config_schema={
        "strict_mode": {"type": "boolean", "default": False},
        "required_fields": {
            "type": "array",
            "default": ["employee_id", "name", "hours_worked", "pay_rate"],
        },
    },
    tags=["validation"],
))

register(Component(
    name="validate_compliance",
    description="Check regulatory compliance: minimum wage, required deductions, documentation.",
    category="validation",
    required_inputs=["validated_rows"],
    outputs=["compliance_report", "compliance_errors"],
    config_schema={
        "jurisdiction": {"type": "string", "default": "federal"},
        "minimum_wage": {"type": "number", "default": 7.25},
    },
    tags=["validation", "compliance"],
))

register(Component(
    name="check_overtime_rules",
    description="Identify and flag overtime hours based on jurisdiction rules (FLSA, state).",
    category="validation",
    required_inputs=["validated_rows"],
    outputs=["overtime_flags", "overtime_hours"],
    config_schema={
        "weekly_threshold": {"type": "number", "default": 40},
        "daily_threshold": {"type": "number", "description": "Daily OT threshold (e.g. 8 for CA)"},
        "overtime_multiplier": {"type": "number", "default": 1.5},
    },
    tags=["validation", "overtime"],
))

# ---------------------------------------------------------------------------
# PAY CALCULATION
# ---------------------------------------------------------------------------

register(Component(
    name="calculate_gross_pay",
    description="Calculate gross pay from hours worked, pay rate, and overtime multipliers.",
    category="calculation",
    required_inputs=["validated_rows", "overtime_flags"],
    outputs=["gross_pay_records"],
    config_schema={
        "include_bonuses": {"type": "boolean", "default": True},
    },
    tags=["calculation", "pay"],
))

register(Component(
    name="calculate_deductions",
    description="Calculate pre-tax deductions: 401k, health insurance, FSA, HSA.",
    category="calculation",
    required_inputs=["gross_pay_records"],
    outputs=["deduction_records", "pre_tax_income"],
    config_schema={
        "deduction_types": {
            "type": "array",
            "default": ["401k", "health_insurance"],
        },
    },
    tags=["calculation", "deductions"],
))

register(Component(
    name="calculate_taxes",
    description="Calculate federal, state, and local tax withholding (FICA, income tax).",
    category="calculation",
    required_inputs=["pre_tax_income", "deduction_records"],
    outputs=["tax_records", "post_tax_income"],
    config_schema={
        "federal": {"type": "boolean", "default": True},
        "state": {"type": "string", "description": "Two-letter state code (e.g. CA, NY, TX)"},
        "local": {"type": "boolean", "default": False},
        "fica_rate": {"type": "number", "default": 0.0765},
        "tax_year": {"type": "integer", "description": "Tax year for rate tables"},
    },
    tags=["calculation", "taxes"],
))

register(Component(
    name="calculate_net_pay",
    description="Calculate final net pay after all deductions and taxes.",
    category="calculation",
    required_inputs=["post_tax_income", "tax_records"],
    outputs=["net_pay_records"],
    tags=["calculation", "pay"],
))

# ---------------------------------------------------------------------------
# HUMAN IN THE LOOP
# ---------------------------------------------------------------------------

register(Component(
    name="human_approval",
    description="Pause workflow and request human review and approval before proceeding.",
    category="approval",
    required_inputs=["net_pay_records"],
    outputs=["approval_status", "approval_notes"],
    config_schema={
        "approver_email": {"type": "string"},
        "approval_threshold": {"type": "number", "description": "Auto-approve if total payroll below this"},
        "timeout_hours": {"type": "integer", "default": 24},
    },
    tags=["human", "approval"],
))

register(Component(
    name="manager_review",
    description="Route specific records to line managers for review (e.g. overtime, anomalies).",
    category="approval",
    required_inputs=["net_pay_records", "overtime_flags"],
    outputs=["reviewed_records", "manager_notes"],
    config_schema={
        "review_overtime_only": {"type": "boolean", "default": True},
        "notify_via": {"type": "string", "default": "email", "enum": ["email", "slack", "teams"]},
    },
    tags=["human", "approval", "review"],
))

# ---------------------------------------------------------------------------
# PAYROLL EXECUTION
# ---------------------------------------------------------------------------

register(Component(
    name="run_payroll",
    description="Execute the payroll run: commit pay records to the payroll system.",
    category="execution",
    required_inputs=["net_pay_records", "approval_status"],
    outputs=["payroll_run_id", "payroll_summary"],
    config_schema={
        "payroll_system": {"type": "string", "enum": ["adp", "gusto", "workday", "internal"]},
        "pay_date": {"type": "string", "description": "ISO date string for pay date"},
        "dry_run": {"type": "boolean", "default": False},
    },
    tags=["execution", "payroll"],
))

register(Component(
    name="process_direct_deposit",
    description="Initiate ACH / direct deposit transfers to employee bank accounts.",
    category="execution",
    required_inputs=["payroll_run_id", "net_pay_records"],
    outputs=["deposit_confirmations", "failed_deposits"],
    config_schema={
        "bank_routing": {"type": "string", "description": "Originating bank routing number"},
        "effective_date": {"type": "string"},
        "batch_size": {"type": "integer", "default": 200},
    },
    tags=["execution", "banking"],
))

# ---------------------------------------------------------------------------
# ACCOUNTING
# ---------------------------------------------------------------------------

register(Component(
    name="generate_journal_entries",
    description="Generate double-entry accounting journal entries for payroll expenses.",
    category="accounting",
    required_inputs=["payroll_summary", "tax_records", "deduction_records"],
    outputs=["journal_entries"],
    config_schema={
        "chart_of_accounts": {"type": "string", "description": "COA version or name"},
        "cost_centers": {"type": "boolean", "default": True},
        "accrual_basis": {"type": "boolean", "default": True},
    },
    tags=["accounting", "journal"],
))

register(Component(
    name="post_to_gl",
    description="Post journal entries to the general ledger (QuickBooks, NetSuite, SAP, etc.).",
    category="accounting",
    required_inputs=["journal_entries"],
    outputs=["gl_posting_confirmation", "gl_reference_ids"],
    config_schema={
        "gl_system": {"type": "string", "enum": ["quickbooks", "netsuite", "sap", "xero", "internal"]},
        "period": {"type": "string", "description": "Accounting period (e.g. 2024-03)"},
        "auto_reconcile": {"type": "boolean", "default": False},
    },
    tags=["accounting", "gl"],
))

# ---------------------------------------------------------------------------
# REPORTING & DISTRIBUTION
# ---------------------------------------------------------------------------

register(Component(
    name="generate_pay_stubs",
    description="Generate PDF pay stubs for each employee.",
    category="reporting",
    required_inputs=["net_pay_records", "tax_records", "deduction_records"],
    outputs=["pay_stub_files"],
    config_schema={
        "format": {"type": "string", "default": "pdf", "enum": ["pdf", "html"]},
        "include_ytd": {"type": "boolean", "default": True},
        "logo_url": {"type": "string"},
    },
    tags=["reporting", "output"],
))

register(Component(
    name="send_summary",
    description="Send payroll run summary report to stakeholders via email or Slack.",
    category="reporting",
    required_inputs=["payroll_summary"],
    outputs=["notification_status"],
    config_schema={
        "recipients": {"type": "array", "items": {"type": "string"}},
        "channel": {"type": "string", "description": "Slack channel if using Slack"},
        "include_breakdown": {"type": "boolean", "default": True},
        "format": {"type": "string", "default": "email", "enum": ["email", "slack", "teams"]},
    },
    tags=["reporting", "notification"],
))

register(Component(
    name="archive_records",
    description="Archive all payroll records and supporting documents to long-term storage.",
    category="reporting",
    required_inputs=["payroll_run_id", "payroll_summary", "pay_stub_files"],
    outputs=["archive_location"],
    config_schema={
        "storage_backend": {"type": "string", "default": "s3", "enum": ["s3", "gcs", "azure", "local"]},
        "retention_years": {"type": "integer", "default": 7},
        "encrypt": {"type": "boolean", "default": True},
    },
    tags=["storage", "compliance"],
))

# ---------------------------------------------------------------------------
# TAX COMPLIANCE & FILINGS
# ---------------------------------------------------------------------------

register(Component(
    name="calculate_employer_taxes",
    description="Calculate employer-side FICA match (Social Security 6.2%, Medicare 1.45%), FUTA, and SUTA contributions.",
    category="calculation",
    required_inputs=["gross_pay_records"],
    outputs=["employer_tax_records"],
    config_schema={
        "futa_rate": {"type": "number", "default": 0.006, "description": "Net FUTA rate after state credit"},
        "suta_rate": {"type": "number", "description": "State unemployment tax rate (employer)"},
        "suta_wage_base": {"type": "number", "description": "State SUI taxable wage base"},
        "social_security_wage_base": {"type": "number", "default": 176100, "description": "SS wage base for current year"},
    },
    tags=["calculation", "taxes", "employer"],
))

register(Component(
    name="apply_garnishments",
    description="Apply court-ordered wage garnishments: child support, creditor garnishments, tax levies. Enforces federal CCPA limits.",
    category="calculation",
    required_inputs=["net_pay_records"],
    outputs=["garnishment_records", "net_pay_after_garnishments"],
    config_schema={
        "ccpa_limit_pct": {"type": "number", "default": 0.25, "description": "Max % of disposable earnings per CCPA"},
        "priority_order": {"type": "array", "default": ["child_support", "tax_levy", "creditor"]},
    },
    tags=["calculation", "garnishments", "compliance"],
))

register(Component(
    name="calculate_pto_accruals",
    description="Accrue paid time off balances (vacation, sick, PTO) based on hours worked and policy rules.",
    category="calculation",
    required_inputs=["validated_rows"],
    outputs=["pto_accrual_records", "pto_balances"],
    config_schema={
        "accrual_policy": {"type": "string", "enum": ["per_pay_period", "per_hour", "annual_front_load"], "default": "per_pay_period"},
        "vacation_rate": {"type": "number", "description": "Hours accrued per pay period"},
        "sick_rate": {"type": "number", "description": "Sick hours accrued per pay period"},
        "max_carryover_hours": {"type": "number"},
    },
    tags=["calculation", "pto", "benefits"],
))

register(Component(
    name="calculate_employer_contributions",
    description="Calculate employer 401(k) match, HSA contributions, and other employer-funded benefit contributions.",
    category="calculation",
    required_inputs=["gross_pay_records", "deduction_records"],
    outputs=["employer_contribution_records"],
    config_schema={
        "k401_match_pct": {"type": "number", "default": 0.5, "description": "Employer match rate (e.g. 0.5 = 50% match)"},
        "k401_match_limit_pct": {"type": "number", "default": 0.06, "description": "Max % of compensation matched"},
        "hsa_employer_contribution": {"type": "number", "description": "Flat annual employer HSA contribution"},
    },
    tags=["calculation", "benefits", "employer"],
))

register(Component(
    name="calculate_workers_comp",
    description="Calculate workers' compensation insurance premiums by job classification code and payroll amount.",
    category="calculation",
    required_inputs=["gross_pay_records"],
    outputs=["workers_comp_records"],
    config_schema={
        "classification_codes": {"type": "object", "description": "Map of job_title -> NCCI class code"},
        "rate_per_100": {"type": "object", "description": "Map of class_code -> rate per $100 payroll"},
    },
    tags=["calculation", "workers_comp", "insurance"],
))

register(Component(
    name="generate_form_941",
    description="Prepare IRS Form 941 (Employer's Quarterly Federal Tax Return) with wages, tips, and tax withholdings.",
    category="tax_filing",
    required_inputs=["tax_records", "employer_tax_records", "payroll_summary"],
    outputs=["form_941_draft"],
    config_schema={
        "quarter": {"type": "string", "description": "Tax quarter (e.g. Q1, Q2, Q3, Q4)"},
        "tax_year": {"type": "integer"},
        "ein": {"type": "string", "description": "Employer Identification Number"},
        "deposit_schedule": {"type": "string", "enum": ["monthly", "semiweekly"], "default": "monthly"},
    },
    tags=["tax_filing", "irs", "941"],
))

register(Component(
    name="generate_form_940",
    description="Prepare IRS Form 940 (Annual FUTA Tax Return) reporting federal unemployment tax liability.",
    category="tax_filing",
    required_inputs=["employer_tax_records", "payroll_summary"],
    outputs=["form_940_draft"],
    config_schema={
        "tax_year": {"type": "integer"},
        "ein": {"type": "string"},
        "state_unemployment_paid": {"type": "boolean", "default": True},
    },
    tags=["tax_filing", "irs", "futa", "940"],
))

register(Component(
    name="generate_w2s",
    description="Generate IRS Form W-2 for each employee: annual wages, withholdings, benefits, and deductions.",
    category="tax_filing",
    required_inputs=["payroll_summary", "tax_records", "deduction_records", "employer_contribution_records"],
    outputs=["w2_records", "w2_files"],
    config_schema={
        "tax_year": {"type": "integer"},
        "ein": {"type": "string"},
        "include_box12_codes": {"type": "boolean", "default": True, "description": "Include 401k, HSA, etc. in Box 12"},
        "electronic_filing": {"type": "boolean", "default": True},
    },
    tags=["tax_filing", "irs", "w2", "year_end"],
))

register(Component(
    name="file_state_tax_returns",
    description="Prepare and file state income tax withholding returns and state unemployment (SUTA) filings.",
    category="tax_filing",
    required_inputs=["tax_records", "employer_tax_records"],
    outputs=["state_filing_confirmations"],
    config_schema={
        "states": {"type": "array", "items": {"type": "string"}, "description": "List of state codes to file in"},
        "filing_frequency": {"type": "string", "enum": ["monthly", "quarterly", "annual"], "default": "quarterly"},
    },
    tags=["tax_filing", "state", "suta"],
))

register(Component(
    name="reconcile_payroll_taxes",
    description="Reconcile payroll register totals against tax deposit records and form line items. Flags discrepancies.",
    category="validation",
    required_inputs=["tax_records", "employer_tax_records", "payroll_summary"],
    outputs=["reconciliation_report", "reconciliation_errors"],
    config_schema={
        "tolerance_amount": {"type": "number", "default": 0.01, "description": "Acceptable rounding difference in USD"},
    },
    tags=["validation", "reconciliation", "taxes"],
))

# ---------------------------------------------------------------------------
# PAYMENT OPERATIONS
# ---------------------------------------------------------------------------

register(Component(
    name="print_checks",
    description="Generate and print physical payroll checks for employees not on direct deposit.",
    category="execution",
    required_inputs=["net_pay_after_garnishments"],
    outputs=["check_records", "check_register"],
    config_schema={
        "check_stock": {"type": "string", "description": "Printer check stock type (e.g. top, middle, bottom)"},
        "starting_check_number": {"type": "integer"},
        "bank_account": {"type": "string"},
    },
    tags=["execution", "check", "payment"],
))

register(Component(
    name="void_reissue_check",
    description="Void a previously issued check or direct deposit and reissue a corrected payment.",
    category="execution",
    required_inputs=["payroll_run_id"],
    outputs=["void_confirmation", "reissued_payment_record"],
    config_schema={
        "reason": {"type": "string", "enum": ["lost", "stale", "incorrect_amount", "wrong_employee"]},
        "original_check_number": {"type": "string"},
        "notify_employee": {"type": "boolean", "default": True},
    },
    tags=["execution", "void", "correction"],
))

register(Component(
    name="run_off_cycle_payroll",
    description="Process an off-cycle (out-of-schedule) payroll for terminations, bonuses, or corrections.",
    category="execution",
    required_inputs=["net_pay_records", "approval_status"],
    outputs=["off_cycle_run_id", "off_cycle_summary"],
    config_schema={
        "reason": {"type": "string", "enum": ["termination", "bonus", "correction", "commission"]},
        "pay_date": {"type": "string"},
        "suppress_benefits": {"type": "boolean", "default": False},
    },
    tags=["execution", "off_cycle", "payroll"],
))

register(Component(
    name="remit_tax_deposits",
    description="Submit tax deposits (EFTPS for federal, state equivalents) for withheld and employer taxes.",
    category="execution",
    required_inputs=["tax_records", "employer_tax_records"],
    outputs=["deposit_confirmations", "deposit_reference_numbers"],
    config_schema={
        "federal_deposit_method": {"type": "string", "default": "eftps"},
        "deposit_due_date": {"type": "string", "description": "ISO date of required deposit due date"},
    },
    tags=["execution", "taxes", "eftps"],
))

register(Component(
    name="remit_garnishment_payments",
    description="Disburse withheld garnishment amounts to courts, agencies, and creditors.",
    category="execution",
    required_inputs=["garnishment_records"],
    outputs=["garnishment_remittance_confirmations"],
    config_schema={
        "payment_method": {"type": "string", "enum": ["check", "eft", "ach"], "default": "ach"},
        "remittance_deadline_days": {"type": "integer", "default": 7},
    },
    tags=["execution", "garnishments"],
))

register(Component(
    name="calculate_retro_pay",
    description="Calculate retroactive pay adjustments for prior-period corrections (rate changes, missed hours, classification fixes).",
    category="calculation",
    required_inputs=["validated_rows"],
    outputs=["retro_pay_records"],
    config_schema={
        "effective_date": {"type": "string", "description": "Date the corrected rate/hours take effect"},
        "periods_to_recalculate": {"type": "integer", "default": 1, "description": "Number of prior pay periods to retroactively recalculate"},
        "reason": {"type": "string", "enum": ["rate_change", "missed_hours", "reclassification", "bonus_true_up"]},
    },
    tags=["calculation", "retro", "correction"],
))

register(Component(
    name="generate_nacha_file",
    description="Generate a NACHA-formatted ACH file for direct deposit submission to the originating bank.",
    category="execution",
    required_inputs=["net_pay_after_garnishments", "payroll_run_id"],
    outputs=["nacha_file", "nacha_batch_summary"],
    config_schema={
        "company_id": {"type": "string", "description": "10-digit ACH company ID"},
        "odfi_routing": {"type": "string", "description": "Originating Depository Financial Institution routing number"},
        "effective_entry_date": {"type": "string", "description": "ISO date for when deposits should settle"},
        "sec_code": {"type": "string", "default": "PPD", "enum": ["PPD", "CCD"], "description": "ACH Standard Entry Class code"},
    },
    tags=["execution", "ach", "nacha", "banking"],
))

register(Component(
    name="cancel_payroll_run",
    description="Cancel or reverse a submitted payroll run. Voids all payments and reverses journal entries.",
    category="execution",
    required_inputs=["payroll_run_id", "approval_status"],
    outputs=["cancellation_confirmation", "reversal_journal_entries"],
    config_schema={
        "reason": {"type": "string", "description": "Reason for cancellation"},
        "void_checks": {"type": "boolean", "default": True},
        "reverse_ach": {"type": "boolean", "default": True, "description": "Initiate ACH reversal if deposits already sent"},
        "notify_employees": {"type": "boolean", "default": True},
    },
    tags=["execution", "cancellation", "reversal"],
))

register(Component(
    name="remit_benefits_contributions",
    description="Transmit employee and employer benefit contributions to insurance carriers, 401(k) custodians, HSA administrators.",
    category="execution",
    required_inputs=["deduction_records", "employer_contribution_records"],
    outputs=["benefits_remittance_confirmations"],
    config_schema={
        "carriers": {"type": "array", "description": "List of benefit carrier names/IDs"},
        "transmission_method": {"type": "string", "enum": ["file_feed", "api", "manual"], "default": "file_feed"},
    },
    tags=["execution", "benefits", "401k"],
))

# ---------------------------------------------------------------------------
# REPORTING (ADDITIONAL)
# ---------------------------------------------------------------------------

register(Component(
    name="generate_payroll_register",
    description="Produce the detailed payroll register report: all employees, earnings, deductions, taxes, and net pay.",
    category="reporting",
    required_inputs=["net_pay_records", "tax_records", "deduction_records"],
    outputs=["payroll_register_report"],
    config_schema={
        "format": {"type": "string", "default": "pdf", "enum": ["pdf", "csv", "xlsx"]},
        "group_by": {"type": "string", "enum": ["department", "location", "all"], "default": "all"},
    },
    tags=["reporting", "register"],
))

register(Component(
    name="generate_941_worksheet",
    description="Produce a working copy of the Form 941 reconciliation worksheet for accountant review.",
    category="reporting",
    required_inputs=["form_941_draft", "tax_records"],
    outputs=["form_941_worksheet"],
    config_schema={},
    tags=["reporting", "tax_filing", "941"],
))

register(Component(
    name="distribute_w2s",
    description="Deliver W-2 forms to employees via secure portal, email, or physical mail.",
    category="reporting",
    required_inputs=["w2_files"],
    outputs=["w2_distribution_log"],
    config_schema={
        "delivery_method": {"type": "string", "enum": ["portal", "email", "mail", "all"], "default": "portal"},
        "deadline": {"type": "string", "description": "W-2 furnishing deadline (Jan 31)"},
        "consent_required": {"type": "boolean", "default": True, "description": "Require employee e-consent for electronic delivery"},
    },
    tags=["reporting", "w2", "year_end"],
))

# ---------------------------------------------------------------------------
# ERROR HANDLING
# ---------------------------------------------------------------------------

register(Component(
    name="error_handling",
    description="Catch and log errors from prior steps; decide whether to halt or continue.",
    category="error_handling",
    required_inputs=[],
    outputs=["error_log", "error_count"],
    config_schema={
        "halt_on_error": {"type": "boolean", "default": True},
        "alert_email": {"type": "string"},
        "max_errors": {"type": "integer", "default": 0},
    },
    tags=["error", "reliability"],
))

register(Component(
    name="retry_step",
    description="Retry a failed step with exponential backoff.",
    category="error_handling",
    required_inputs=["error_log"],
    outputs=["retry_result"],
    config_schema={
        "max_attempts": {"type": "integer", "default": 3},
        "backoff_seconds": {"type": "integer", "default": 5},
        "step_to_retry": {"type": "string"},
    },
    tags=["error", "reliability"],
))

register(Component(
    name="notify_on_failure",
    description="Send an alert notification when the workflow encounters a critical failure.",
    category="error_handling",
    required_inputs=["error_log"],
    outputs=["alert_sent"],
    config_schema={
        "alert_channel": {"type": "string", "default": "email", "enum": ["email", "slack", "pagerduty"]},
        "severity": {"type": "string", "default": "high", "enum": ["low", "medium", "high", "critical"]},
        "recipients": {"type": "array"},
    },
    tags=["error", "notification"],
))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_component(name: str) -> Component | None:
    return REGISTRY.get(name)


def list_components(category: str | None = None) -> list[Component]:
    components = list(REGISTRY.values())
    if category:
        components = [c for c in components if c.category == category]
    return components


def registry_summary() -> str:
    """Return a compact text summary of all components for use in prompts."""
    lines = []
    by_category: dict[str, list[Component]] = {}
    for c in REGISTRY.values():
        by_category.setdefault(c.category, []).append(c)
    for cat, comps in by_category.items():
        lines.append(f"\n[{cat.upper()}]")
        for c in comps:
            inputs = ", ".join(c.required_inputs) or "none"
            outputs = ", ".join(c.outputs)
            lines.append(f"  {c.name}: {c.description}")
            lines.append(f"    inputs: {inputs}  |  outputs: {outputs}")
    return "\n".join(lines)
