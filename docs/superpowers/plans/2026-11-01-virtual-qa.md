# Real-Time Virtual QA Extension, Whisper Loop & Supervisor Escalation (v2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real-time "Virtual QA" extension on top of FreeSWITCH and FastAPI that monitors bridged agent calls via isolated stereo channel tailing, runs local regex checks and Vertex AI Gemini evaluations on non-silent utterances, injects coaching whispers exclusively to the agent via ElevenLabs TTS and FreeSWITCH ESL, and escalates to supervisors with real-time WebSocket alerts and live Listen/Whisper/Barge intervention controls.

**Architecture:** Monitored agent-to-customer calls are tagged in FreeSWITCH dialplan XML with unbuffered stereo recording (`RECORD_STEREO=true`, `enable_file_write_buffering=false`). An ESL background listener detects bridged calls and spawns an audio tailing task that extracts only the agent's channel, transcribes utterances with ElevenLabs STT, and evaluates policy violations. Coaching whispers are played into only the agent's ear via `uuid_broadcast <uuid> <wav> <leg>`; on repeated violations (`warn_count > max_warn_retries`), single-instance escalations are published to Redis Pub/Sub and broadcasted to supervisor WebSockets with direct eavesdrop action endpoints.

**Tech Stack:** Python 3.12, FastAPI, FreeSWITCH ESL, PostgreSQL, Redis Pub/Sub, ElevenLabs STT/TTS (`httpx`), Google Vertex AI Gemini (`google-genai`), NumPy, Wave/Audio, React 18, TypeScript, Tailwind CSS, Lucide Icons.

**Spec:** [docs/virtual_qa.md](file:///Users/tejaspatil/PBX/docs/virtual_qa.md)

---

## Global Constraints

- **A-leg UUID is canonical:** Recording filenames (`{QA_RECORDINGS_DIR}/{tenant_id}/{a_leg_uuid}.wav`), `uuid_broadcast`, `eavesdrop`, Redis keys, and WebSocket payloads all use the A-leg (originating channel) UUID. `qa_agent_leg` (`aleg` or `bleg`) is always relative to that UUID.
- **Tenant isolation everywhere:** Every Redis key is prefixed `tenant:{tenant_id}:...`; every database query, REST endpoint, supervisor dial code, and WebSocket message is strictly tenant-scoped.
- **Single-instance background work:** The ESL event listener and call audio monitors run under a single leader lock (`qa_leader`) across API workers or as a dedicated background worker, publishing events to Redis Pub/Sub.
- **Transcripts are sensitive:** Transcripts and snippet payloads are delivered only to supervisor/admin WebSockets and stored in `qa_events` with retention cleanup (`QA_EVENT_RETENTION_DAYS = 30`).
- **No secrets in repo/image:** Google Service Account JSON path is mounted and referenced via `GOOGLE_APPLICATION_CREDENTIALS`; API keys via `.env`.
- **Step sequence:** Step order strictly follows **0 → 1 → 5 → 2 → 3 → 4 → 6**.

---

## Review Focus

1. **Word-boundary keyword false positives:** The local policy checker must match words using strict regex boundaries (`\bhell\b`), ensuring words like "hello", "shell", and "Hellen" never trigger profanity/aggression violations.
2. **Audio channel bleed / Whisper leakage:** `uuid_broadcast <a_uuid> <wav> <qa_agent_leg>` must verify that the whisper audio plays strictly into the designated leg without echoing or mixing into the counterparty's bridge audio.
3. **WAV header live-file truncation:** When reading a live recording file, FreeSWITCH leaves the header length bytes unfinalized until hangup; the WAV reader must dynamically compute audio frames from file offset to EOF rather than failing on header chunk lengths.
4. **Off-by-one warning count synchronization:** Gemini must not determine the warning count string; the counter increments atomically via Redis `HINCRBY` first, and the whisper text is composed deterministically: `f"Warning {n} of {max_retries}. {coaching}"`.
5. **Supervisor cross-tenant eavesdrop prevention:** When dialing `*88<mode><agent_ext>` or invoking `POST /intervene`, the backend must verify that the target call belongs to the caller's authenticated `tenant_id` before invoking FreeSWITCH `eavesdrop`.

---

## Task Breakdown

### Task 0: Technical Spike & Behavior Verification

**Files:**
- Create: `docs/virtual_qa_spike.md`
- Create: `tests/spike_freeswitch_audio.py`

**Interfaces:**
- Consumes: FreeSWITCH ESL connection parameters (`FREESWITCH_ESL_HOST`, `FREESWITCH_ESL_PORT`, `FREESWITCH_ESL_PASSWORD` from `src.core.config`).
- Produces: `docs/virtual_qa_spike.md` documenting verified behavior for `uuid_broadcast` isolation (`aleg` vs `bleg`), `RECORD_STEREO` channel mapping (A-leg vs B-leg), `CHANNEL_BRIDGE` event variable shapes, and Gemini / ElevenLabs latency baselines.

- [ ] **Step 1: Write spike probe script**
  Create `tests/spike_freeswitch_audio.py` using `socket` to connect to FreeSWITCH ESL, test `api status`, test `RECORD_STEREO` sample rate on test recordings, and test Google Vertex AI / ElevenLabs API connectivity.
- [ ] **Step 2: Run spike probe**
  Run: `PYTHONPATH=. python3 tests/spike_freeswitch_audio.py`
  Expected: Prints FreeSWITCH ESL connection status, recording channel confirmation, and model credential status.
- [ ] **Step 3: Document spike results in `docs/virtual_qa_spike.md`**
  Record:
  1. Whisper isolation confirmation on `aleg` vs `bleg`.
  2. FreeSWITCH stereo channel mapping (Channel 0 = A-leg caller, Channel 1 = B-leg callee).
  3. Sample rate (e.g., 8000 Hz vs 16000 Hz) and data offset.
  4. Vertex AI model `gemini-3.5-flash` or fallback `gemini-2.5-flash` availability.
- [ ] **Step 4: Commit spike findings**
  ```bash
  git add docs/virtual_qa_spike.md tests/spike_freeswitch_audio.py
  git commit -m "docs: complete Virtual QA step 0 technical spike findings"
  ```

---

### Task 1: Database Migration, Models & Config Settings

**Files:**
- Create: `migrations/002_virtual_qa.sql`
- Modify: `PBX-backend/src/core/config.py:30-65`
- Modify: `PBX-backend/requirements.txt`
- Test: `tests/test_virtual_qa_schema_and_config.py`

**Interfaces:**
- Consumes: PostgreSQL schema from `migrations/001_initial_schema.sql`.
- Produces:
  - Database tables: `public.virtual_qa_configs`, `public.qa_events`.
  - Column: `extensions.is_virtual` (BOOLEAN DEFAULT false).
  - Role: `VIRTUAL_QA` in `public.roles`.
  - Config parameters: `ELEVENLABS_API_KEY`, `ELEVENLABS_STT_MODEL`, `ELEVENLABS_TTS_MODEL`, `ELEVENLABS_VOICE_ID`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GEMINI_QA_MODEL`, `QA_DEMO_MODE`, `QA_RECORDINGS_DIR`, `QA_POLL_INTERVAL_SEC`, `QA_WINDOW_SEC`, `QA_WINDOW_OVERLAP_SEC`, `QA_SILENCE_RMS`, `QA_MAX_CONCURRENT_AI`, `QA_LEADER_LOCK_TTL_SEC`, `QA_EVENT_RETENTION_DAYS`.

- [ ] **Step 1: Write failing test for config and migration models**
  Create `tests/test_virtual_qa_schema_and_config.py` validating that new settings exist on `settings`, `is_virtual` defaults to `False`, and SQL migration syntax parses cleanly.
- [ ] **Step 2: Run test to verify it fails**
  Run: `PYTHONPATH=. pytest tests/test_virtual_qa_schema_and_config.py -v`
  Expected: FAIL with `AttributeError: Settings object has no attribute 'ELEVENLABS_API_KEY'`.
- [ ] **Step 3: Update `requirements.txt` and `src/core/config.py`**
  Add dependencies: `google-genai>=1.0.0`, `google-auth`, `httpx`, `numpy`.
  Add Virtual QA configuration fields and defaults in `src/core/config.py`.
- [ ] **Step 4: Create `migrations/002_virtual_qa.sql`**
  Implement:
  - Unique constraint `uq_roles_name` on `public.roles`.
  - Insert `VIRTUAL_QA` role.
  - `ALTER TABLE public.extensions ADD COLUMN IF NOT EXISTS is_virtual BOOLEAN NOT NULL DEFAULT false;`.
  - Create table `public.virtual_qa_configs` with scope check, max warn check, cooldown check, and unique `(tenant_id, qa_extension_id)`.
  - Create table `public.qa_events` with indexes on `(tenant_id, created_at DESC)` and `(tenant_id, call_uuid)`.
- [ ] **Step 5: Run test to verify it passes**
  Run: `PYTHONPATH=. pytest tests/test_virtual_qa_schema_and_config.py -v`
  Expected: PASS.
- [ ] **Step 6: Commit**
  ```bash
  git add migrations/002_virtual_qa.sql src/core/config.py requirements.txt tests/test_virtual_qa_schema_and_config.py
  git commit -m "feat(qa): add virtual QA database migration, models, and config settings"
  ```

---

### Task 2: Multi-Worker WebSocket Pub/Sub Broadcaster

**Files:**
- Modify: `PBX-backend/src/core/websocket_manager.py:20-64`
- Modify: `PBX-backend/src/main.py:25-50`
- Test: `tests/test_qa_websocket_pubsub.py`

**Interfaces:**
- Consumes: Redis client from `src.core.redis.get_redis`, active connections map from `ConnectionManager`.
- Produces:
  - `ConnectionManager.broadcast_qa_event(tenant_id: str, payload: dict, roles=("supervisor", "admin"))`.
  - Redis Pub/Sub subscription on channel `qa_events:{tenant_id}` dispatching to local WebSockets.

- [ ] **Step 1: Write failing test for `broadcast_qa_event`**
  Create `tests/test_qa_websocket_pubsub.py` mocking Redis pub/sub and WebSocket clients to test:
  1. Publishing QA payload to Redis channel `qa_events:{tenant_id}`.
  2. Role-based filtering (agents do not receive `qa.warning` or `qa.escalation`; supervisors/admins do).
  3. Payload formatting (`version: 1`, `timestamp`, `call_uuid`, `event`).
- [ ] **Step 2: Run test to verify it fails**
  Run: `PYTHONPATH=. pytest tests/test_qa_websocket_pubsub.py -v`
  Expected: FAIL with `AttributeError: ConnectionManager object has no attribute 'broadcast_qa_event'`.
- [ ] **Step 3: Implement Redis Pub/Sub broadcasting in `src/core/websocket_manager.py`**
  1. Add `broadcast_qa_event(tenant_id: str, payload: dict, roles=("supervisor", "admin"))`.
  2. Add background subscriber task `listen_to_redis_qa_events()` that consumes `qa_events:*`, decodes JSON, and delivers to connected local websockets matching the recipient tenant and role.
  3. Add per-socket send timeout / dead-socket cleanup to prevent slow clients from blocking the broadcast.
- [ ] **Step 4: Hook subscriber startup and shutdown in `src/main.py` lifespan**
  Launch `asyncio.create_task(ws_manager.start_redis_listener())` on startup, cancel on shutdown.
- [ ] **Step 5: Run test to verify it passes**
  Run: `PYTHONPATH=. pytest tests/test_qa_websocket_pubsub.py -v`
  Expected: PASS.
- [ ] **Step 6: Commit**
  ```bash
  git add src/core/websocket_manager.py src/main.py tests/test_qa_websocket_pubsub.py
  git commit -m "feat(ws): implement multi-worker Redis Pub/Sub QA event broadcasting"
  ```

---

### Task 3: Directory and Dialplan Hooks for Monitored Calls & Supervisor Eavesdrop

**Files:**
- Modify: `PBX-backend/src/xml_curl/directory.py:65-155`
- Modify: `PBX-backend/src/xml_curl/dialplan.py:150-220`, `PBX-backend/src/xml_curl/dialplan.py:850-915`
- Test: `tests/test_qa_dialplan_and_directory.py`

**Interfaces:**
- Consumes: `is_virtual` from `extensions`, `virtual_qa_configs` from DB, Redis agent-to-call mappings.
- Produces:
  - Directory XML with `loopback/park` dial-string for virtual extensions and SIP auth rejection.
  - Dialplan XML with `RECORD_STEREO=true`, `enable_file_write_buffering=false`, `qa_enabled=true`, `qa_agent_leg`, and forced `{QA_RECORDINGS_DIR}/{tenant_id}/{a_leg_uuid}.wav` recording path.
  - Supervisor short dial code routing `*88<mode><agent_ext>` with tenant validation and FreeSWITCH `eavesdrop`.

- [ ] **Step 1: Write failing test for directory and dialplan hooks**
  Create `tests/test_qa_dialplan_and_directory.py` verifying:
  1. Directory returns `loopback/park` for virtual extension.
  2. Dialplan injects `RECORD_STEREO=true`, unbuffered recording, and QA variables using `application="set"` (not `export`).
  3. Dialplan handles `*880<agent_ext>`, `*881<agent_ext>`, `*882<agent_ext>`, `*883<agent_ext>` resolving the target call UUID from Redis.
- [ ] **Step 2: Run test to verify it fails**
  Run: `PYTHONPATH=. pytest tests/test_qa_dialplan_and_directory.py -v`
  Expected: FAIL with missing QA variables in generated XML.
- [ ] **Step 3: Update `src/xml_curl/directory.py`**
  Check `is_virtual` column:
  - If `is_virtual = True`: return `<param name="dial-string" value="loopback/park"/>` and do NOT return SIP password/auth elements.
- [ ] **Step 4: Update `src/xml_curl/dialplan.py`**
  1. Add park / virtual extension announcement handler for direct human dials to the QA extension.
  2. In extension-to-extension bridging: query active `virtual_qa_configs` for tenant. If caller or destination is monitored:
     - Set `qa_agent_leg = "aleg"` if caller is monitored, else `"bleg"`.
     - Inject `RECORD_STEREO=true`, `enable_file_write_buffering=false`, `qa_enabled=true`, `qa_config_id`, `qa_virtual_ext`, `qa_agent_leg`, `qa_agent_ext`, `qa_customer_ext`, `qa_max_retries`, `qa_cooldown`, `qa_supervisor_ext`.
     - Force recording path to `{QA_RECORDINGS_DIR}/{tenant_id}/${uuid}.wav`.
  3. Add condition for `^(\*88[0-3](\d{2,10}))$` resolving the target active call from Redis key `tenant:{tenant_id}:qa_agent_call:{agent_ext}`, verifying tenant ownership, setting `eavesdrop_enable_dtmf=true`, mode whisper variable, and bridging to `eavesdrop`.
- [ ] **Step 5: Run test to verify it passes**
  Run: `PYTHONPATH=. pytest tests/test_qa_dialplan_and_directory.py -v`
  Expected: PASS.
- [ ] **Step 6: Commit**
  ```bash
  git add src/xml_curl/directory.py src/xml_curl/dialplan.py tests/test_qa_dialplan_and_directory.py
  git commit -m "feat(dialplan): add virtual QA stereo recording hooks and supervisor eavesdrop codes"
  ```

---

### Task 4: Audio Tailing, AI Policy Engine, ESL Listener & QA Worker Service

**Files:**
- Create: `PBX-backend/src/services/virtual_qa_service.py`
- Test: `tests/test_virtual_qa_audio_and_ai.py`
- Test: `tests/test_virtual_qa_state_machine.py`

**Interfaces:**
- Consumes: FreeSWITCH recording WAV files, ElevenLabs API, Vertex AI Gemini API, Redis, ESL socket (8021).
- Produces:
  - Functions: `parse_wav_header`, `deinterleave_stereo_chunk`, `calculate_rms`, `local_policy_check`, `evaluate_with_gemini`, `transcribe_elevenlabs_stt`, `synthesize_elevenlabs_tts`, `trigger_qa_violation`.
  - Service: `VirtualQAService` running the persistent ESL subscriber loop, leader election lock, audio tailing loop, cooldown tracking, and audit logging into `qa_events`.

- [ ] **Step 1: Write failing test for audio tailing and local policy regex**
  Create `tests/test_virtual_qa_audio_and_ai.py` testing:
  1. Synthetic stereo WAV generation, frame alignment, and de-interleaving (Channel 0 vs Channel 1).
  2. Float64 RMS calculation (preventing int16 overflow).
  3. Strict word-boundary regex in `local_policy_check` ("hell" matches "what the hell", but does NOT match "hello", "shell", "Hellen").
- [ ] **Step 2: Run test to verify it fails**
  Run: `PYTHONPATH=. pytest tests/test_virtual_qa_audio_and_ai.py -v`
  Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.virtual_qa_service'`.
- [ ] **Step 3: Implement audio processing and local policy check**
  In `src/services/virtual_qa_service.py`:
  - `parse_wav_header(file_obj) -> dict`: reads sample rate, channels, bits per sample, and data offset.
  - `deinterleave_stereo_chunk(raw_bytes, leg) -> np.ndarray`: selects target leg samples.
  - `calculate_rms(samples: np.ndarray) -> float`: executes `np.sqrt(np.mean(samples.astype(np.float64) ** 2))`.
  - `local_policy_check(transcript, banned_keywords, rms, threshold) -> Optional[dict]`: executes case-insensitive `\b<kw>\b` regex matching and returns violation payload if matched.
- [ ] **Step 4: Implement AI evaluation and synthesis helpers**
  In `src/services/virtual_qa_service.py`:
  - `evaluate_with_gemini(...)`: uses `google-genai` with Vertex AI backend, temperature 0, prompt-injection shielding tags, and structured JSON output schema `{"violation": bool, "reason": str, "coaching": str}`.
  - `transcribe_elevenlabs_stt(...)`: submits in-memory WAV multipart/form-data to ElevenLabs Scribe STT API.
  - `synthesize_elevenlabs_tts(...)`: calls ElevenLabs TTS with pre-caching for standard warning templates ("Warning 1 of 3", etc.) and fallback to `warning_audio_path`.
- [ ] **Step 5: Write failing test for state machine, cooldown and escalation**
  Create `tests/test_virtual_qa_state_machine.py` testing:
  1. Cooldown prevents duplicate triggers within `cooldown_seconds`.
  2. Warnings 1 to `max_retries` trigger whisper actions with `uuid_broadcast`.
  3. Warning `max_retries + 1` triggers `qa.escalation` exactly once and suppresses further whispers.
- [ ] **Step 6: Implement `trigger_qa_violation` and ESL call monitor**
  In `src/services/virtual_qa_service.py`:
  - Atomic Redis lock on `tenant:{t}:qa_cd:{uuid}` for cooldown.
  - `HINCRBY tenant:{t}:qa_call:{uuid} warn_count 1`.
  - Format deterministic whisper text: `f"Warning {n} of {max_retries}. {coaching}"`.
  - If `n <= max_retries`: play whisper via ESL `uuid_broadcast {a_leg_uuid} {wav} {qa_agent_leg}` and record `qa_events` `warning` row.
  - If `n > max_retries`: atomic `HSETNX ... escalated 1`, record `qa_events` `escalation` row, and broadcast `qa.escalation`.
  - Async ESL event client for `CHANNEL_BRIDGE` and `CHANNEL_HANGUP` with periodic active call reconciliation.
- [ ] **Step 7: Run tests to verify they pass**
  Run: `PYTHONPATH=. pytest tests/test_virtual_qa_audio_and_ai.py tests/test_virtual_qa_state_machine.py -v`
  Expected: PASS.
- [ ] **Step 8: Commit**
  ```bash
  git add src/services/virtual_qa_service.py tests/test_virtual_qa_audio_and_ai.py tests/test_virtual_qa_state_machine.py
  git commit -m "feat(qa): implement audio tailing, AI policy engine, ESL listener, and violation state machine"
  ```

---

### Task 5: REST API for Virtual QA Configuration, Active Calls & Interventions

**Files:**
- Create: `PBX-backend/src/api/v1/qa.py`
- Modify: `PBX-backend/src/main.py:65-95`
- Test: `tests/test_qa_api.py`

**Interfaces:**
- Consumes: Database tables `virtual_qa_configs`, `qa_events`, `extensions`, Redis active call hashes.
- Produces:
  - `GET /api/v1/qa/configs`, `POST /api/v1/qa/configs`, `PUT /api/v1/qa/configs/{id}`, `DELETE /api/v1/qa/configs/{id}`.
  - `GET /api/v1/qa/active-calls`: active monitored calls for UI hydration.
  - `POST /api/v1/qa/calls/{call_uuid}/intervene?mode=listen|whisper|barge`: executes ESL `originate ... &eavesdrop({call_uuid})`.
  - `POST /api/v1/qa/calls/{call_uuid}/simulate-violation`: demo violation simulation gated by `QA_DEMO_MODE`.
  - `GET /api/v1/qa/events`: paginated audit history.

- [ ] **Step 1: Write failing test for QA REST endpoints**
  Create `tests/test_qa_api.py` with FastAPI test client verifying:
  1. Config CRUD enforces tenant scoping and supervisor/admin roles.
  2. `POST /configs` sets `extensions.is_virtual = true` and assigns `VIRTUAL_QA` role.
  3. `POST /intervene` checks tenant ownership and initiates ESL originate.
  4. `POST /simulate-violation` returns 404 when `QA_DEMO_MODE=false`.
- [ ] **Step 2: Run test to verify it fails**
  Run: `PYTHONPATH=. pytest tests/test_qa_api.py -v`
  Expected: FAIL with 404 or router not found.
- [ ] **Step 3: Implement `src/api/v1/qa.py` and register in `src/main.py`**
  1. Define Pydantic request/response schemas for configs and intervention.
  2. Implement config CRUD with validation: verified extension numbers, trimmed lowercased banned keywords, and pre-generation of cached whisper phrases.
  3. Implement `GET /active-calls` querying Redis `tenant:{t}:qa_active_calls` and returning metadata for UI hydration.
  4. Implement `POST /intervene`: verify call in Redis, assemble FreeSWITCH ESL command:
     `bgapi originate {eavesdrop_enable_dtmf=true,eavesdrop_whisper_{leg}=true,originate_timeout=25,ignore_early_media=true}user/{supervisor_ext}@{sip_domain} &eavesdrop({call_uuid})`, and log audit row in `qa_events`.
  5. Implement `POST /simulate-violation` checking `settings.QA_DEMO_MODE`.
  6. Register `app.include_router(qa.router, prefix=settings.API_V1_STR)` in `src/main.py`.
- [ ] **Step 4: Run test to verify it passes**
  Run: `PYTHONPATH=. pytest tests/test_qa_api.py -v`
  Expected: PASS.
- [ ] **Step 5: Commit**
  ```bash
  git add src/api/v1/qa.py src/main.py tests/test_qa_api.py
  git commit -m "feat(api): add Virtual QA configuration, active call monitoring, and intervention endpoints"
  ```

---

### Task 6: React Frontend: Virtual QA View, Live Call Table & Supervisor Escalation Banner

**Files:**
- Create: `PBX-frontend/src/components/VirtualQAView.tsx`
- Modify: `PBX-frontend/src/services/api.ts:200-260`
- Modify: `PBX-frontend/src/types.ts:150-210`
- Modify: `PBX-frontend/src/App.tsx:50-120`
- Modify: `PBX-frontend/src/components/DashboardView.tsx:100-140`

**Interfaces:**
- Consumes: REST endpoints `/api/v1/qa/*`, WebSocket events `qa.warning`, `qa.escalation`, `qa.call_ended`.
- Produces:
  - Navigation tab "Virtual QA" in UI for Admin and Supervisor roles.
  - Configuration management panel (extension selection, monitored agents, threshold sliders, keyword tags).
  - Live monitored calls table with warning badges (`1/3 Warnings`, `ESCALATED`) and trigger simulation button.
  - Supervisor escalation banner with Listen, Whisper, Barge buttons and short dial codes (`*88<mode><agent_ext>`).
  - Historical audit trail table from `GET /api/v1/qa/events`.

- [ ] **Step 1: Update TypeScript types and API service**
  In `PBX-frontend/src/types.ts`, declare `VirtualQAConfig`, `QAActiveCall`, `QAEvent`, and `QAEscalationPayload`.
  In `PBX-frontend/src/services/api.ts`, add methods: `getQAConfigs`, `createQAConfig`, `updateQAConfig`, `deleteQAConfig`, `getQAActiveCalls`, `interveneQACall`, `simulateQAViolation`, `getQAEvents`.
- [ ] **Step 2: Create `PBX-frontend/src/components/VirtualQAView.tsx`**
  Build:
  1. Header with enabled status badge and "Create Virtual QA Bot" button.
  2. Tab navigation: "Monitored Live Calls", "QA Bot Configurations", "Audit History".
  3. Live Calls Table: displays Active Call UUID, Agent, Customer, Warning Count badge, Latest Reason, and Action Buttons (Listen, Whisper, Barge, Simulate).
  4. Config Modal/Drawer: fields for QA Bot Extension, Monitored Agents (multi-select), Max Retries, Cooldown, Banned Keywords (tag pill editor), Voice ID, and Custom Prompt.
- [ ] **Step 3: Implement Supervisor Escalation Banner in `DashboardView.tsx` & `VirtualQAView.tsx`**
  1. Add WebSocket listener for `qa.escalation` and `qa.warning`.
  2. On `qa.escalation`: render animated high-priority red alert banner with Agent, Caller, Reason, Warning count, and direct action buttons (Listen `*880`, Whisper `*881`, Barge `*883`).
  3. Include "Acknowledge" button to dismiss banner and auto-clear banner on `qa.call_ended`.
  4. Hydrate pending escalations on initial load via `getQAActiveCalls()`.
- [ ] **Step 4: Register tab in `PBX-frontend/src/App.tsx`**
  Add `virtual_qa` view to navigation sidebar for `SUPER_ADMIN`, `TENANT_ADMIN`, and `SUPERVISOR`.
- [ ] **Step 5: Run frontend build check**
  Run: `npm --prefix PBX-frontend run build` (or `npm test`)
  Expected: Clean build without TypeScript or JSX compilation errors.
- [ ] **Step 6: Commit**
  ```bash
  git add PBX-frontend/src/types.ts PBX-frontend/src/services/api.ts PBX-frontend/src/components/VirtualQAView.tsx PBX-frontend/src/components/DashboardView.tsx PBX-frontend/src/App.tsx
  git commit -m "feat(ui): add Virtual QA management, live monitor table, and supervisor escalation banner"
  ```

---

### Task 7: End-to-End Integration Verification & Compliance Documentation

**Files:**
- Create: `tests/test_virtual_qa_e2e.py`
- Modify: `docs/virtual_qa.md` (Checklist confirmation)

**Interfaces:**
- Consumes: All components from Tasks 1-6.
- Produces: Passing end-to-end integration test verifying the 20-item checklist from `docs/virtual_qa.md`.

- [ ] **Step 1: Write E2E integration test**
  Create `tests/test_virtual_qa_e2e.py` testing complete flow:
  1. Create tenant, extension, and virtual QA config.
  2. Simulate bridge event for monitored pair.
  3. Stream simulated audio frames with banned keyword ("shut up").
  4. Assert warning 1 whisper broadcasted.
  5. Trigger subsequent violations until `warn_count > max_retries`.
  6. Assert escalation event emitted to WebSocket and whispers stopped.
  7. Test supervisor `intervene?mode=whisper` originate command.
  8. Simulate hangup event and verify cleanup and `qa_events` audit trail.
- [ ] **Step 2: Run E2E test**
  Run: `PYTHONPATH=. pytest tests/test_virtual_qa_e2e.py -v`
  Expected: PASS.
- [ ] **Step 3: Commit**
  ```bash
  git add tests/test_virtual_qa_e2e.py
  git commit -m "test(qa): add comprehensive end-to-end Virtual QA integration test"
  ```
