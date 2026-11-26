# Integration recovery: offline evidence and acceptance plan

## Current boundaries

- The original development job and two already-saved non-held-out PDFs are copied by identifier into a **temporary test database**. The original PDFs are read in place; no real database claims or gaps are created. The mocked claims, reviewer and gap are explicitly labeled `SYNTHETIC TEST ONLY`.
- V3's evidence IDs are resolved to exact chunk text and end-exclusive chunk/page offsets. Structural linkage alone does not establish semantic support.
- A human can explicitly submit a supported, partially supported, contradicted or insufficient-evidence assessment with a self-reported reviewer name, rationale and timestamp. Review is a local self-attestation, not authenticated identity or automatic scientific verification. Reviews cannot yet be revised or revoked. Existing unprovenanced or stale results are displayed as unverified and excluded from gap packets.
- Gap packets require current human review, valid stored citations, rechecked source PDFs and claims produced for the same research job. At least two distinct papers must have supported claims. Partially supported and contradicted records remain labeled context, not supporting evidence. Saved candidates remain provisional.
- Current provider configuration requires both cloud keys at configuration-load time, even when only one provider is selected; there is no automatic fallback. Local budgets do not guarantee a cloud account cannot incur charges.

## Sanitized, offline provider request shapes

The following measurements use the same harmless synthetic text and V3 JSON Schema, a mocked transport, and a 256-token reservation. They are serialized body bytes, not measured provider tokens. No credentials, research passages, raw responses or provider messages are retained.

| Provider | POST endpoint path | Request options | Bytes | Output reserve | Timeout |
| --- | --- | --- | ---: | ---: | ---: |
| Gemini `gemini-3.5-flash-lite` | `/v1beta/models/gemini-3.5-flash-lite:generateContent` | `contents`, JSON MIME, `responseJsonSchema`, `maxOutputTokens`, `thinkingLevel=MINIMAL` | 1,261 | 256 | 30 s |
| Groq `openai/gpt-oss-120b` | `/openai/v1/chat/completions` | `model`, `messages`, `max_tokens`, `response_format=json_schema`; provider default best-effort mode | 1,263 | 256 | 30 s |

Both use `application/json` and their documented credential header; only header presence was inspected. Root schema: `claims` array. Candidate properties: `claim_statement` string, `claim_category` enum reference, `evidence_ids` array, and nullable `uncertainty_or_missing_information`. Mock response parsing and local Pydantic validation succeeded; HTTP status is not applicable to these mocks. Admission reserves one request, up to 3,840 input bytes and 256 output tokens under the 4,096 combined local bound. No retry or switching occurs.

Google documents the [`generateContent` endpoint](https://ai.google.dev/api/generate-content) and [JSON structured output](https://ai.google.dev/gemini-api/docs/structured-output). Gemini ignores `minLength` during constrained decoding; local validation remains required. Groq documents its [chat endpoint](https://console.groq.com/docs/api-reference) and [best-effort JSON Schema mode](https://console.groq.com/docs/structured-outputs); omission of `strict` selects the documented default, not strict mode. These comparisons do not establish acceptance of the V3 schema in either live account.

Prior live outcomes remain: Gemini V3 HTTP 400, Groq HTTP 403, and a minimal Gemini request HTTP 503 (`UNAVAILABLE`). Underlying cause is **unknown** in each case; those results do not establish a schema defect or account state. No past response body is reconstructed.

## Acquisition and UI

Mocked acquisition tests cover DNS, TLS, HTTP, timeout, malformed/oversized PDF, policy rejection, persistence, explicit retry and idempotent reuse. Client errors before FastAPI do not create backend attempt rows. Batch preparation skips saved failures. Existing source restrictions and page provenance remain in force.

## Next live acceptance checklist — approval required for each step

1. Confirm the current backend is running, local free-tier confirmation remains true, provider and account billing settings are reviewed by the developer, and the one-request budget is in effect. Local budgets alone do not guarantee zero charges.
2. With explicit approval, send **one** bounded Gemini V3 compatibility/extraction request using one non-held-out development chunk. Record admission, sanitized response diagnostics and any structurally valid evidence; do not retry or equate citation validity with scientific support.
3. Consider **one** separately approved Groq test only if Gemini remains unusable and provider access/account permissions are explicitly checked. Select Groq manually with new consent; no fallback.
4. After backend restart and explicit approval, perform **one** manual retry for the previously selected permitted PDF source. Inspect the persisted attempt and stop on failure; do not broaden network policy or try another source automatically.

Ollama remains manually selected and subject to its existing RAM checks. Neither provider comparison, model choice, semantic-review accuracy nor global novelty can be established by the offline harness.
# Offline V3 complete-passage admission

The web extraction action now selects up to three deterministic interior sentence spans from the stored chunk after the existing PDF, paper/job, and held-out guards pass. It excludes chunk-edge fragments and common abbreviation boundaries, verifies exact normalized page coordinates, and uses the same provider request builder as generation to check the complete JSON body against the existing input and output reservation. If no span is admissible, the action reports a blocked request without contacting a provider. Whole-chunk V3 callers remain supported; their historical identities are unchanged. Selected-span run identities include the selected evidence IDs. Sentence boundaries and scientific suitability are heuristic; source linkage does not establish semantic support.

Offline readiness check: development document `0bb74217-f47b-40a9-b7cf-770c3ddf1f33`, page 1, chunk `62a1efda-294a-4750-8941-84f5c03cd45d`, produced three page-matching interior spans. The Gemini V3 body measured 2,722 UTF-8 bytes against the 3,840-byte admission ceiling with 256 output tokens reserved. This check did not contact a model provider or establish live model compatibility. Any future live request requires explicit consent and the usual current preflight.

