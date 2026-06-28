"""
Ollama integration for DepHeaven.

Talks to a locally-running Ollama instance (http://localhost:11434).
Completely optional — all callers check `is_available()` first and
fall back gracefully when Ollama is not running.

No API key. No account. Just `ollama serve` on the local machine.
"""

import json
import urllib.request
from typing import Optional

_BASE = "http://localhost:11434"
_DEFAULT_MODEL = "mistral"  # small, fast, good at code; user can override


def is_available() -> bool:
    """Return True if Ollama is running locally."""
    try:
        with urllib.request.urlopen(f"{_BASE}/api/tags", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def list_models() -> list[str]:
    """Return names of models installed in Ollama."""
    try:
        with urllib.request.urlopen(f"{_BASE}/api/tags", timeout=4) as r:
            data = json.loads(r.read())
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def best_model() -> str:
    """Pick the best available model for code tasks."""
    models = list_models()
    preference = [
        "codellama", "deepseek-coder", "qwen2.5-coder", "starcoder2",
        "mistral", "llama3", "llama2", "phi3", "gemma",
    ]
    for pref in preference:
        for m in models:
            if pref in m.lower():
                return m
    return models[0] if models else _DEFAULT_MODEL


def _stream_generate(prompt: str, model: str) -> str:
    """Call /api/generate with stream=False and return the full response text."""
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        f"{_BASE}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
        return data.get("response", "").strip()


def analyze_changelog(
    package: str,
    from_version: str,
    to_version: str,
    changelog_text: str,
    model: Optional[str] = None,
) -> dict:
    """
    Ask Ollama to read a changelog and identify breaking changes + migration steps.

    Returns:
        {
            "breaking": bool,
            "summary": str,          # one paragraph
            "api_changes": [str],     # list of specific API changes
            "migration_steps": [str], # ordered steps to migrate
        }
    """
    if not model:
        model = best_model()

    prompt = f"""You are a software dependency migration expert.

Package: {package}
Upgrading from version {from_version} to {to_version}

CHANGELOG:
---
{changelog_text[:4000]}
---

Respond in JSON with exactly these keys:
{{
  "breaking": true/false,
  "summary": "one paragraph describing what changed",
  "api_changes": ["list", "of", "specific", "renamed/removed/changed API calls"],
  "migration_steps": ["ordered", "steps", "to", "migrate", "existing", "code"]
}}

Only output the JSON object. No markdown fences. No explanation outside the JSON."""

    try:
        raw = _stream_generate(prompt, model)
        # strip potential markdown code fences
        raw = raw.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            raw = raw.rstrip("`").strip()
        return json.loads(raw)
    except Exception as e:
        return {
            "breaking": False,
            "summary": f"Could not parse Ollama response: {e}",
            "api_changes": [],
            "migration_steps": [],
        }


def suggest_code_fix(
    source_snippet: str,
    package: str,
    api_changes: list[str],
    language: str,
    model: Optional[str] = None,
) -> Optional[str]:
    """
    Given a source code snippet and a list of API changes, ask Ollama to
    rewrite the snippet to use the new API.

    Returns the rewritten snippet, or None if Ollama is unavailable/fails.
    """
    if not model:
        model = best_model()

    changes_text = "\n".join(f"- {c}" for c in api_changes)
    prompt = f"""You are a code migration assistant.

Language: {language}
Package: {package}
Known API changes in new version:
{changes_text}

Rewrite the following code snippet to use the updated API.
Only output the fixed code. No explanation. No markdown fences.

CODE:
{source_snippet}"""

    try:
        return _stream_generate(prompt, model)
    except Exception:
        return None


def explain_error(
    error_text: str,
    package: str,
    version: str,
    model: Optional[str] = None,
) -> Optional[str]:
    """Ask Ollama to explain a dependency-related error and suggest a fix."""
    if not model:
        model = best_model()

    prompt = f"""A Python/JS project is getting this error after upgrading {package} to {version}:

{error_text[:2000]}

Explain what caused this error and give the exact code change needed to fix it.
Be concise. Focus only on the {package} upgrade."""

    try:
        return _stream_generate(prompt, model)
    except Exception:
        return None
