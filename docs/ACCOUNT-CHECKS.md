# Account usage and credit checks

Implemented September 27, 2026. Settings provides an explicit small inference check for Gemini, OpenAI, and Anthropic. It uses the selected analysis model and that provider's current server credential. If a key or model is edited in the form, the check first applies those fields using the existing Settings behavior. Keys entered in the form remain session-only; `.env` is not rewritten.

## What the result means

- **Ready:** the selected model completed a tiny text response. The key and account worked at that moment.
- **Billing blocked:** the provider explicitly reported insufficient credits, a billing issue, or a spending limit. This is not an invented balance of zero.
- **Rate/quota limited:** the request hit a quota or rate limit. This alone does not establish whether credits remain.
- **Invalid key / access denied / model unavailable:** credential or model access needs attention.
- **Network/provider error / inconclusive:** the request could not establish readiness. A missing or malformed response is never marked ready.

The usage figures belong only to the check request, as returned by the provider. They are not account-wide consumption or a dollar estimate. Exact balance is explicitly unavailable through this feature; each card links to billing and usage dashboards. A text check does not verify TTS access, full-book affordability, sustained rate capacity, or literary-analysis quality.

## Why there is no universal balance number

OpenAI documents different error codes for depleted prepaid credits, hard spending limits, approved usage limits, and rate limits. Organization-level costs/usage are a separate administrative capability. See [spend limits](https://developers.openai.com/api/docs/guides/spend-limits) and [organization API](https://developers.openai.com/api/reference/python/resources/admin/subresources/organization).

Anthropic's historical Usage and Cost API requires suitable organization administration credentials and is unavailable for individual accounts. It reports consumption/costs rather than a universal remaining-credit balance. See [Usage and Cost API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api).

Google documents prepaid credit balances and spending/billing status in AI Studio. Its request quotas and spending caps can also prevent requests, so a generic `RESOURCE_EXHAUSTED` error cannot safely be labeled depleted credit. See [Gemini billing](https://ai.google.dev/gemini-api/docs/billing).

## Execution and storage

`POST /api/account-checks/{provider}` performs at most one bounded request with a fixed short prompt and no book content, tools, redirects, or retries. `GET /api/status` returns cached check results without making external requests. Checks run only on user action. All-provider checks run independently so one error does not hide the others.

Results are held in memory and reused for 30 seconds for the same provider/key/model; the displayed timestamp identifies when the request actually occurred. Concurrent duplicate checks return a conflict. Replacing a key or model invalidates its old result, including a check still in flight. Keys, key-derived identifiers, and provider response bodies are not returned as diagnostics or persisted in SQLite. Restarting clears the results and reloads `.env` credentials under the documented environment precedence.

See the [validation record](VALIDATION.md) for automated coverage and dated live check results. Settings preview captures remain local and are excluded from Git because they can contain account information.
