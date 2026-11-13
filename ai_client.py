"""Small real-LLM adapter: Ollama locally or an OpenAI-compatible cloud endpoint."""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from pydantic import ValidationError

from plan import Plan, SYSTEM_PROMPT


class ModelError(Exception):
    """A provider failed or returned an invalid plan."""


def _post_json(url: str, body: dict, headers: dict[str, str], timeout: int = 80) -> dict:
    req = Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read(2_000_000).decode("utf-8"))
    except HTTPError as exc:
        raise ModelError(f"Model provider returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ModelError("Cannot reach the AI model. Confirm Ollama is running, or configure a cloud provider.") from exc
    except (ValueError, UnicodeError) as exc:
        raise ModelError("The AI provider response was not valid JSON.") from exc


def normalize_model_response(content: str) -> Plan:
    """Refuse to silently invent plans if the model violates the contract."""
    value = content.strip()
    if value.startswith("```json"):
        value = value[7:]
    elif value.startswith("```"):
        value = value[3:]
    if value.endswith("```"):
        value = value[:-3]
    try:
        result = json.loads(value.strip())
        return Plan.model_validate(result)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ModelError("The model did not return a valid structured plan. Try again or use a stronger model.") from exc


def generate_plan(brief: str, client_name: str = "") -> Plan:
    provider = os.getenv("AI_PROVIDER", "ollama").lower()
    prompt = f"Client name (for context only): {client_name or 'Not specified'}\n\nCLIENT BRIEF:\n{brief}"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    if provider == "ollama":
        url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
        model = os.getenv("OLLAMA_MODEL", "qwen3.5:2b")
        result = _post_json(url, {"model": model, "messages": messages, "format": "json", "stream": False, "options": {"temperature": 0}}, {})
        try:
            content = result["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ModelError("Ollama returned an unexpected response.") from exc
    elif provider == "openai":
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise ModelError("OPENAI_API_KEY is not configured.")
        url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        result = _post_json(url, {"model": model, "messages": messages, "temperature": 0, "response_format": {"type": "json_object"}}, {"Authorization": f"Bearer {key}"})
        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("The cloud provider returned an unexpected response.") from exc
    else:
        raise ModelError("AI_PROVIDER must be 'ollama' or 'openai'.")
    return normalize_model_response(content)
