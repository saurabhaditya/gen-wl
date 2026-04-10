#!/usr/bin/env python3
"""
CLI interface for the payroll workflow generator.

Usage:
    python cli.py "Process monthly payroll for 50 employees..."
    python cli.py --validate workflow.json
    python cli.py --list-components
    python cli.py --list-components --category accounting
"""

import argparse
import json
import sys

from components import list_components, REGISTRY
from generator import generate_workflow
from validator import validate_and_report, validate_workflow


def cmd_generate(args: argparse.Namespace) -> None:
    prompt = " ".join(args.prompt)
    if not prompt:
        print("Error: provide a prompt string.", file=sys.stderr)
        sys.exit(1)

    print(f"\nGenerating workflow for: {prompt!r}\n", file=sys.stderr)

    try:
        workflow = generate_workflow(prompt)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.validate:
        result = validate_workflow(workflow)
        if not result.valid:
            print("Validation errors:", file=sys.stderr)
            for err in result.errors:
                print(f"  [ERROR] {err}", file=sys.stderr)
            for warn in result.warnings:
                print(f"  [WARN]  {warn}", file=sys.stderr)
            sys.exit(1)
        elif result.warnings:
            for warn in result.warnings:
                print(f"  [WARN]  {warn}", file=sys.stderr)

    output = json.dumps(workflow, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Workflow saved to {args.output}", file=sys.stderr)
    else:
        print(output)


def cmd_validate(args: argparse.Namespace) -> None:
    path = args.file
    try:
        with open(path) as f:
            workflow = json.load(f)
    except FileNotFoundError:
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON in {path}: {exc}", file=sys.stderr)
        sys.exit(1)

    validate_and_report(workflow)
    result = validate_workflow(workflow)
    sys.exit(0 if result.valid else 1)


def cmd_list_components(args: argparse.Namespace) -> None:
    components = list_components(category=args.category)
    if not components:
        print(f"No components found for category: {args.category}")
        return

    by_cat: dict[str, list] = {}
    for c in components:
        by_cat.setdefault(c.category, []).append(c)

    for cat, comps in sorted(by_cat.items()):
        print(f"\n{'='*50}")
        print(f"  {cat.upper()}")
        print(f"{'='*50}")
        for c in comps:
            print(f"\n  {c.name}")
            print(f"    {c.description}")
            if c.required_inputs:
                print(f"    Inputs:  {', '.join(c.required_inputs)}")
            else:
                print(f"    Inputs:  (none)")
            print(f"    Outputs: {', '.join(c.outputs)}")
            if c.config_schema:
                keys = list(c.config_schema.keys())
                print(f"    Config:  {', '.join(keys)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Payroll Workflow Generator — convert prompts to structured workflows."
    )
    subparsers = parser.add_subparsers(dest="command")

    # generate (default)
    gen = subparsers.add_parser("generate", help="Generate a workflow from a prompt")
    gen.add_argument("prompt", nargs="+", help="Natural language prompt")
    gen.add_argument("--validate", action="store_true", help="Validate after generating")
    gen.add_argument("-o", "--output", help="Save workflow JSON to this file")

    # validate
    val = subparsers.add_parser("validate", help="Validate an existing workflow JSON file")
    val.add_argument("file", help="Path to workflow JSON file")

    # list
    lst = subparsers.add_parser("list", help="List available components")
    lst.add_argument("--category", help="Filter by category")

    args = parser.parse_args()

    if args.command == "validate":
        cmd_validate(args)
    elif args.command == "list":
        cmd_list_components(args)
    else:
        # Default: treat bare arguments as a generate command
        if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
            args.prompt = sys.argv[1:]
            args.validate = False
            args.output = None
            cmd_generate(args)
        else:
            parser.print_help()


if __name__ == "__main__":
    main()
