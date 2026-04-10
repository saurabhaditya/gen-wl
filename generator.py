"""
Prompt-to-workflow generator using Claude API.
"""

import json
import os
import re
from typing import Any

import anthropic

from components import registry_summary, REGISTRY

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are a payroll workflow architect. Your job is to convert a natural language description
of a payroll process into a structured JSON workflow using only the predefined components listed below.

## Available Components
{registry}

## Rules
1. Only use component names that appear in the registry above.
2. Order steps logically so that each step's required_inputs are satisfied by earlier steps' outputs.
3. Always include validate_payroll_data before any calculation steps.
4. Always include human_approval or manager_review before run_payroll.
5. Add error_handling as the first step when the prompt mentions reliability or large batches (>20 employees).
6. Include relevant config values when the prompt provides specifics (tax jurisdiction, employee count, etc.).
7. Add intermediate steps that are implied but not explicitly mentioned (e.g., map_columns after read_spreadsheet).
8. Return ONLY valid JSON — no markdown fences, no commentary.

## Output Format
{{
  "workflow_name": "short descriptive name",
  "description": "one sentence description",
  "estimated_employees": <integer or null>,
  "flow": [
    {{
      "step": "<component_name>",
      "config": {{...optional key-value config...}}
    }},
    ...
  ]
}}
"""


def generate_workflow(prompt: str, model: str = "claude-opus-4-6") -> dict[str, Any]:
    """
    Convert a natural language prompt into a structured workflow dict.

    Args:
        prompt: Natural language description of the payroll workflow.
        model: Claude model to use.

    Returns:
        Parsed workflow dict with 'flow', 'workflow_name', etc.

    Raises:
        ValueError: If the response cannot be parsed or contains unknown steps.
    """
    system = SYSTEM_PROMPT.format(registry=registry_summary())

    message = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if the model wrapped the JSON
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        workflow = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned invalid JSON:\n{raw}") from exc

    # Normalize: ensure every step has at least an empty config
    for step in workflow.get("flow", []):
        step.setdefault("config", {})

    return workflow


def generate_workflow_stream(prompt: str, model: str = "claude-opus-4-6"):
    """
    Same as generate_workflow but yields partial text chunks for streaming UIs.
    Yields str chunks and finally the parsed dict as the last item.
    """
    system = SYSTEM_PROMPT.format(registry=registry_summary())
    accumulated = []

    with client.messages.stream(
        model=model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            accumulated.append(text)
            yield text

    raw = "".join(accumulated).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        workflow = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned invalid JSON:\n{raw}") from exc

    for step in workflow.get("flow", []):
        step.setdefault("config", {})

    yield workflow


if __name__ == "__main__":
    import sys

    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "Process monthly payroll for 50 employees, validate hours, calculate taxes, "
        "get manager approval, then generate journal entries and send summary."
    )
    result = generate_workflow(prompt)
    print(json.dumps(result, indent=2))
