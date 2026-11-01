#!/usr/bin/env python3
"""
=============================================================================
Virtual QA Interactive Live Experience & Demo
=============================================================================
Demonstrates real-time telephony AI compliance monitoring:
1. Live transcript policy evaluation via Google Gemini (gemini-2.5-flash-lite)
2. Live coaching whisper generation via ElevenLabs TTS (Flash v2.5)
3. Physical audio playback through macOS speakers (via afplay)
4. Redis atomic warning state & cooldown counter
5. Supervisor escalation dispatch & intervention dial codes (*88[0-2]<ext>)
=============================================================================
"""

import os
import sys
import time
import json
import asyncio
import subprocess
import uuid

# Ensure backend root is in python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.config import settings
from src.core.redis import init_redis
from src.services.virtual_qa_service import (
    local_policy_check,
    evaluate_with_gemini,
    synthesize_elevenlabs_tts,
    trigger_qa_violation
)

def play_audio_macos(wav_path: str):
    """Plays audio through macOS speakers using native afplay."""
    if os.path.exists(wav_path) and sys.platform == "darwin":
        try:
            print(f"🔊 Playing coaching whisper audio through your speakers ({os.path.basename(wav_path)})...")
            subprocess.run(["afplay", wav_path], check=False)
        except Exception as e:
            print(f"⚠️ Could not play audio via afplay: {e}")

async def process_utterance(
    agent_utterance: str,
    agent_ext: str = "1001",
    customer_ext: str = "2001",
    call_uuid: str = None,
    tenant_id: str = None,
    policy: str = "Agents must always remain courteous, empathetic, and professional. Never use profanity, insults, or threaten customers."
):
    await init_redis()

    if not call_uuid:
        call_uuid = f"demo-call-{uuid.uuid4().hex[:8]}"

    # Auto-resolve actual tenant UUID so it appears on the live PBX platform
    if not tenant_id or tenant_id == "tenant-demo-01":
        try:
            from src.core.database import execute_query_one
            t_row = await execute_query_one("SELECT id FROM tenants WHERE domain = 'pbx.terrixai.com' OR name = 'aikyamlabs' LIMIT 1")
            if not t_row:
                t_row = await execute_query_one("SELECT id FROM tenants LIMIT 1")
            tenant_id = str(t_row["id"]) if t_row else "9c61b161-db6f-4825-b7db-cb76d88155c5"
        except Exception:
            tenant_id = "9c61b161-db6f-4825-b7db-cb76d88155c5"

    # Register active call in Redis so it appears live on the PBX platform
    from src.core.redis import get_redis
    r = await get_redis()
    if r:
        try:
            await r.hset(
                f"tenant:{tenant_id}:qa_call:{call_uuid}",
                mapping={
                    "call_uuid": call_uuid,
                    "tenant_id": tenant_id,
                    "agent_ext": agent_ext,
                    "customer_ext": customer_ext,
                    "agent_leg": "bleg",
                    "warn_count": 0,
                    "escalated": 0,
                    "started_at": str(time.time()),
                }
            )
            await r.expire(f"tenant:{tenant_id}:qa_call:{call_uuid}", 3600)
        except Exception:
            pass

    call_info = {
        "call_uuid": call_uuid,
        "tenant_id": tenant_id,
        "agent_extension": agent_ext,
        "qa_agent_leg": "bleg",
        "customer_number": customer_ext,
        "max_warn_retries": 1,
        "cooldown_seconds": 1,
        "config": {
            "whisper_enabled": True,
            "max_warn_retries": 1,
            "warning_cooldown_sec": 1,
            "banned_keywords": ["idiot", "shut up", "hell", "damn", "scam", "sue"]
        }
    }

    print("\n" + "=" * 70)
    print(f"📞 CALL SIMULATION: Agent {agent_ext} ↔ Customer {customer_ext}")
    print(f"🗣️  AGENT SPOKE: \"{agent_utterance}\"")
    print("-" * 70)

    # 1. Local keyword policy check (instant regex with word boundaries)
    local_hit = local_policy_check(agent_utterance, call_info["config"]["banned_keywords"])
    if local_hit:
        print(f"⚡ FAST LOCAL MATCH: Banned keyword detected -> '{local_hit['keyword']}'")

    # 2. Semantic evaluation via Google Gemini
    print("🤖 Evaluating compliance with Google Gemini (gemini-2.5-flash-lite)...")
    start_time = time.time()
    gemini_res = await evaluate_with_gemini(agent_utterance, policy)
    duration = time.time() - start_time
    print(f"⏱️  Gemini response in {duration:.2f}s:")
    print(f"    • Violation: {gemini_res['violation']}")
    print(f"    • Reason:    {gemini_res['reason']}")
    if gemini_res.get("coaching"):
        print(f"    • Coaching:  {gemini_res['coaching']}")

    violation = local_hit["violation"] if local_hit else gemini_res["violation"]
    reason = local_hit["reason"] if local_hit else gemini_res["reason"]
    coaching = gemini_res.get("coaching") or "Please maintain courteous and professional standards."

    if not violation:
        print("\n✅ COMPLIANT: Utterance complies with QA policy. No whisper dispatched.")
        return

    # 3. Dynamic Whisper Audio Generation via ElevenLabs TTS
    print("\n🎙️  Generating dynamic whisper coaching audio with ElevenLabs Flash v2.5...")
    whisper_text = coaching
    tts_start = time.time()
    wav_path = await synthesize_elevenlabs_tts(whisper_text)
    tts_duration = time.time() - tts_start
    print(f"⏱️  TTS generated in {tts_duration:.2f}s: {wav_path}")

    # Play the whisper audio to user
    play_audio_macos(wav_path)

    # 4. State Machine: Cooldown, Warning Count & FreeSWITCH Isolation
    violation_data = {
        "violation": True,
        "reason": reason,
        "coaching": coaching,
        "transcript": agent_utterance
    }
    result = await trigger_qa_violation(call_info, violation_data)
    print("\n📊 QA STATE MACHINE RESULT:")
    print(f"    • Action:           {result.get('action')}")
    print(f"    • Warning Count:    {result.get('warn_count')}")
    print(f"    • Escalated:        {result.get('escalated')}")

    if result.get("escalated"):
        print("\n🚨🚨 SUPERVISOR ESCALATION TRIGGERED! 🚨🚨")
        print("    A high-priority alert was broadcast via WebSocket (qa_events:{tenant_id}).")
        print("    Supervisors can intervene instantly from the dashboard or via dial codes:")
        print(f"      • *880{agent_ext} -> SPY    (Listen silently to both legs)")
        print(f"      • *881{agent_ext} -> WHISPER (Speak exclusively into agent ear)")
        print(f"      • *882{agent_ext} -> BARGE   (Three-way bridge into active call)")

async def run_scenario_demo():
    print("""
╔══════════════════════════════════════════════════════════════════════╗
║              VIRTUAL QA LIVE EXPERIENCE DEMO                         ║
║      Real-Time Telephony Whisper & Supervisor Escalation             ║
╚══════════════════════════════════════════════════════════════════════╝
    """)
    demo_call_uuid = f"demo-session-{uuid.uuid4().hex[:6]}"

    print("\n▶️  SCENARIO 1: Polite Agent Interaction (No Violation)")
    await process_utterance(
        agent_utterance="Good morning! Thank you for contacting customer support. My name is Alex, how may I help you today?",
        call_uuid=demo_call_uuid
    )
    time.sleep(2)

    print("\n\n▶️  SCENARIO 2: First Policy Violation (Whisper Injected to Agent Ear)")
    await process_utterance(
        agent_utterance="Look, you stupid idiot, didn't I tell you that your payment failed?",
        call_uuid=demo_call_uuid
    )
    time.sleep(4)

    print("\n\n▶️  SCENARIO 3: Repeated Policy Violation (Supervisor Escalation)")
    await process_utterance(
        agent_utterance="Shut up! I don't care, go ahead and sue us if you want!",
        call_uuid=demo_call_uuid
    )

async def run_interactive_mode():
    print("""
╔══════════════════════════════════════════════════════════════════════╗
║              INTERACTIVE VIRTUAL QA CONSOLE                          ║
║    Type any spoken utterance to test live AI policy evaluation,      ║
║    dynamic ElevenLabs voice coaching, and supervisor escalation.     ║
╚══════════════════════════════════════════════════════════════════════╝
Type 'exit' or 'quit' to end.
    """)
    call_uuid = f"interactive-{uuid.uuid4().hex[:6]}"
    while True:
        try:
            user_input = input("\n🎙️  Enter agent utterance (or 'exit'): ").strip()
            if not user_input or user_input.lower() in ("exit", "quit"):
                print("Exiting Virtual QA Demo. Goodbye!")
                break
            await process_utterance(user_input, call_uuid=call_uuid)
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        asyncio.run(run_interactive_mode())
    else:
        print("Choose Demo Mode:")
        print("  1. Run automated 3-scenario walkthrough (Polite -> Warning Whisper -> Escalation)")
        print("  2. Interactive live console (Type whatever you want to test)")
        choice = input("\nEnter choice [1 or 2] (default 1): ").strip()
        if choice == "2":
            asyncio.run(run_interactive_mode())
        else:
            asyncio.run(run_scenario_demo())
