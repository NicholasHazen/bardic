"""Small, explicit inference checks; these do not report an account balance.

Normal inference credentials do not provide a portable balance API. One bounded
request tests the selected model without sending book contents or retrying a
potentially billable call. Messages below are local text, never provider echoes.
"""
from __future__ import annotations

import re
from urllib.parse import quote

import httpx


PROVIDERS = {"gemini", "openai", "anthropic"}
CHECK_PROMPT = "Reply with only the word OK."
MAX_OUTPUT_TOKENS = 128


def _result(state, message, http_status=None, usage=None):
    return {"state": state, "message": message, "usage": usage, "http_status": http_status}


def _error_result(provider, status, payload):
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    codes = {value.lower() for name in ("code", "type", "status")
             if isinstance(value := error.get(name), str)}
    details = error.get("details")
    if isinstance(details, list):
        codes.update(item["reason"].lower() for item in details
                     if isinstance(item, dict) and isinstance(item.get("reason"), str))
    message = error.get("message", "")
    message = message.lower() if isinstance(message, str) else ""

    # Authentication and access failures take priority over incidental mentions
    # of billing in help text. Never return the message, which may echo a key.
    if provider == "openai" and status == 401 and any(
        marker in message for marker in ("ip not authorized", "ip allowlist", "must be a member", "organization membership")
    ):
        return _result("access_denied", "Account or network access was denied. Check organization membership and IP restrictions.", status)
    if status == 401 or codes & {"invalid_api_key", "authentication_error", "api_key_invalid", "api_key_expired", "unauthenticated", "authentication"} or any(
        marker in message for marker in ("api key not valid", "api key has expired", "invalid api key", "incorrect api key")
    ):
        return _result("invalid_key", "The provider rejected this API key. Check or replace it, then try again.", status)

    depleted = codes & {"credit_balance_exhausted", "payment_required"} or any(
        marker in message for marker in ("credit balance is too low", "credit balance too low", "prepayment credits are depleted", "no credits remaining", "credit balance is depleted", "insufficient credits")
    )
    if depleted:
        return _result("billing_blocked", "The provider reports insufficient credits. Add credits in its billing dashboard, then check again.", status)
    if codes & {"organization_spend_limit_exceeded", "project_spend_limit_exceeded", "organization_usage_limit_exceeded"}:
        return _result("billing_blocked", "The provider reports a spending or account usage limit. Review the limit in its billing dashboard.", status)
    if status == 402 or codes & {"billing_error", "billing_disabled", "billing_not_active", "billing_account_disabled"}:
        return _result("billing_blocked", "The provider reports a billing or payment problem. Review its billing dashboard.", status)
    if provider == "openai" and "insufficient_quota" in codes:
        return _result("billing_blocked", "The provider reports exhausted account quota or credits. Check the balance and account spending limits.", status)
    if provider == "anthropic" and re.search(r"(?:spend|spending) limit.{0,80}(?:reach|exceed)|(?:reach|exceed).{0,80}(?:spend|spending) limit", message):
        return _result("billing_blocked", "The provider reports a spending limit. Review organization and workspace limits in its billing dashboard.", status)
    if status == 403 or codes & {"permission_error", "permission_denied", "api_key_service_blocked"}:
        return _result("access_denied", "This key cannot access the selected model or service. Check permissions, project settings, and regional availability.", status)
    if status == 404 or codes & {"model_not_found", "model_not_available"} or re.search(
        r"model.{0,100}(?:does not exist|not found|not supported for|not available)", message
    ):
        return _result("model_unavailable", "The selected model is unavailable to this key. Check its model ID and your account's model access.", status)
    if status == 429 or codes & {"rate_limit_exceeded", "rate_limit_error", "quota_exceeded", "resource_exhausted", "too_many_requests", "slow_down"}:
        return _result("rate_limited", "The provider reports a rate or quota limit. Check its usage dashboard or try later; remaining credits are unknown.", status)
    if status >= 500:
        return _result("provider_error", "The provider could not complete the check. Try again later; account credits were not determined.", status)
    return _result("provider_error", "The provider rejected the check. Verify the selected model and account settings; credits were not determined.", status)


def _usage(provider, payload):
    raw = payload.get("usageMetadata" if provider == "gemini" else "usage")
    if not isinstance(raw, dict):
        return None
    fields = ({"input_tokens": "promptTokenCount", "output_tokens": "candidatesTokenCount", "total_tokens": "totalTokenCount"}
              if provider == "gemini" else {name: name for name in ("input_tokens", "output_tokens", "total_tokens")})
    result = {name: raw[source] for name, source in fields.items()
              if type(raw.get(source)) is int and raw[source] > 0}
    if "total_tokens" not in result and "input_tokens" in result and "output_tokens" in result:
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result or None


def _has_text(items, kind):
    return isinstance(items, list) and any(
        isinstance(item, dict) and item.get("type") == kind
        and isinstance(item.get("text"), str) and item["text"].strip()
        for item in items
    )


def _completed(provider, payload):
    if provider == "openai":
        output = payload.get("output")
        if isinstance(output, list) and any(
            isinstance(item, dict) and isinstance(item.get("content"), list)
            and any(isinstance(part, dict) and part.get("type") == "refusal" for part in item["content"])
            for item in output
        ):
            return False
        return payload.get("status") == "completed" and isinstance(output, list) and any(
            isinstance(item, dict) and item.get("type") == "message"
            and item.get("role") == "assistant" and item.get("status") in (None, "completed")
            and _has_text(item.get("content"), "output_text")
            for item in output
        )
    if provider == "anthropic":
        return (payload.get("type") == "message" and payload.get("role") in (None, "assistant")
                and payload.get("stop_reason") == "end_turn" and _has_text(payload.get("content"), "text"))
    candidates = payload.get("candidates")
    feedback = payload.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        return False
    if not isinstance(candidates, list):
        return False
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("finishReason") != "STOP":
            continue
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if isinstance(parts, list) and any(isinstance(part, dict) and not part.get("thought")
                                         and isinstance(part.get("text"), str) and part["text"].strip() for part in parts):
            return True
    return False


def check_account(provider: str, api_key: str, model: str) -> dict:
    """Make at most one tiny generation request to one fixed provider endpoint."""
    if provider not in PROVIDERS:
        raise ValueError("Account checks require gemini, openai, or anthropic.")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", model):
        raise ValueError("Choose a valid analysis model ID before checking the account.")
    if not api_key or not api_key.strip():
        return _result("missing_key", "Add an API key before checking this account.")
    if not api_key.isascii() or any(c.isspace() for c in api_key):
        return _result("invalid_key", "The API key contains invalid whitespace or characters. Check or replace it.")

    if provider == "openai":
        url = "https://api.openai.com/v1/responses"
        headers = {"Authorization": f"Bearer {api_key}"}
        body = {"model": model, "input": CHECK_PROMPT, "store": False, "max_output_tokens": MAX_OUTPUT_TOKENS}
    elif provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        body = {"model": model, "messages": [{"role": "user", "content": CHECK_PROMPT}], "max_tokens": MAX_OUTPUT_TOKENS}
    else:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe='')}:generateContent"
        headers = {"x-goog-api-key": api_key}
        body = {"contents": [{"role": "user", "parts": [{"text": CHECK_PROMPT}]}],
                "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS}}
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False) as client:
            response = client.post(url, headers=headers, json=body)
    except (httpx.HTTPError, ValueError, UnicodeError):
        return _result("network_error", "The provider could not be reached. Check your connection and try again; credits were not determined.")
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not response.is_success or isinstance(payload, dict) and payload.get("error"):
        return _error_result(provider, response.status_code, payload)
    if not isinstance(payload, dict):
        return _result("inconclusive", "The provider returned an unexpected response. Account readiness and credits could not be determined.", response.status_code)
    usage = _usage(provider, payload)
    if _completed(provider, payload):
        return _result("ready", "A small text request completed successfully with this model. Remaining credits and narration access are not checked.", response.status_code, usage)
    return _result("inconclusive", "The provider responded, but the small text check did not complete. Remaining credits are unknown; no automatic retry was made.", response.status_code, usage)
