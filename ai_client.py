"""Small real-LLM adapter: Ollama locally or an OpenAI-compatible cloud endpoint.

Reliability measures for small local models:
- Ollama structured outputs: the Plan JSON schema is passed as `format`, so decoding
  is constrained to the schema instead of hoping for valid JSON.
- Thinking is disabled for speed on a 4 GB GPU.
- Every response is still validated with Pydantic; on failure the model gets one
  repair attempt that includes the validation error. If that also fails, the caller
  receives a ModelError. A plan is never fabricated.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from pydantic import ValidationError

from brief_checks import timing_phrase
from plan import Plan, SYSTEM_PROMPT, model_output_schema


class ModelError(Exception):
    """A provider failed or returned an invalid plan."""

    def __init__(self, message: str, attempts: int = 0):
        super().__init__(message)
        self.attempts = attempts


@dataclass
class Generation:
    plan: Plan
    provider: str
    model: str
    attempts: int
    latency_ms: int
    errors: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)  # deterministic corrections applied after the model


def apply_brief_fixes(plan: Plan, brief: str) -> list[str]:
    """Recover the client's timing words when the model dropped them (never invents a date)."""
    fixes = []
    if not plan.deadline_mention:
        phrase = timing_phrase(brief)
        if phrase:
            plan.deadline_mention = phrase
            fixes.append(f"Copied the client's timing words from the brief: '{phrase}'.")
    if not plan.deadline and plan.deadline_mention:
        found = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", plan.deadline_mention))
        if len(found) == 1:
            plan.deadline = found.pop()
            fixes.append(f"Deadline {plan.deadline} taken from the date quoted in the brief.")
    return fixes


def provider_settings() -> tuple[str, str]:
    provider = os.getenv("AI_PROVIDER", "ollama").lower()
    if provider == "openai":
        return provider, os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    return provider, os.getenv("OLLAMA_MODEL", "qwen3.5:4b")


def _post_json(url: str, body: dict, headers: dict[str, str], timeout: int) -> dict:
    req = Request(url, data=json.dumps(body).encode("utf-8"),
                  headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read(2_000_000).decode("utf-8"))
    except HTTPError as exc:
        hint = " Is the model pulled? Run `ollama list`." if exc.code == 404 else ""
        raise ModelError(f"Model provider returned HTTP {exc.code}.{hint}") from exc
    except TimeoutError as exc:
        raise ModelError(f"The model did not answer within {timeout}s. A small local model may need longer; "
                         "raise MODEL_TIMEOUT or use a smaller brief.") from exc
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise ModelError(f"The model did not answer within {timeout}s.") from exc
        raise ModelError("Cannot reach the AI model. Confirm Ollama is running (`ollama serve`).") from exc
    except OSError as exc:
        raise ModelError("Cannot reach the AI model. Confirm Ollama is running (`ollama serve`).") from exc
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
        if isinstance(result, dict):
            # The schedule is computed server-side; ignore any due dates a model supplies.
            for task in result.get("tasks") or []:
                if isinstance(task, dict):
                    task.pop("due_date", None)
            # Small models sometimes quote an absolute date but leave `deadline` empty.
            # Recover it only when the quoted words contain one unambiguous ISO date.
            mention = result.get("deadline_mention")
            if not result.get("deadline") and isinstance(mention, str):
                found = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", mention))
                if len(found) == 1:
                    result["deadline"] = found.pop()
        return Plan.model_validate(result)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:6])
        raise ModelError(f"The model's plan failed validation ({problems}).") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise ModelError("The model did not return a valid structured plan.") from exc


def _call(provider: str, model: str, messages: list[dict], timeout: int) -> str:
    if provider == "ollama":
        url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
        body = {"model": model, "messages": messages, "format": model_output_schema(), "stream": False,
                "think": False, "options": {"temperature": 0, "num_ctx": 8192}}
        result = _post_json(url, body, {}, timeout)
        try:
            return result["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ModelError("Ollama returned an unexpected response.") from exc
    if provider == "openai":
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise ModelError("OPENAI_API_KEY is not configured.")
        url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        body = {"model": model, "messages": messages, "temperature": 0, "response_format": {"type": "json_object"}}
        result = _post_json(url, body, {"Authorization": f"Bearer {key}"}, timeout)
        try:
            return result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("The cloud provider returned an unexpected response.") from exc
    raise ModelError("AI_PROVIDER must be 'ollama' or 'openai'.")


def generate_plan(brief: str, client_name: str = "", max_attempts: int = 2) -> Generation:
    provider, model = provider_settings()
    timeout = int(os.getenv("MODEL_TIMEOUT", "300"))
    prompt = f"Client name (for context only): {client_name or 'Not specified'}\n\nCLIENT BRIEF:\n{brief}"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    errors: list[str] = []
    started = time.monotonic()
    for attempt in range(1, max_attempts + 1):
        content = _call(provider, model, messages, timeout)  # transport errors are not retried
        try:
            plan = normalize_model_response(content)
            fixes = apply_brief_fixes(plan, brief)
            return Generation(plan, provider, model, attempt, int((time.monotonic() - started) * 1000), errors, fixes)
        except ModelError as exc:
            errors.append(str(exc))
            # One repair round: show the model its own output and the exact validation problem.
            messages = messages[:2] + [
                {"role": "assistant", "content": content[:6000]},
                {"role": "user", "content": f"That response was rejected: {exc} Return the corrected JSON object only."},
            ]
    raise ModelError(f"{errors[-1]} Tried {max_attempts} times; try again or use a stronger model.", attempts=max_attempts)
