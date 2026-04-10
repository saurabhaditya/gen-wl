"""
Journal entry generator: converts normalized payroll data into proper
double-entry accounting journal entries.

Supports:
- Standard payroll JEs (expense debits / liability credits)
- Accrual entries (period-end accrual + reversing entry)
- Employer tax entries
- Benefits entries
- Segmented GL account structures (department, cost center, location)
- Output in JSON, CSV (QuickBooks IIF / generic import), or Xero format
"""

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


# ---------------------------------------------------------------------------
# Default GL Account Chart (standard payroll accounts)
# Standard 4-digit + segment: base-dept-cc
# ---------------------------------------------------------------------------

DEFAULT_GL_ACCOUNTS = {
    # Expense accounts (Debit on payroll run)
    "wages_regular":        {"number": "6100", "name": "Wages & Salaries — Regular",       "type": "expense"},
    "wages_overtime":       {"number": "6110", "name": "Wages & Salaries — Overtime",      "type": "expense"},
    "wages_bonus":          {"number": "6120", "name": "Wages & Salaries — Bonus/Commission","type": "expense"},
    "wages_pto":            {"number": "6130", "name": "Wages & Salaries — PTO/Holiday",   "type": "expense"},
    "payroll_tax_exp":      {"number": "6200", "name": "Payroll Tax Expense",               "type": "expense"},
    "benefits_exp":         {"number": "6300", "name": "Employee Benefits Expense",         "type": "expense"},
    "workers_comp_exp":     {"number": "6310", "name": "Workers Compensation Expense",      "type": "expense"},
    "401k_er_exp":          {"number": "6320", "name": "401(k) Employer Match Expense",     "type": "expense"},
    "health_er_exp":        {"number": "6330", "name": "Health Insurance Employer Expense", "type": "expense"},

    # Liability accounts (Credit on payroll run)
    "cash":                 {"number": "1010", "name": "Cash / Checking Account",           "type": "asset"},
    "wages_payable":        {"number": "2100", "name": "Wages Payable",                     "type": "liability"},
    "federal_it_payable":   {"number": "2200", "name": "Federal Income Tax Payable",        "type": "liability"},
    "state_it_payable":     {"number": "2210", "name": "State Income Tax Payable",          "type": "liability"},
    "local_it_payable":     {"number": "2220", "name": "Local Income Tax Payable",          "type": "liability"},
    "ss_payable":           {"number": "2230", "name": "Social Security Tax Payable",       "type": "liability"},
    "medicare_payable":     {"number": "2240", "name": "Medicare Tax Payable",              "type": "liability"},
    "sdi_payable":          {"number": "2250", "name": "SDI / SUI Employee Payable",        "type": "liability"},
    "futa_payable":         {"number": "2260", "name": "FUTA Tax Payable",                  "type": "liability"},
    "suta_payable":         {"number": "2270", "name": "SUTA Tax Payable",                  "type": "liability"},
    "401k_ee_payable":      {"number": "2300", "name": "401(k) Employee Contribution Payable","type": "liability"},
    "401k_er_payable":      {"number": "2310", "name": "401(k) Employer Match Payable",     "type": "liability"},
    "health_ee_payable":    {"number": "2320", "name": "Health Insurance Premium Payable",  "type": "liability"},
    "health_er_payable":    {"number": "2325", "name": "Health Ins Employer Payable",       "type": "liability"},
    "garnishment_payable":  {"number": "2330", "name": "Garnishment Payable",               "type": "liability"},
    "hsa_payable":          {"number": "2340", "name": "HSA Contribution Payable",          "type": "liability"},
    "accrued_wages":        {"number": "2110", "name": "Accrued Wages",                     "type": "liability"},
    "accrued_payroll_taxes":{"number": "2260", "name": "Accrued Payroll Taxes",             "type": "liability"},
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class JournalLine:
    account_number: str
    account_name: str
    debit: float
    credit: float
    description: str
    department: str = ""
    cost_center: str = ""
    entity: str = ""          # for intercompany

    @property
    def segmented_account(self) -> str:
        parts = [self.account_number]
        if self.department:
            parts.append(self.department)
        if self.cost_center:
            parts.append(self.cost_center)
        return "-".join(parts)

    def to_dict(self) -> dict:
        return {
            "account": self.segmented_account,
            "account_name": self.account_name,
            "debit": round(self.debit, 2),
            "credit": round(self.credit, 2),
            "description": self.description,
            "department": self.department,
            "cost_center": self.cost_center,
        }


@dataclass
class JournalEntry:
    entry_date: str          # ISO date string
    reference: str
    description: str
    entry_type: str          # "payroll", "accrual", "reversal", "tax_deposit", "benefits"
    lines: list[JournalLine] = field(default_factory=list)
    is_reversing: bool = False
    reversal_date: str | None = None

    def total_debits(self) -> float:
        return round(sum(l.debit for l in self.lines), 2)

    def total_credits(self) -> float:
        return round(sum(l.credit for l in self.lines), 2)

    def is_balanced(self) -> bool:
        return abs(self.total_debits() - self.total_credits()) < 0.02

    def to_dict(self) -> dict:
        return {
            "entry_date": self.entry_date,
            "reference": self.reference,
            "description": self.description,
            "entry_type": self.entry_type,
            "is_reversing": self.is_reversing,
            "reversal_date": self.reversal_date,
            "balanced": self.is_balanced(),
            "total_debits": self.total_debits(),
            "total_credits": self.total_credits(),
            "lines": [l.to_dict() for l in self.lines],
        }


@dataclass
class PayrollSummary:
    """Aggregated payroll totals used as JE inputs."""
    pay_date: str
    period_start: str
    period_end: str
    reference: str = "PR-001"
    entity: str = ""

    # Gross earnings
    gross_regular: float = 0.0
    gross_overtime: float = 0.0
    gross_bonus: float = 0.0
    gross_pto: float = 0.0
    gross_other: float = 0.0

    # Employee taxes
    tax_federal_it: float = 0.0
    tax_state_it: float = 0.0
    tax_local_it: float = 0.0
    tax_ss_ee: float = 0.0
    tax_medicare_ee: float = 0.0
    tax_sdi_ee: float = 0.0

    # Employer taxes
    tax_ss_er: float = 0.0
    tax_medicare_er: float = 0.0
    tax_futa: float = 0.0
    tax_suta_er: float = 0.0
    tax_workers_comp: float = 0.0

    # Employee deductions
    deduct_401k_ee: float = 0.0
    deduct_health_ee: float = 0.0
    deduct_dental_ee: float = 0.0
    deduct_hsa_ee: float = 0.0
    deduct_garnishment: float = 0.0
    deduct_other_post: float = 0.0

    # Employer contributions
    contrib_401k_er: float = 0.0
    contrib_health_er: float = 0.0
    contrib_hsa_er: float = 0.0

    # Net pay
    net_pay: float = 0.0

    # Breakdown by department/cost center (optional)
    by_department: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def gross_total(self) -> float:
        return self.gross_regular + self.gross_overtime + self.gross_bonus + self.gross_pto + self.gross_other

    @classmethod
    def from_normalized_rows(cls, rows: list[dict], pay_date: str,
                              period_start: str, period_end: str,
                              reference: str = "PR-001") -> "PayrollSummary":
        """Aggregate normalized row dicts into a PayrollSummary."""
        s = cls(pay_date=pay_date, period_start=period_start,
                period_end=period_end, reference=reference)

        def _sum(field: str) -> float:
            return sum(float(r.get(field, 0) or 0) for r in rows)

        s.gross_regular = _sum("gross_regular")
        s.gross_overtime = _sum("gross_overtime")
        s.gross_bonus = _sum("gross_bonus")
        s.gross_pto = _sum("gross_pto")
        s.gross_other = _sum("gross_other")
        s.tax_federal_it = _sum("tax_federal_it")
        s.tax_state_it = _sum("tax_state_it")
        s.tax_local_it = _sum("tax_local_it")
        s.tax_ss_ee = _sum("tax_ss_ee")
        s.tax_medicare_ee = _sum("tax_medicare_ee")
        s.tax_sdi_ee = _sum("tax_sdi_ee")
        s.tax_ss_er = _sum("tax_ss_er")
        s.tax_medicare_er = _sum("tax_medicare_er")
        s.tax_futa = _sum("tax_futa")
        s.tax_suta_er = _sum("tax_suta_er")
        s.tax_workers_comp = _sum("tax_workers_comp")
        s.deduct_401k_ee = _sum("deduct_401k_ee")
        s.deduct_health_ee = _sum("deduct_health_ee")
        s.deduct_hsa_ee = _sum("deduct_hsa_ee")
        s.deduct_garnishment = _sum("deduct_garnishment")
        s.deduct_other_post = _sum("deduct_other_post")
        s.contrib_401k_er = _sum("contrib_401k_er")
        s.contrib_health_er = _sum("contrib_health_er")
        s.net_pay = _sum("net_pay")

        # By department
        for row in rows:
            dept = str(row.get("department") or row.get("department_code") or "Unallocated")
            if dept not in s.by_department:
                s.by_department[dept] = {"gross_total": 0.0, "net_pay": 0.0}
            s.by_department[dept]["gross_total"] += float(row.get("gross_total") or 0)
            s.by_department[dept]["net_pay"] += float(row.get("net_pay") or 0)

        return s


# ---------------------------------------------------------------------------
# JE generation
# ---------------------------------------------------------------------------

def _acct(key: str) -> tuple[str, str]:
    """Return (account_number, account_name) for a GL key."""
    a = DEFAULT_GL_ACCOUNTS[key]
    return a["number"], a["name"]


def generate_payroll_je(
    summary: PayrollSummary,
    gl_accounts: dict | None = None,
    use_dept_segments: bool = True,
) -> JournalEntry:
    """
    Generate the main payroll journal entry.

    Dr: Wage/salary expense accounts
    Cr: Cash (net pay), tax liabilities, deduction liabilities
    """
    accts = {**DEFAULT_GL_ACCOUNTS, **(gl_accounts or {})}

    def a(key: str) -> tuple[str, str]:
        acc = accts[key]
        return acc["number"], acc["name"]

    lines: list[JournalLine] = []
    desc = f"Payroll run {summary.reference} | Period {summary.period_start} – {summary.period_end}"

    def debit(key: str, amount: float, note: str = "", dept: str = "") -> None:
        if amount <= 0:
            return
        num, name = a(key)
        lines.append(JournalLine(num, name, round(amount, 2), 0.0, note or desc, department=dept))

    def credit(key: str, amount: float, note: str = "", dept: str = "") -> None:
        if amount <= 0:
            return
        num, name = a(key)
        lines.append(JournalLine(num, name, 0.0, round(amount, 2), note or desc, department=dept))

    # --- Gross wage expense debits (by department if available) ---
    if summary.by_department and use_dept_segments:
        for dept, totals in summary.by_department.items():
            gt = totals.get("gross_total", 0.0)
            if gt > 0:
                num, name = a("wages_regular")
                lines.append(JournalLine(num, name, round(gt, 2), 0.0,
                                         f"Gross wages – {dept}", department=dept))
    else:
        debit("wages_regular", summary.gross_regular, "Regular wages")
        debit("wages_overtime", summary.gross_overtime, "Overtime wages")
        debit("wages_bonus", summary.gross_bonus, "Bonus / commission")
        debit("wages_pto", summary.gross_pto, "PTO / holiday pay")

    # --- Employer payroll tax expense debits ---
    total_er_taxes = (summary.tax_ss_er + summary.tax_medicare_er +
                      summary.tax_futa + summary.tax_suta_er + summary.tax_workers_comp)
    debit("payroll_tax_exp", total_er_taxes, "Employer payroll taxes (FICA ER + FUTA + SUTA)")

    # --- Employer benefit contribution expense debits ---
    total_er_benefits = summary.contrib_401k_er + summary.contrib_health_er + summary.contrib_hsa_er
    if summary.contrib_401k_er:
        debit("401k_er_exp", summary.contrib_401k_er, "401(k) employer match")
    if summary.contrib_health_er:
        debit("health_er_exp", summary.contrib_health_er, "Health insurance employer premium")

    # --- Credits: net pay to cash ---
    credit("cash", summary.net_pay, "Net pay disbursed")

    # --- Credits: employee tax liabilities ---
    credit("federal_it_payable", summary.tax_federal_it, "Federal income tax withheld")
    credit("state_it_payable", summary.tax_state_it, "State income tax withheld")
    credit("local_it_payable", summary.tax_local_it, "Local income tax withheld")
    credit("ss_payable",
           summary.tax_ss_ee + summary.tax_ss_er,
           "Social Security tax (EE + ER)")
    credit("medicare_payable",
           summary.tax_medicare_ee + summary.tax_medicare_er,
           "Medicare tax (EE + ER)")
    credit("sdi_payable", summary.tax_sdi_ee, "SDI withheld")
    credit("futa_payable", summary.tax_futa, "FUTA payable")
    credit("suta_payable", summary.tax_suta_er, "SUTA payable")

    # --- Credits: employee deduction liabilities ---
    credit("401k_ee_payable", summary.deduct_401k_ee, "401(k) employee contribution payable")
    credit("401k_er_payable", summary.contrib_401k_er, "401(k) employer match payable")
    credit("health_ee_payable",
           summary.deduct_health_ee + summary.contrib_health_er,
           "Health insurance premiums payable")
    credit("hsa_payable",
           summary.deduct_hsa_ee + summary.contrib_hsa_er,
           "HSA contributions payable")
    credit("garnishment_payable", summary.deduct_garnishment, "Garnishment payable")

    return JournalEntry(
        entry_date=summary.pay_date,
        reference=summary.reference,
        description=desc,
        entry_type="payroll",
        lines=lines,
    )


def generate_accrual_je(
    summary: PayrollSummary,
    accrual_date: str,
    accrual_days: int,
    work_days_in_period: int = 10,
) -> tuple[JournalEntry, JournalEntry]:
    """
    Generate period-end payroll accrual JE + reversing entry.

    Accrues wages earned but not yet paid (e.g., last 5 days of month
    when pay date falls in the next month).

    Returns (accrual_entry, reversing_entry).
    """
    daily_gross = summary.gross_total / max(work_days_in_period, 1)
    daily_taxes = (summary.tax_ss_er + summary.tax_medicare_er) / max(work_days_in_period, 1)
    accrual_amount = round(daily_gross * accrual_days, 2)
    tax_accrual = round(daily_taxes * accrual_days, 2)

    reversal_date = _next_month_first(accrual_date)
    accrual_desc = (f"Payroll accrual: {accrual_days} days of "
                    f"{summary.period_start}–{summary.period_end} period")

    accrual_lines = [
        JournalLine(*_acct("wages_regular"), accrual_amount, 0.0, accrual_desc),
        JournalLine(*_acct("payroll_tax_exp"), tax_accrual, 0.0, "Accrued employer payroll taxes"),
        JournalLine(*_acct("accrued_wages"), 0.0, accrual_amount, accrual_desc),
        JournalLine(*_acct("accrued_payroll_taxes"), 0.0, tax_accrual, "Accrued payroll taxes"),
    ]

    reversal_lines = [
        JournalLine(*_acct("accrued_wages"), accrual_amount, 0.0, f"REVERSAL: {accrual_desc}"),
        JournalLine(*_acct("accrued_payroll_taxes"), tax_accrual, 0.0, "REVERSAL: Accrued payroll taxes"),
        JournalLine(*_acct("wages_regular"), 0.0, accrual_amount, f"REVERSAL: {accrual_desc}"),
        JournalLine(*_acct("payroll_tax_exp"), 0.0, tax_accrual, "REVERSAL: Accrued payroll taxes"),
    ]

    accrual_je = JournalEntry(
        entry_date=accrual_date,
        reference=f"{summary.reference}-ACC",
        description=accrual_desc,
        entry_type="accrual",
        lines=accrual_lines,
        is_reversing=False,
        reversal_date=reversal_date,
    )

    reversal_je = JournalEntry(
        entry_date=reversal_date,
        reference=f"{summary.reference}-REV",
        description=f"REVERSAL: {accrual_desc}",
        entry_type="reversal",
        lines=reversal_lines,
        is_reversing=True,
    )

    return accrual_je, reversal_je


def generate_tax_deposit_je(
    summary: PayrollSummary,
    deposit_date: str,
) -> JournalEntry:
    """
    JE for remitting tax deposits (EFTPS, state) — clears the liability accounts.

    Dr: Tax liability accounts
    Cr: Cash
    """
    lines = []
    total = 0.0
    desc = f"Tax deposit — {summary.reference}"

    def dr_liability(key: str, amount: float, note: str) -> None:
        nonlocal total
        if amount <= 0:
            return
        num, name = _acct(key)
        lines.append(JournalLine(num, name, round(amount, 2), 0.0, note))
        total += amount

    dr_liability("federal_it_payable", summary.tax_federal_it, "Federal income tax deposit")
    dr_liability("ss_payable",
                 summary.tax_ss_ee + summary.tax_ss_er,
                 "FICA SS deposit (EE + ER)")
    dr_liability("medicare_payable",
                 summary.tax_medicare_ee + summary.tax_medicare_er,
                 "FICA Medicare deposit (EE + ER)")
    dr_liability("futa_payable", summary.tax_futa, "FUTA deposit")

    if total > 0:
        num, name = _acct("cash")
        lines.append(JournalLine(num, name, 0.0, round(total, 2), "Cash — tax remittance"))

    return JournalEntry(
        entry_date=deposit_date,
        reference=f"{summary.reference}-TAX",
        description=desc,
        entry_type="tax_deposit",
        lines=lines,
    )


def generate_benefits_remittance_je(summary: PayrollSummary, remit_date: str) -> JournalEntry:
    """
    JE for remitting benefit contributions to carriers — clears payables.

    Dr: Benefits payable accounts
    Cr: Cash
    """
    lines = []
    total = 0.0
    desc = f"Benefits remittance — {summary.reference}"

    def dr(key: str, amt: float, note: str) -> None:
        nonlocal total
        if amt <= 0:
            return
        num, name = _acct(key)
        lines.append(JournalLine(num, name, round(amt, 2), 0.0, note))
        total += amt

    dr("401k_ee_payable", summary.deduct_401k_ee, "401(k) EE contribution remittance")
    dr("401k_er_payable", summary.contrib_401k_er, "401(k) ER match remittance")
    dr("health_ee_payable",
       summary.deduct_health_ee + summary.contrib_health_er,
       "Health insurance premium remittance")
    dr("hsa_payable",
       summary.deduct_hsa_ee + summary.contrib_hsa_er,
       "HSA contribution remittance")
    dr("garnishment_payable", summary.deduct_garnishment, "Garnishment remittance")

    if total > 0:
        num, name = _acct("cash")
        lines.append(JournalLine(num, name, 0.0, round(total, 2), "Cash — benefits remittance"))

    return JournalEntry(
        entry_date=remit_date,
        reference=f"{summary.reference}-BEN",
        description=desc,
        entry_type="benefits",
        lines=lines,
    )


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def to_json(entries: list[JournalEntry], indent: int = 2) -> str:
    return json.dumps([e.to_dict() for e in entries], indent=indent)


def to_csv(entries: list[JournalEntry]) -> str:
    """Generic CSV — importable into most GL systems."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Date", "Reference", "Description", "Entry Type",
        "Account", "Account Name", "Department", "Cost Center",
        "Debit", "Credit", "Line Description",
    ])
    for entry in entries:
        for line in entry.lines:
            writer.writerow([
                entry.entry_date,
                entry.reference,
                entry.description,
                entry.entry_type,
                line.segmented_account,
                line.account_name,
                line.department,
                line.cost_center,
                f"{line.debit:.2f}" if line.debit else "",
                f"{line.credit:.2f}" if line.credit else "",
                line.description,
            ])
    return output.getvalue()


def to_quickbooks_iif(entries: list[JournalEntry]) -> str:
    """QuickBooks IIF format for journal entry import."""
    lines = [
        "!TRNS\tTRNSTYPE\tDATE\tACCNT\tAMOUNT\tMEMO",
        "!SPL\tTRNSTYPE\tDATE\tACCNT\tAMOUNT\tMEMO",
        "!ENDTRNS",
    ]
    for entry in entries:
        for i, line in enumerate(entry.lines):
            amount = line.debit - line.credit  # positive = debit in QB IIF
            row_type = "TRNS" if i == 0 else "SPL"
            lines.append(
                f"{row_type}\tGENERAL JOURNAL\t{entry.entry_date}\t"
                f"{line.account_name}\t{amount:.2f}\t{line.description}"
            )
        lines.append("ENDTRNS")
    return "\n".join(lines)


def to_xero_csv(entries: list[JournalEntry]) -> str:
    """Xero manual journal import CSV format."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "*Narration", "*JournalDate", "Reference",
        "*AccountCode", "Description", "*TaxType",
        "*Debit", "*Credit", "TrackingName1", "TrackingOption1",
    ])
    for entry in entries:
        for line in entry.lines:
            writer.writerow([
                entry.description,
                entry.entry_date,
                entry.reference,
                line.account_number,
                line.description,
                "TAX EXEMPT",
                f"{line.debit:.2f}" if line.debit else "0.00",
                f"{line.credit:.2f}" if line.credit else "0.00",
                "Department" if line.department else "",
                line.department,
            ])
    return output.getvalue()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_month_first(date_str: str) -> str:
    """Return the first day of the next month given an ISO date string."""
    d = date.fromisoformat(date_str)
    if d.month == 12:
        return date(d.year + 1, 1, 1).isoformat()
    return date(d.year, d.month + 1, 1).isoformat()


def validate_entries(entries: list[JournalEntry]) -> list[str]:
    """Return list of balance errors across all entries."""
    errors = []
    for e in entries:
        if not e.is_balanced():
            diff = abs(e.total_debits() - e.total_credits())
            errors.append(
                f"{e.reference} ({e.entry_type}): out of balance by ${diff:.2f} "
                f"(Dr {e.total_debits():.2f} / Cr {e.total_credits():.2f})"
            )
    return errors


# ---------------------------------------------------------------------------
# Convenience: generate all standard JEs for a payroll run
# ---------------------------------------------------------------------------

def generate_all_entries(
    summary: PayrollSummary,
    include_accrual: bool = False,
    accrual_date: str | None = None,
    accrual_days: int = 0,
    work_days_in_period: int = 10,
    deposit_date: str | None = None,
    remit_date: str | None = None,
) -> dict[str, list[JournalEntry]]:
    """
    Generate the full set of payroll JEs for a pay run.

    Returns dict with keys: 'payroll', 'accrual', 'reversal', 'tax_deposit', 'benefits'
    """
    result: dict[str, list[JournalEntry]] = {"payroll": [], "accrual": [],
                                               "reversal": [], "tax_deposit": [], "benefits": []}

    result["payroll"].append(generate_payroll_je(summary))

    if include_accrual and accrual_date and accrual_days > 0:
        acc, rev = generate_accrual_je(summary, accrual_date, accrual_days, work_days_in_period)
        result["accrual"].append(acc)
        result["reversal"].append(rev)

    if deposit_date:
        result["tax_deposit"].append(generate_tax_deposit_je(summary, deposit_date))

    if remit_date:
        result["benefits"].append(generate_benefits_remittance_je(summary, remit_date))

    return result


if __name__ == "__main__":
    # Demo
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

    je = generate_payroll_je(summary)
    print(f"Payroll JE — Balanced: {je.is_balanced()} "
          f"(Dr {je.total_debits():,.2f} / Cr {je.total_credits():,.2f})")
    print(json.dumps(je.to_dict(), indent=2))
