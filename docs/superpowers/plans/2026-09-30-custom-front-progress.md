# SDD ledger — plan: docs/superpowers/plans/2026-09-30-custom-front.md

Spec and plan approved by Nicolas Riault. Execution: native, same conversation.

Pre-flight: Tasks 1→2 share SessionStore/config; Tasks 2→4 share API payloads; Tasks 3→4 share NDJSON; no interface conflict.
Ruling: No Git repository exists here. Work in place, preserve engine/configuration, use this durable ledger instead of Git-dependent task scripts. Cost if wrong: no automatic commit rollback, existing files remain on disk.
Ruling: Use .venv/bin/python for all tests and installs, because this is the existing project runtime. No system Python changes.

Baseline: 29 existing tests passed.
Task 1: complete. tests/test_auth.py RED (module absent) → GREEN (7 tests).
Task 2: complete. tests/test_web.py RED (web factory absent) → GREEN (15 tests). Auth + web: 22 passed.
Task 3: complete. Seven additional stream/validation tests RED → GREEN; auth + web 29 passed.
Task 4: in progress. Static assets test RED (missing page). NDJSON decoder isolated into static/stream.mjs: four Node tests RED → GREEN, including split UTF-8 and unexpected EOF.
Ruling: Add static/stream.mjs so the stream parser can be verified without a browser or DOM. Cost if wrong: one extra locally served module.
Ruling: Replace the old Streamlit sidebar test with a web API selection/protocol test, because the front is intentionally replaced. Correct the Anthropic fixture termination to message_stop, preserving the existing provider's truncated-stream detection. Cost if wrong: fixture assumptions, engine code stays intact.
Browser QA: login/session resume, empty library, partial upload success, streaming response, citations, literal malicious HTML, interrupted response + retry, unavailable model server + correction, selected model application observed.
Live oMLX: real temporary index (1 fragment), retrieval, generation returned AZUR-742 with validation.txt as source. Production ChromaDB and config.yaml untouched.
Task 5: documentation updated for front, oMLX protocols/models/indexes, legacy Ollama, sessions, proxy/tunnel and testing.
Final review: independent reviewer (gpt-6-astra). Two P2 findings: draft erased after generation, stale library after index switch.
Final: fixed draft loss — real sendQuestion with controlled stream RED→GREEN.
Final: fixed stale library — out-of-order document lists and failed new-index load RED→GREEN. Old request generations invalidated on logout/index change.
Final review declined visual/keyboard/tunnel/full-suite checks to parent; no additional code behaviors declined. Parent owns browser proof; tunnel is not deployed in this task.
