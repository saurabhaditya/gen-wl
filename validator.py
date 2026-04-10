"""
Workflow validator: checks component existence, I/O dependencies, and business rules.
"""

from dataclasses import dataclass, field
from typing import Any

from components import REGISTRY, get_component


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.valid


BUSINESS_RULES = [
    {
        "id": "approval_before_payroll",
        "description": "human_approval or manager_review must precede run_payroll",
        "requires": ["human_approval", "manager_review"],
        "before": "run_payroll",
        "severity": "error",
    },
    {
        "id": "validate_before_calculate",
        "description": "validate_payroll_data must precede any pay calculation step",
        "requires": ["validate_payroll_data"],
        "before": "calculate_gross_pay",
        "severity": "error",
    },
    {
        "id": "gross_before_deductions",
        "description": "calculate_gross_pay must precede calculate_deductions",
        "requires": ["calculate_gross_pay"],
        "before": "calculate_deductions",
        "severity": "error",
    },
    {
        "id": "deductions_before_taxes",
        "description": "calculate_deductions must precede calculate_taxes",
        "requires": ["calculate_deductions"],
        "before": "calculate_taxes",
        "severity": "error",
    },
    {
        "id": "taxes_before_net",
        "description": "calculate_taxes must precede calculate_net_pay",
        "requires": ["calculate_taxes"],
        "before": "calculate_net_pay",
        "severity": "error",
    },
    {
        "id": "payroll_before_deposit",
        "description": "run_payroll must precede process_direct_deposit",
        "requires": ["run_payroll"],
        "before": "process_direct_deposit",
        "severity": "error",
    },
    {
        "id": "journal_before_gl",
        "description": "generate_journal_entries must precede post_to_gl",
        "requires": ["generate_journal_entries"],
        "before": "post_to_gl",
        "severity": "error",
    },
    {
        "id": "pay_stubs_before_archive",
        "description": "generate_pay_stubs should precede archive_records",
        "requires": ["generate_pay_stubs"],
        "before": "archive_records",
        "severity": "warning",
    },
]


def validate_workflow(workflow: dict[str, Any]) -> ValidationResult:
    """
    Validate a workflow dict against:
    1. Component registry existence
    2. I/O dependency ordering
    3. Business rules (approval gates, calculation order, etc.)

    Returns a ValidationResult with errors and warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    flow = workflow.get("flow", [])
    if not flow:
        return ValidationResult(valid=False, errors=["Workflow has no steps."])

    step_names: list[str] = []
    available_outputs: set[str] = set()

    for idx, step in enumerate(flow):
        name = step.get("step")
        position = idx + 1

        if not name:
            errors.append(f"Step {position} is missing the 'step' field.")
            continue

        component = get_component(name)
        if component is None:
            errors.append(
                f"Step {position} '{name}' is not in the component registry. "
                f"Known steps: {', '.join(sorted(REGISTRY.keys()))}"
            )
            step_names.append(name)
            continue

        # Check that required inputs are available from previous outputs
        missing_inputs = [
            inp for inp in component.required_inputs if inp not in available_outputs
        ]
        if missing_inputs:
            errors.append(
                f"Step {position} '{name}' requires inputs {missing_inputs} "
                f"that are not produced by any earlier step."
            )

        available_outputs.update(component.outputs)
        step_names.append(name)

    # Business rule checks
    for rule in BUSINESS_RULES:
        before_step = rule["before"]
        requires = rule["requires"]

        if before_step not in step_names:
            continue  # rule doesn't apply if the gated step isn't in the flow

        before_idx = step_names.index(before_step)
        satisfying = [r for r in requires if r in step_names and step_names.index(r) < before_idx]

        if not satisfying:
            msg = f"Business rule '{rule['id']}': {rule['description']}."
            if rule["severity"] == "error":
                errors.append(msg)
            else:
                warnings.append(msg)

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


def validate_and_report(workflow: dict[str, Any]) -> None:
    """Print a human-readable validation report to stdout."""
    result = validate_workflow(workflow)
    name = workflow.get("workflow_name", "Unnamed workflow")

    print(f"\nValidation report for: {name}")
    print("=" * 60)
    if result.valid:
        print("✓ Workflow is valid.")
    else:
        print(f"✗ Workflow has {len(result.errors)} error(s).")

    for err in result.errors:
        print(f"  [ERROR] {err}")
    for warn in result.warnings:
        print(f"  [WARN]  {warn}")
    print()


if __name__ == "__main__":
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path:
        with open(path) as f:
            workflow = json.load(f)
    else:
        # Minimal smoke test
        workflow = {
            "workflow_name": "Test",
            "flow": [
                {"step": "read_spreadsheet"},
                {"step": "run_payroll"},  # Missing approval — should error
            ],
        }

    validate_and_report(workflow)
