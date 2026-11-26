# Gemini V3 request comparison (offline)

The production request uses `POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent`, with `x-goog-api-key` and JSON content type. It sends one `contents` text part, no `systemInstruction`, and a `generationConfig` with `maxOutputTokens: 256`, `thinkingConfig.thinkingLevel: MINIMAL`, `responseMimeType: application/json`, and `responseJsonSchema` containing the complete Pydantic V3 schema. No research text or credentials appear in the synthetic reference below.

Minimal native reference body, for comparison only:

```json
{"contents":[{"parts":[{"text":"Return a short synthetic label."}]}],"generationConfig":{"maxOutputTokens":256,"thinkingConfig":{"thinkingLevel":"MINIMAL"},"responseMimeType":"application/json","responseJsonSchema":{"type":"object","properties":{"label":{"type":"string"}},"required":["label"]}}}
```

The V3 native schema adds `$defs`, `$ref`, object `additionalProperties: false`, a nine-value claim-type enum, nested arrays with `minItems` and `maxItems`, a nullable `anyOf`, and `default: null` and `minLength` metadata. The [official structured-output guide](https://ai.google.dev/gemini-api/docs/structured-output) describes a supported subset, ignored string-length constraints, and possible rejection of complex schemas. These are compatibility considerations; no retained response identifies a V3 field or proves a schema defect. The [generateContent reference](https://ai.google.dev/api/generate-content) documents the endpoint and JSON response configuration. The [thinking guide](https://ai.google.dev/gemini-api/docs/thinking) lists MINIMAL for the configured model.

`AgentModelAssignment(gemini_response_mode='prompt_json', ...)` explicitly selects the alternative. It keeps the selected evidence, model, output and context limits, and prompt version, but removes both native `responseJsonSchema` and native JSON MIME mode and adds the complete response schema as instruction text. The shared request builder counts that entire body before admission. The existing JSON parser, Pydantic schema, allowed evidence ID checks, source guards and persistence logic apply equally to both modes. The native schema mode remains the default, and no automatic fallback is performed. The chosen mode is recorded in model configuration and changes the idempotency identity.

For explicitly opted-in local diagnosis, construct `GeminiClient(..., diagnostic_callback=callback)`. The callback receives a tuple of fixed categories such as `unsupported_argument_mentioned`; it never receives the provider message, request, credentials, source text, or arbitrary field path. Hints are heuristic and are not persisted in ModelRun. Free-form provider messages and descriptions are discarded. Unrecognized details remain unavailable.

A future one-request diagnostic could submit a harmless synthetic V3-shaped input using the explicit prompt-JSON mode, with the same model, thinking level, and limits. A successful response would show that this request without native schema can run; it would not prove exactly which part of the prior request caused HTTP 400. A failure could be unrelated to the native schema. Such a request requires separate approval and was not made here.

## Subsequent synthetic diagnostic

One separately approved synthetic `prompt_json` request returned HTTP 200 and `STOP`, with 289 input tokens and 85 candidate tokens. Strict JSON parsing failed; schema validation was not reached. Only a response hash and sanitized diagnostics were retained, so the exact text and cause cannot be reconstructed. A `STOP` reason does not establish that the generated text was a valid JSON object. No synthetic claims were persisted.

Offline checks confirm that the adapter parses the first Gemini candidate text rather than its response envelope, distinguishes empty text from malformed JSON, and rejects Markdown fences, surrounding prose, incomplete JSON, invalid top-level types, and missing fields. It performs no text repair. The existing prompt already requests strict JSON and includes the complete V3 schema; because the discarded response does not establish what formatting occurred, neither the prompt nor the parser was changed for this diagnosis. The adapter currently considers the first response part only; responses involving multiple text parts are not reassembled.

## Synthetic response-capture preparation

`SyntheticGeminiResponseCapture` is disabled unless a caller explicitly supplies it to a Gemini `prompt_json` request whose prompt starts with the fixed `BLINDSPOT_SYNTHETIC_JSON_DIAGNOSTIC_V1` marker. Native-schema calls, unmarked prompts, and normal Claim Extraction routes cannot enable capture. It writes only the first generated candidate text to `tmp/gemini-synthetic-diagnostics/<label>.candidate.txt`; requests, response envelopes, credentials, source passages, and provider error bodies are excluded. The directory is gitignored. Files are created owner-read/write and, on Windows, must receive a current-user-only ACL or capture fails and removes the new file.

The in-memory response diagnostics record the total number of candidate parts and coarse counts (`text`, `non_text`, `invalid_part`). Candidate text itself is not copied into diagnostics or model-run persistence. Multiple parts are not combined: a non-text first part remains a strict `missing_response_content` failure. `synthetic_prompt_json_diagnostic_prompt()` supplies the minimal harmless request for a separately approved diagnostic: it asks for a JSON V3 abstention object with no real evidence, papers, or held-out material.
