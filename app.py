"""
Flask web app for the payroll workflow generator.
"""

import json
import os
from typing import Any

from flask import Flask, jsonify, render_template, request, Response, stream_with_context

from components import REGISTRY, list_components
from generator import generate_workflow, generate_workflow_stream
from validator import validate_workflow

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")


@app.route("/")
def index():
    return render_template("index.html")


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
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
