# Virtual QA Step 0 Technical Spike Findings

This document records the baseline verification and findings from the technical spike conducted in Step 0.

---

### 1. Whisper Isolation (`uuid_broadcast` vs `uuid_displace`)
- **Command:** `uuid_broadcast <a_leg_uuid> <path_to_wav> aleg` (or `bleg`).
- **Behavior:**
  - When `aleg` is passed, FreeSWITCH injects the media strictly into Channel A. Channel B continues receiving regular bridged audio with zero interruption.
  - When `bleg` is passed, media injects strictly into Channel B.
  - **Leg Mapping Rule:** Dialplan sets `qa_agent_leg="aleg"` if the caller is the monitored agent, or `"bleg"` if the callee is the monitored agent. The whisper broadcast always targets `uuid_broadcast {a_leg_uuid} {wav} {qa_agent_leg}`.

---

### 2. Unbuffered Stereo Recording & Channel Mapping
- **Channel Variables in Dialplan:**
  ```xml
  <action application="set" data="RECORD_STEREO=true"/>
  <action application="set" data="enable_file_write_buffering=false"/>
  ```
  *Note:* Must use `set` (not `export`) so variables remain bound to the canonical A-leg channel and avoid duplicate B-leg tagging.
- **Stereo Channel Interleaving:**
  - **Channel 0 (Left):** A-leg (Caller).
  - **Channel 1 (Right):** B-leg (Callee).
  - De-interleave formula:
    ```python
    samples = np.frombuffer(raw_pcm_bytes, dtype=np.int16)
    agent_samples = samples[0::2] if qa_agent_leg == "aleg" else samples[1::2]
    ```
- **Frame Alignment & Header Parsing:**
  - 16-bit stereo frame = 4 bytes (2 bytes per channel).
  - Audio tailing reads `(len(new_bytes) // 4) * 4` bytes, holding back unaligned remainder bytes for the next poll cycle.
  - Live WAV files have unfinalized length fields in the header; the reader parses the `data` chunk header offset (typically byte 44) and treats all subsequent appended bytes as valid PCM frames up to EOF.

---

### 3. Audio RMS Energy Calculation
- **Calculation Formula:**
  ```python
  rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
  ```
  *Critical fix:* Squaring `np.int16` directly causes integer overflow for amplitudes $> 181$; casting to `np.float64` is required for numerical stability.

---

### 4. AI Provider Models & Fallback Architecture
- **Vertex AI Gemini:**
  - Target model: `gemini-3.5-flash` (or regional `gemini-2.5-flash`).
  - Auth: Google Service Account credentials via `GOOGLE_APPLICATION_CREDENTIALS`.
  - Structured output: `{"violation": bool, "reason": str, "coaching": str}` with temperature 0.
- **ElevenLabs STT & TTS:**
  - STT: `scribe_v1` via multipart WAV upload.
  - TTS: `eleven_flash_v2_5` (voice ID: `JBFqnCBsd6RMkjVDRZzb`).
- **Fallback Chain:**
  1. Local regex policy check (`local_policy_check`) executes first (zero latency, zero API cost).
  2. Fixed phrases ("Warning 1 of 3", etc.) are pre-generated and cached on disk.
  3. If ElevenLabs API is unavailable, fallback to `warning_audio_path` (`tone_stream://%(400,200,800,1000);loops=2`).
  4. If Vertex AI is unavailable, `local_policy_check` decision governs.
  5. In `QA_DEMO_MODE=true`, `simulate-violation` supports `skip_ai=true` for 100% deterministic testing.

---

### 5. Verification Checklist Status
- [x] Whisper isolation verified.
- [x] Unbuffered stereo channel extraction verified.
- [x] Float64 RMS calculation verified.
- [x] Header parsing & live frame tailing verified.
