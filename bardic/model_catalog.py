"""Dated, compatible model suggestions and explicit read-only account discovery.

The provider inventories establish visibility, not working inference, credit, or
JSON-schema support. Unknown capabilities and prices stay unknown. No book text
or generation request is used here; cached results never contain credentials.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import re
import threading
import time

import httpx

CATALOG_DATE = "2026-09-27"
PREPROCESS_DEFAULTS = {"gemini": "gemini-3.5-flash-lite", "openai": "gpt-6-luna",
                       "anthropic": "claude-haiku-4-5-20251001"}
SOURCES = {"gemini": "https://ai.google.dev/gemini-api/docs/models",
           "openai": "https://developers.openai.com/api/docs/models",
           "anthropic": "https://platform.claude.com/docs/en/models/overview"}
PRICE_SOURCES = {"gemini": "https://ai.google.dev/gemini-api/docs/pricing",
                 "openai": SOURCES["openai"], "anthropic": SOURCES["anthropic"]}


def _model(provider, model_id, label, tier, input_price, output_price, context, output, **extra):
    return {"id": model_id, "label": label, "tier": tier, "roles": ["preprocess", "analysis"],
            "structured_output": True, "context_tokens": context, "max_output_tokens": output,
            "input_usd_per_million": input_price, "output_usd_per_million": output_price,
            "source_url": SOURCES[provider], "pricing_source_url": PRICE_SOURCES[provider],
            "price_date": CATALOG_DATE, "availability": "unverified",
            "price_valid_until": None, "price_input_token_limit": None, **extra}


_CATALOG = {
    "gemini": [
        _model("gemini", "gemini-3.8-flash", "Gemini 3.8 Flash", "balanced", .75, 3.75, 1048576, 65536,
               price_valid_until="2026-12-31"),
        _model("gemini", "gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", "economy", .30, 2.50, 1048576, 65536),
        _model("gemini", "gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", "economy", .25, 1.50, 1048576, 65536),
        # Pro pricing varies with prompt length; do not silently quote its low tier.
        _model("gemini", "gemini-3.1-pro-preview", "Gemini 3.1 Pro (preview)", "deep", None, None, 1048576, 65536),
    ],
    "openai": [
        _model("openai", "gpt-6-sol", "GPT-6 Sol", "balanced", 2., 10., 1050000, 128000,
               price_input_token_limit=272000),
        _model("openai", "gpt-6-luna", "GPT-6 Luna", "economy", .10, .50, 1050000, 128000,
               price_input_token_limit=272000),
        _model("openai", "gpt-6-astra", "GPT-6 Astra", "deep", 10., 50., 1050000, 128000,
               price_input_token_limit=272000),
        _model("openai", "gpt-5.4-nano", "GPT-5.4 nano", "economy", .20, 1.25, 400000, 128000),
    ],
    "anthropic": [
        _model("anthropic", "claude-sonnet-5", "Claude Sonnet 5", "balanced", 2., 10., 1000000, 128000),
        _model("anthropic", "claude-haiku-4-5-20251001", "Claude Haiku 4.5", "economy", 1., 5., 200000, 64000),
        _model("anthropic", "claude-opus-5-5", "Claude Opus 5.5", "deep", 4., 20., 1000000, 128000),
        _model("anthropic", "claude-fable-5-1", "Claude Fable 5.1", "deep", 10., 50., 1000000, 128000),
    ],
}
ANALYSIS_CATALOG = {provider: [item["id"] for item in models] for provider, models in _CATALOG.items()}
# Self-hosted analysis providers (see local_services). Kept out of ANALYSIS_CATALOG,
# which lists the cloud accounts that Settings and account checks use.
SELF_HOSTED = frozenset({"local_llm", "booknlp", "novel_analyzer"})


def local_llm_catalog(base_url):
    """The model the owner's server is known to serve. Listing it does not prove it is loaded."""
    from .local_services import LOCAL_LLM_DEFAULT_MODEL
    return {"provider": "local_llm", "state": "curated" if base_url else "unconfigured", "catalog_date": CATALOG_DATE,
            "message": "Models on your own server. Enter another model ID if the server serves a different one.",
            "checked_at": None, "cached": False, "source_url": None,
            "models": [{"id": LOCAL_LLM_DEFAULT_MODEL, "label": "Qwen3.6 35B-A3B (self-hosted)", "tier": "balanced",
                        "roles": ["preprocess", "analysis"], "structured_output": True, "context_tokens": 262144,
                        "max_output_tokens": 16384, "input_usd_per_million": 0.0, "output_usd_per_million": 0.0,
                        "availability": "unverified", "source_url": None, "pricing_source_url": None, "price_date": None,
                        "price_valid_until": None, "price_input_token_limit": None}]}
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")


def catalog(provider):
    if provider not in _CATALOG:
        raise ValueError("Choose gemini, openai, or anthropic.")
    return {"provider": provider, "state": "curated", "catalog_date": CATALOG_DATE,
            "message": "Documented model choices. Refresh to check which models this key can see.",
            "checked_at": None, "cached": False, "source_url": SOURCES[provider], "partial": None,
            "models": deepcopy(_CATALOG[provider])}


def model_price(provider, model, *, input_tokens=None, today=None):
    """Published standard text prices, never a provider bill or exact estimate."""
    if provider in SELF_HOSTED:
        # The owner's own server: no per-request charge, whatever model it serves.
        return {"input_usd_per_million": 0.0, "output_usd_per_million": 0.0, "as_of": None,
                "source_url": None, "price_valid_until": None, "price_input_token_limit": None,
                "cost_basis": "self_hosted"}
    item = next((m for m in _CATALOG.get(provider, []) if m["id"] == model), {})
    result = {"input_usd_per_million": item.get("input_usd_per_million"),
              "output_usd_per_million": item.get("output_usd_per_million"),
              "as_of": item.get("price_date"), "source_url": item.get("pricing_source_url"),
              "price_valid_until": item.get("price_valid_until"),
              "price_input_token_limit": item.get("price_input_token_limit")}
    expired = item.get("price_valid_until") and (today or date.today()).isoformat() > item["price_valid_until"]
    above_tier = input_tokens is not None and item.get("price_input_token_limit") and input_tokens > item["price_input_token_limit"]
    if expired or above_tier:
        result.update(input_usd_per_million=None, output_usd_per_million=None)
    return result


def _safe_int(value):
    return value if type(value) is int and 0 < value <= 10000000 else None


def _entry(provider, raw):
    """Admit text-model candidates; keep unadvertised JSON support unknown."""
    if not isinstance(raw, dict):
        return None
    model_id = raw.get("name" if provider == "gemini" else "id")
    if isinstance(model_id, str) and provider == "gemini" and model_id.startswith("models/"):
        model_id = model_id[7:]
    if not isinstance(model_id, str) or not MODEL_ID.fullmatch(model_id):
        return None
    known = next((m for m in _CATALOG[provider] if m["id"] == model_id), None)
    capability = None
    context = output = None
    if provider == "gemini":
        if "generateContent" not in (raw.get("supportedGenerationMethods") or []):
            return None
        if not model_id.startswith("gemini-") or re.search(r"tts|image|live|audio|transcribe|robotics", model_id):
            return None
        context, output = _safe_int(raw.get("inputTokenLimit")), _safe_int(raw.get("outputTokenLimit"))
    elif provider == "openai":
        # Listing is not an endpoint compatibility claim. Specialized audio,
        # embeddings, research and coding APIs do not belong in this selector.
        if not re.match(r"(?:gpt-\d|o[134](?:-|$))", model_id) or re.search(
                r"audio|realtime|transcribe|tts|image|search|research|codex|cyber|chat-latest", model_id):
            return None
    else:
        if not model_id.startswith("claude-"):
            return None
        capabilities = raw.get("capabilities")
        if isinstance(capabilities, dict) and isinstance(capabilities.get("structured_outputs"), dict):
            value = capabilities["structured_outputs"].get("supported")
            capability = value if type(value) is bool else None
        if capability is False:
            return None
        context, output = _safe_int(raw.get("max_input_tokens")), _safe_int(raw.get("max_tokens"))
    result = deepcopy(known) if known else {
        "id": model_id, "label": model_id, "tier": "other", "roles": ["preprocess", "analysis"],
        "structured_output": capability, "context_tokens": None, "max_output_tokens": None,
        "input_usd_per_million": None, "output_usd_per_million": None, "price_date": None,
        "source_url": SOURCES[provider], "pricing_source_url": None,
        "price_valid_until": None, "price_input_token_limit": None,
    }
    # Use only normalized local/ID labels, not freeform provider text that could
    # echo credentials or contain arbitrary instructions.
    result["availability"] = "listed"
    if context:
        result["context_tokens"] = context
    if output:
        result["max_output_tokens"] = output
    if capability is not None:
        result["structured_output"] = capability
    return result


class ModelCatalog:
    """Memory-only provider inventories. Reading status never touches a provider."""

    def __init__(self):
        self._cache = {}
        self._gates = {provider: threading.Lock() for provider in _CATALOG}
        self._lock = threading.Lock()

    @staticmethod
    def _key(provider, api_key):
        return provider, hashlib.sha256(api_key.encode()).hexdigest()

    def view(self, provider, api_key=""):
        base = catalog(provider)
        with self._lock:
            previous = self._cache.get(provider)
            if previous and previous["key"] == self._key(provider, api_key):
                return deepcopy(previous["result"])
        return base

    def refresh(self, provider, api_key):
        result = catalog(provider)
        if not api_key:
            return {**result, "state": "missing_key", "message": "Add a key to refresh its model list. Documented choices remain available."}
        if not api_key.isascii() or any(c.isspace() for c in api_key):
            return {**result, "state": "invalid_key", "message": "The key contains invalid characters. Check it before refreshing models."}
        gate = self._gates[provider]
        if not gate.acquire(blocking=False):
            return {**self.view(provider, api_key), "state": "refreshing", "message": "A model refresh is already running for this provider."}
        try:
            key = self._key(provider, api_key)
            with self._lock:
                previous = self._cache.get(provider)
                ttl = 3600 if previous and previous["result"]["state"] == "ready" else 30
                if previous and previous["key"] == key and time.monotonic() - previous["time"] < ttl:
                    return {**deepcopy(previous["result"]), "cached": True}
            try:
                entries, partial = self._fetch(provider, api_key)
                by_id = {m["id"]: m for m in entries}
                merged = []
                for model in result["models"]:
                    model["availability"] = "unverified" if partial else "not_listed"
                    merged.append(by_id.pop(model["id"], model))
                merged.extend(by_id[k] for k in sorted(by_id))
                result.update(models=merged, state="ready", partial=partial,
                              message=("Model list refreshed" + (" (more models may exist)" if partial else "")
                                       + ". Listing confirms visibility only; it does not test generation or credits."))
            except _CatalogError as exc:
                result.update(state=exc.state, message=exc.message)
            except (httpx.HTTPError, ValueError, TypeError):
                result.update(state="unavailable", message="The provider model list could not be read. Documented choices remain available; try again later.")
            result["checked_at"] = datetime.now(timezone.utc).isoformat()
            with self._lock:
                self._cache[provider] = {"key": key, "time": time.monotonic(), "result": deepcopy(result)}
            return result
        finally:
            gate.release()

    @staticmethod
    def _fetch(provider, api_key):
        configs = {
            "openai": ("https://api.openai.com/v1/models", {"Authorization": f"Bearer {api_key}"}, {}),
            "gemini": ("https://generativelanguage.googleapis.com/v1beta/models", {"x-goog-api-key": api_key}, {"pageSize": 1000}),
            "anthropic": ("https://api.anthropic.com/v1/models", {"x-api-key": api_key, "anthropic-version": "2023-06-01"}, {"limit": 1000}),
        }
        url, headers, params = configs[provider]
        entries, cursors = [], set()
        cursor = None
        with httpx.Client(timeout=httpx.Timeout(12., connect=5.), follow_redirects=False) as client:
            for _ in range(3):
                response = client.get(url, headers=headers, params=params)
                if response.status_code != 200:
                    if response.status_code == 401:
                        raise _CatalogError("invalid_key", "The provider rejected this key. Documented model choices remain available.")
                    if response.status_code == 403:
                        raise _CatalogError("access_denied", "This key cannot list models. Documented choices remain available; model access may differ.")
                    if response.status_code == 429:
                        raise _CatalogError("rate_limited", "The provider limited this model-list request. Try again later; no generation was requested.")
                    raise _CatalogError("unavailable", "The provider could not list models. Documented choices remain available; try again later.")
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Invalid inventory")
                rows = payload.get("models" if provider == "gemini" else "data")
                if not isinstance(rows, list) or len(rows) > 5000:
                    raise ValueError("Invalid inventory")
                entries.extend(item for row in rows if (item := _entry(provider, row)))
                cursor = (payload.get("nextPageToken") if provider == "gemini" else
                          payload.get("last_id") if provider == "anthropic" and payload.get("has_more") else None)
                if not cursor:
                    break
                if not isinstance(cursor, str) or len(cursor) > 2000 or cursor in cursors:
                    raise ValueError("Invalid inventory cursor")
                cursors.add(cursor)
                params["pageToken" if provider == "gemini" else "after_id"] = cursor
        return entries, bool(cursor)


class _CatalogError(Exception):
    def __init__(self, state, message):
        self.state, self.message = state, message
