# Payroll Workflow Generator

Convert natural language descriptions into structured payroll workflow JSON using Claude AI and a curated component library.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your API key
cp .env.example .env
# edit .env and add your ANTHROPIC_API_KEY

# 3. Run the web app
python app.py
# → open http://localhost:5000

# 4. Or use the CLI
python cli.py "Process monthly payroll for 50 employees, calculate taxes, get approval, post to GL"
```

## What It Does

Type a plain-English description of your payroll process. The system uses Claude to:
1. Analyze your intent
2. Select the right components from the registry
3. Order them correctly (respecting I/O dependencies)
4. Add implied intermediate steps
5. Return validated workflow JSON

**Example input:**
> "Run payroll for 200 employees, validate compliance for New York, calculate all taxes, require manager approval, process direct deposits, post to NetSuite, and archive everything."

**Example output:**
```json
{
  "workflow_name": "Full NY Payroll Run",
  "description": "Complete payroll with NY compliance, direct deposit, and NetSuite GL posting.",
  "estimated_employees": 200,
  "flow": [
    { "step": "error_handling", "config": { "halt_on_error": true } },
    { "step": "read_spreadsheet" },
    { "step": "map_columns", "config": { "auto_detect": true } },
    { "step": "normalize_rows" },
    { "step": "validate_payroll_data", "config": { "strict_mode": true } },
    { "step": "validate_compliance", "config": { "jurisdiction": "NY" } },
    ...
  ]
}
```

## Component Library

26 built-in payroll components across 8 categories:

| Category | Components |
|---|---|
| **Ingestion** | read_spreadsheet, map_columns, normalize_rows |
| **Validation** | validate_payroll_data, validate_compliance, check_overtime_rules |
| **Calculation** | calculate_gross_pay, calculate_deductions, calculate_taxes, calculate_net_pay |
| **Approval** | human_approval, manager_review |
| **Execution** | run_payroll, process_direct_deposit |
| **Accounting** | generate_journal_entries, post_to_gl |
| **Reporting** | generate_pay_stubs, send_summary, archive_records |
| **Error Handling** | error_handling, retry_step, notify_on_failure |

List all components:
```bash
python cli.py list
python cli.py list --category accounting
```

## Validation

Every generated workflow is automatically validated for:
- **Registry existence** — all step names are real components
- **I/O dependency order** — each step's required inputs are produced by earlier steps
- **Business rules** — approval before payroll, taxes before net pay, etc.

Validate an existing file:
```bash
python cli.py validate my_workflow.json
```

## Project Structure

```
workflow-generator/
├── components.py      # Component registry (all 26 payroll steps)
├── generator.py       # Claude API integration
├── validator.py       # Workflow validation logic
├── app.py             # Flask web app
├── cli.py             # Command-line interface
├── templates/
│   └── index.html     # Web UI (Tailwind CSS)
├── examples/          # Example prompts and expected outputs
└── requirements.txt
```

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key | required |
| `SECRET_KEY` | Flask session secret | `dev-secret-change-in-prod` |
| `PORT` | Web server port | `5000` |
