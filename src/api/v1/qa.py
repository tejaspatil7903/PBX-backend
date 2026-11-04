import json
import time
import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from pydantic import BaseModel, Field

from src.core.config import settings
from src.core.redis import get_redis
from src.core.database import execute_query, execute_query_one
from src.core.permissions import get_current_user, CurrentUser, require_roles, require_module
from src.services.virtual_qa_service import qa_service, trigger_qa_violation, synthesize_elevenlabs_tts

logger = logging.getLogger("pbx.api.v1.qa")

router = APIRouter(prefix="/qa", tags=["Virtual QA & Monitoring"])


# =========================================================================
# SCHEMAS
# =========================================================================

class VirtualQAConfigCreate(BaseModel):
    tenant_id: Optional[str] = None
    qa_extension_number: str = Field(..., description="Extension number to assign as headless virtual QA bot")
    max_warn_retries: int = Field(3, ge=1, le=10)
    cooldown_seconds: int = Field(10, ge=3, le=120)
    supervisor_extension: Optional[str] = None
    monitored_agents: List[str] = Field(default_factory=list)
    monitored_callers: List[str] = Field(default_factory=lambda: ["*"])
    policy_prompt: Optional[str] = None
    banned_keywords: List[str] = Field(default_factory=list)
    enabled: bool = True


class VirtualQAConfigUpdate(BaseModel):
    max_warn_retries: Optional[int] = Field(None, ge=1, le=10)
    cooldown_seconds: Optional[int] = Field(None, ge=3, le=120)
    supervisor_extension: Optional[str] = None
    monitored_agents: Optional[List[str]] = None
    monitored_callers: Optional[List[str]] = None
    policy_prompt: Optional[str] = None
    banned_keywords: Optional[List[str]] = None
    enabled: Optional[bool] = None


class InterveneRequest(BaseModel):
    mode: str = Field("whisper", pattern="^(listen|whisper|barge)$")
    supervisor_extension: Optional[str] = None


class StartDemoCallRequest(BaseModel):
    tenant_id: Optional[str] = None
    agent_extension: str = "1001"
    customer_extension: str = "2001"


class SimulateSpeechRequest(BaseModel):
    utterance: Optional[str] = None


# =========================================================================
# ENDPOINTS
# =========================================================================

@router.get("/configs")
async def list_qa_configs(
    current_user: CurrentUser = Depends(require_module("virtual-qa", allowed_roles=["SUPER_ADMIN", "TENANT_ADMIN", "SUB_ADMIN", "SUPERVISOR"])),
    tenant_id: Optional[str] = None
):
    """Lists Virtual QA configurations for the authenticated tenant."""
    tid = tenant_id if current_user.is_super_admin and tenant_id else current_user.tenant_id
    if tid:
        configs = await execute_query(
            """
            SELECT c.*, e.extension_number as qa_extension_number, e.display_name as qa_extension_name,
                   t.name as tenant_name, t.domain as tenant_domain
            FROM virtual_qa_configs c
            JOIN extensions e ON c.qa_extension_id = e.id
            LEFT JOIN tenants t ON c.tenant_id = t.id
            WHERE c.tenant_id = CAST(:tid AS uuid) AND c.deleted_at IS NULL
            ORDER BY c.created_at DESC
            """,
            {"tid": tid}
        )
    elif current_user.is_super_admin:
        configs = await execute_query(
            """
            SELECT c.*, e.extension_number as qa_extension_number, e.display_name as qa_extension_name,
                   t.name as tenant_name, t.domain as tenant_domain
            FROM virtual_qa_configs c
            JOIN extensions e ON c.qa_extension_id = e.id
            LEFT JOIN tenants t ON c.tenant_id = t.id
            WHERE c.deleted_at IS NULL
            ORDER BY c.created_at DESC
            """,
            {}
        )
    else:
        raise HTTPException(status_code=400, detail="Tenant context required")

    return configs


@router.post("/configs")
async def create_qa_config(
    payload: VirtualQAConfigCreate,
    current_user: CurrentUser = Depends(require_module("virtual-qa", allowed_roles=["SUPER_ADMIN", "TENANT_ADMIN", "SUB_ADMIN"]))
):
    """
    Creates a new Virtual QA policy and provisions the extension as a headless virtual agent.
    """
    tid = payload.tenant_id if current_user.is_super_admin and payload.tenant_id else current_user.tenant_id
    if not tid:
        raise HTTPException(status_code=400, detail="Tenant context required")

    ext = await execute_query_one(
        """
        SELECT id, extension_number, is_virtual FROM extensions
        WHERE tenant_id = CAST(:tid AS uuid) AND extension_number = :ext_num AND deleted_at IS NULL
        LIMIT 1
        """,
        {"tid": tid, "ext_num": payload.qa_extension_number}
    )
    if not ext:
        # Auto-create headless virtual extension for this tenant
        ext = await execute_query_one(
            """
            INSERT INTO extensions (
                tenant_id, extension_number, display_name, sip_password, voicemail_pin,
                is_virtual, webrtc_enabled, enabled
            ) VALUES (
                CAST(:tid AS uuid), :ext_num, :name, :sip_pwd, '1234',
                true, false, true
            )
            RETURNING id, extension_number, is_virtual
            """,
            {
                "tid": tid,
                "ext_num": payload.qa_extension_number,
                "name": f"Virtual QA Bot ({payload.qa_extension_number})",
                "sip_pwd": "virtual-qa-disabled-pwd",
            }
        )
    else:
        # 2. Mark extension is_virtual = true and suppress registration
        await execute_query(
            """
            UPDATE extensions SET is_virtual = true, updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
            """,
            {"id": str(ext["id"])}
        )

    ext_id = str(ext["id"])

    # 3. Clean banned keywords
    cleaned_keywords = [kw.strip().lower() for kw in payload.banned_keywords if kw.strip()]

    # 4. Pre-cache standard warning templates
    for i in range(1, payload.max_warn_retries + 1):
        sample_whisper = f"Warning {i} of {payload.max_warn_retries}. Please maintain professional standards."
        try:
            await synthesize_elevenlabs_tts(sample_whisper, tenant_id=tid)
        except Exception:
            pass

    sup_ext = payload.supervisor_extension.strip() if payload.supervisor_extension and payload.supervisor_extension.strip() else "9001"
    policy_text = payload.policy_prompt.strip() if payload.policy_prompt and payload.policy_prompt.strip() else "Agent must be courteous, empathetic, and professional at all times."

    # 5. Insert configuration row
    new_cfg = await execute_query_one(
        """
        INSERT INTO virtual_qa_configs (
            tenant_id, qa_extension_id, name, max_warn_retries, cooldown_seconds,
            supervisor_extension, monitored_agents, monitored_callers,
            policy_prompt, qa_policy_prompt, banned_keywords, enabled
        ) VALUES (
            CAST(:tid AS uuid), CAST(:ext_id AS uuid), :name, :retries, :cooldown,
            :sup_ext, CAST(:agents AS jsonb), CAST(:callers AS jsonb),
            :policy, :policy, CAST(:keywords AS jsonb), :enabled
        )
        RETURNING *
        """,
        {
            "tid": tid,
            "ext_id": ext_id,
            "name": f"Virtual QA Bot ({payload.qa_extension_number})",
            "retries": payload.max_warn_retries,
            "cooldown": payload.cooldown_seconds,
            "sup_ext": sup_ext,
            "agents": json.dumps(payload.monitored_agents),
            "callers": json.dumps(payload.monitored_callers),
            "policy": policy_text,
            "keywords": json.dumps(cleaned_keywords),
            "enabled": payload.enabled
        }
    )
    return new_cfg


@router.put("/configs/{config_id}")
async def update_qa_config(
    config_id: str,
    payload: VirtualQAConfigUpdate,
    current_user: CurrentUser = Depends(require_module("virtual-qa", allowed_roles=["SUPER_ADMIN", "TENANT_ADMIN", "SUB_ADMIN"]))
):
    """Updates an existing Virtual QA configuration."""
    tid = current_user.tenant_id
    existing = await execute_query_one(
        "SELECT * FROM virtual_qa_configs WHERE id = CAST(:id AS uuid) AND deleted_at IS NULL",
        {"id": config_id}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Configuration not found")
    if not current_user.is_super_admin and str(existing["tenant_id"]) != tid:
        raise HTTPException(status_code=403, detail="Forbidden")

    updates = []
    params = {"id": config_id}

    if payload.max_warn_retries is not None:
        updates.append("max_warn_retries = :retries")
        params["retries"] = payload.max_warn_retries
    if payload.cooldown_seconds is not None:
        updates.append("cooldown_seconds = :cooldown")
        params["cooldown"] = payload.cooldown_seconds
    if payload.supervisor_extension is not None:
        updates.append("supervisor_extension = :sup_ext")
        params["sup_ext"] = payload.supervisor_extension
    if payload.monitored_agents is not None:
        updates.append("monitored_agents = CAST(:agents AS jsonb)")
        params["agents"] = json.dumps(payload.monitored_agents)
    if payload.monitored_callers is not None:
        updates.append("monitored_callers = CAST(:callers AS jsonb)")
        params["callers"] = json.dumps(payload.monitored_callers)
    if payload.policy_prompt is not None:
        updates.append("policy_prompt = :policy")
        updates.append("qa_policy_prompt = :policy")
        params["policy"] = payload.policy_prompt
    if payload.banned_keywords is not None:
        cleaned = [k.strip().lower() for k in payload.banned_keywords if k.strip()]
        updates.append("banned_keywords = CAST(:keywords AS jsonb)")
        params["keywords"] = json.dumps(cleaned)
    if payload.enabled is not None:
        updates.append("enabled = :enabled")
        params["enabled"] = payload.enabled

    if not updates:
        return existing

    updates.append("updated_at = NOW()")
    sql = f"UPDATE virtual_qa_configs SET {', '.join(updates)} WHERE id = CAST(:id AS uuid) RETURNING *"
    res = await execute_query_one(sql, params)
    return res


@router.delete("/configs/{config_id}")
async def delete_qa_config(
    config_id: str,
    current_user: CurrentUser = Depends(require_module("virtual-qa", allowed_roles=["SUPER_ADMIN", "TENANT_ADMIN", "SUB_ADMIN"]))
):
    """Soft deletes a Virtual QA configuration."""
    tid = current_user.tenant_id
    existing = await execute_query_one(
        "SELECT tenant_id FROM virtual_qa_configs WHERE id = CAST(:id AS uuid) AND deleted_at IS NULL",
        {"id": config_id}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Configuration not found")
    if not current_user.is_super_admin and str(existing["tenant_id"]) != tid:
        raise HTTPException(status_code=403, detail="Forbidden")

    await execute_query(
        "UPDATE virtual_qa_configs SET deleted_at = NOW(), enabled = false WHERE id = CAST(:id AS uuid)",
        {"id": config_id}
    )
    return {"status": "deleted", "id": config_id}


@router.get("/active-calls")
async def get_active_monitored_calls(
    tenant_id: Optional[str] = None,
    current_user: CurrentUser = Depends(require_module("virtual-qa", allowed_roles=["SUPER_ADMIN", "TENANT_ADMIN", "SUB_ADMIN", "SUPERVISOR"]))
):
    """
    Returns active calls currently monitored by Virtual QA for dashboard hydration.
    """
    tid = tenant_id if current_user.is_super_admin and tenant_id else current_user.tenant_id

    r_obj = get_redis()
    redis_client = await r_obj if hasattr(r_obj, "__await__") else r_obj
    if not redis_client:
        return []

    if tid:
        pattern = f"tenant:{tid}:qa_call:*"
    elif current_user.is_super_admin:
        pattern = "tenant:*:qa_call:*"
    else:
        return []

    keys = await redis_client.keys(pattern)
    active_calls = []

    for k in keys:
        if isinstance(k, bytes):
            k = k.decode("utf-8")
        data = await redis_client.hgetall(k)
        if data:
            active_calls.append({
                "call_uuid": data.get("call_uuid") or k.split(":")[-1],
                "tenant_id": data.get("tenant_id") or "",
                "agent_extension": data.get("agent_ext") or "1001",
                "customer_extension": data.get("customer_ext") or "2001",
                "agent_leg": data.get("agent_leg") or "bleg",
                "warn_count": int(data.get("warn_count", 0)),
                "escalated": bool(int(data.get("escalated", 0))),
                "started_at": float(data.get("started_at", 0))
            })

    return active_calls


@router.post("/calls/{call_uuid}/intervene")
async def supervisor_intervene(
    call_uuid: str,
    mode: str = Query("whisper", pattern="^(listen|whisper|barge)$"),
    current_user: CurrentUser = Depends(require_module("virtual-qa", allowed_roles=["SUPER_ADMIN", "TENANT_ADMIN", "SUB_ADMIN", "SUPERVISOR"]))
):
    """
    Initiates live supervisor intervention (listen, whisper, barge) via FreeSWITCH eavesdrop originate.
    """
    tid = current_user.tenant_id
    r_obj = get_redis()
    redis_client = await r_obj if hasattr(r_obj, "__await__") else r_obj
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    if not tid and current_user.is_super_admin:
        keys = await redis_client.keys(f"tenant:*:qa_call:{call_uuid}")
        if keys:
            first_key = keys[0].decode("utf-8") if isinstance(keys[0], bytes) else keys[0]
            tid = first_key.split(":")[1]

    call_data = await redis_client.hgetall(f"tenant:{tid}:qa_call:{call_uuid}")
    if not call_data:
        raise HTTPException(status_code=404, detail="Active call not found")

    agent_leg = str(call_data.get("agent_leg", "bleg")).lower()

    # Look up supervisor extension & tenant sip_domain
    tenant_row = await execute_query_one(
        "SELECT sip_domain FROM tenants WHERE id = CAST(:tid AS uuid)",
        {"tid": tid}
    )
    sip_domain = tenant_row.get("sip_domain") if tenant_row else "pbx.terrixai.com"

    # Look up supervisor extension
    sup_ext = None
    user_ext = await execute_query_one(
        "SELECT extension_number FROM extensions WHERE tenant_id = CAST(:tid AS uuid) AND email = :email AND enabled = true AND deleted_at IS NULL LIMIT 1",
        {"tid": tid, "email": current_user.email}
    )
    if user_ext and user_ext.get("extension_number"):
        sup_ext = user_ext["extension_number"]

    if not sup_ext:
        sup_ext_row = await execute_query_one(
            """
            SELECT extension_number FROM extensions 
            WHERE tenant_id = CAST(:tid AS uuid) 
              AND enabled = true AND deleted_at IS NULL
              AND (display_name ILIKE '%supervisor%' OR display_name ILIKE '%qa%' OR extension_number IN ('1004', '9001'))
            ORDER BY (display_name ILIKE '%supervisor%') DESC
            LIMIT 1
            """,
            {"tid": tid}
        )
        if sup_ext_row and sup_ext_row.get("extension_number"):
            sup_ext = sup_ext_row["extension_number"]

    if not sup_ext:
        sup_ext = "1004"

    # Build originate eavesdrop command
    flags = [
        "eavesdrop_enable_dtmf=true",
        "originate_timeout=25",
        "ignore_early_media=true",
        "read_resample_rate=8000",
        "write_resample_rate=48000",
        "eavesdrop_buffer=1000",
    ]
    if mode == "whisper":
        if agent_leg in ("aleg", "0", "left"):
            flags.append("eavesdrop_whisper_aleg=true")
        else:
            flags.append("eavesdrop_whisper_bleg=true")
    elif mode == "listen":
        flags.append("eavesdrop_hide=true")
    elif mode == "barge":
        flags.append("eavesdrop_whisper_aleg=true")
        flags.append("eavesdrop_whisper_bleg=true")

    flags_str = ",".join(flags)
    cmd = f"bgapi originate {{{flags_str}}}user/{sup_ext}@{sip_domain} &eavesdrop({call_uuid})"
    logger.info(f"Supervisor {current_user.email} initiating {mode} intervention: {cmd}")

    try:
        await qa_service.api(cmd)
    except Exception as err:
        logger.warning(f"ESL originate dispatch warning: {err}")

    # Audit row
    try:
        import uuid as _uuid_lib
        safe_tid = None
        try:
            safe_tid = str(_uuid_lib.UUID(str(tid)))
        except Exception:
            pass

        if safe_tid:
            await execute_query(
                """
                INSERT INTO qa_events (
                    tenant_id, call_uuid, event_type, agent_extension,
                    coaching_message, details
                ) VALUES (
                    CAST(:tid AS uuid), :uuid, 'intervention', :agent_ext,
                    :msg, CAST(:details AS jsonb)
                )
                """,
                {
                    "tid": safe_tid,
                    "uuid": call_uuid,
                    "agent_ext": call_data.get("agent_ext", ""),
                    "msg": f"Supervisor intervention mode='{mode}'",
                    "details": json.dumps({"mode": mode, "supervisor_ext": sup_ext, "user_id": current_user.user_id})
                }
            )
    except Exception as db_err:
        logger.error(f"Failed to record intervention event: {db_err}")

    return {
        "status": "intervention_initiated",
        "call_uuid": call_uuid,
        "mode": mode,
        "supervisor_extension": sup_ext
    }


@router.get("/webrtc-credentials")
async def get_webrtc_credentials(
    request: Request,
    current_user: CurrentUser = Depends(require_module("virtual-qa", allowed_roles=["SUPER_ADMIN", "TENANT_ADMIN", "SUB_ADMIN", "SUPERVISOR"]))
):
    """
    Returns ephemeral WebRTC SIP credentials for in-browser audio listening and barging.
    """
    tid = current_user.tenant_id
    if not tid and current_user.is_super_admin:
        t_row = await execute_query_one("SELECT id FROM tenants WHERE enabled = true AND deleted_at IS NULL ORDER BY created_at ASC LIMIT 1")
        if t_row:
            tid = str(t_row["id"])

    if not tid:
        raise HTTPException(status_code=400, detail="Tenant context required")

    tenant_row = await execute_query_one(
        "SELECT id, name, sip_domain FROM tenants WHERE id = CAST(:tid AS uuid)",
        {"tid": tid}
    )
    sip_domain = tenant_row.get("sip_domain") if tenant_row else "pbx.aikyamlabs.local"

    # Find supervisor extension and SIP password
    ext_row = await execute_query_one(
        """
        SELECT extension_number, sip_password, display_name 
        FROM extensions 
        WHERE tenant_id = CAST(:tid AS uuid) 
          AND email = :email AND enabled = true AND deleted_at IS NULL 
        LIMIT 1
        """,
        {"tid": tid, "email": current_user.email}
    )

    if not ext_row:
        ext_row = await execute_query_one(
            """
            SELECT extension_number, sip_password, display_name 
            FROM extensions 
            WHERE tenant_id = CAST(:tid AS uuid) 
              AND enabled = true AND deleted_at IS NULL 
              AND (display_name ILIKE '%supervisor%' OR display_name ILIKE '%qa%' OR extension_number IN ('1004', '9001', '1003'))
            ORDER BY (display_name ILIKE '%supervisor%') DESC
            LIMIT 1
            """,
            {"tid": tid}
        )

    if not ext_row:
        raise HTTPException(status_code=404, detail="No supervisor extension found in tenant for WebRTC audio")

    host_ip = request.headers.get("x-forwarded-host") or request.headers.get("host") or "35.244.8.204"
    if ":" in host_ip:
        host_ip = host_ip.split(":")[0]

    # If host_ip is loopback / internal docker host (from reverse proxy changeOrigin), fallback to public IP
    if host_ip in ("127.0.0.1", "localhost", "0.0.0.0", "api", "nginx"):
        host_ip = "35.244.8.204"

    # FreeSWITCH WS is on port 5066; WSS is on port 7443
    ws_uri = f"ws://{host_ip}:5066"

    return {
        "status": "success",
        "ws_server": ws_uri,
        "sip_domain": sip_domain,
        "extension": ext_row["extension_number"],
        "password": ext_row["sip_password"],
        "display_name": ext_row.get("display_name") or "In-Browser Supervisor",
        "ice_servers": [
            {"urls": "stun:stun.l.google.com:19302"},
            {"urls": "stun:stun1.l.google.com:19302"}
        ]
    }


@router.post("/calls/start-demo")
async def start_demo_call(
    payload: Optional[StartDemoCallRequest] = None,
    current_user: CurrentUser = Depends(require_module("virtual-qa", allowed_roles=["SUPER_ADMIN", "TENANT_ADMIN", "SUB_ADMIN", "SUPERVISOR"]))
):
    """
    Creates an active monitored call in Redis so users can experience live QA monitoring directly from the platform UI.
    """
    req = payload or StartDemoCallRequest()
    tid = req.tenant_id if current_user.is_super_admin and req.tenant_id else current_user.tenant_id
    if not tid:
        t_row = await execute_query_one("SELECT id FROM tenants LIMIT 1")
        tid = str(t_row["id"]) if t_row else None
    if not tid:
        raise HTTPException(status_code=400, detail="Tenant context required")

    r_obj = get_redis()
    redis_client = await r_obj if hasattr(r_obj, "__await__") else r_obj
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    import uuid as _uuid
    call_uuid = f"demo-{_uuid.uuid4().hex[:8]}"

    # Look up active QA config for tenant
    cfg = await execute_query_one(
        "SELECT id FROM virtual_qa_configs WHERE tenant_id = CAST(:tid AS uuid) AND enabled = true AND deleted_at IS NULL LIMIT 1",
        {"tid": tid}
    )
    config_id = str(cfg["id"]) if cfg else ""

    now_ts = time.time()
    call_data = {
        "call_uuid": call_uuid,
        "tenant_id": tid,
        "agent_ext": req.agent_extension,
        "customer_ext": req.customer_extension,
        "agent_leg": "bleg",
        "warn_count": "0",
        "escalated": "0",
        "config_id": config_id,
        "started_at": str(now_ts),
    }

    call_key = f"tenant:{tid}:qa_call:{call_uuid}"
    await redis_client.hset(call_key, mapping=call_data)
    await redis_client.expire(call_key, 3600)

    from src.core.websocket_manager import broadcast_qa_event
    await broadcast_qa_event(tid, {
        "event_type": "qa.call_started",
        "call_uuid": call_uuid,
        "tenant_id": tid,
        "agent_extension": req.agent_extension,
        "customer_extension": req.customer_extension,
    })

    return {
        "status": "started",
        "call_uuid": call_uuid,
        "tenant_id": tid,
        "agent_extension": req.agent_extension,
        "customer_extension": req.customer_extension,
        "agent_leg": "bleg",
        "warn_count": 0,
        "escalated": False,
        "started_at": now_ts
    }


@router.post("/calls/{call_uuid}/simulate-violation")
async def simulate_violation(
    call_uuid: str,
    payload: Optional[SimulateSpeechRequest] = None,
    current_user: CurrentUser = Depends(require_module("virtual-qa", allowed_roles=["SUPER_ADMIN", "TENANT_ADMIN", "SUB_ADMIN", "SUPERVISOR"]))
):
    """
    Demo/testing endpoint to trigger live Gemini evaluation and ElevenLabs whisper on an active call.
    Accepts an optional custom agent utterance from the UI.
    """
    tid = current_user.tenant_id
    r_obj = get_redis()
    redis_client = await r_obj if hasattr(r_obj, "__await__") else r_obj
    if not tid and current_user.is_super_admin:
        keys = await redis_client.keys(f"tenant:*:qa_call:{call_uuid}") if redis_client else []
        if keys:
            first_key = keys[0].decode("utf-8") if isinstance(keys[0], bytes) else keys[0]
            tid = first_key.split(":")[1]

    call_data = await redis_client.hgetall(f"tenant:{tid}:qa_call:{call_uuid}") if redis_client else None
    if not call_data:
        raise HTTPException(status_code=404, detail="Call not found")

    cfg_id = call_data.get("config_id")
    qa_cfg = None
    if cfg_id:
        qa_cfg = await execute_query_one(
            "SELECT id, COALESCE(policy_prompt, qa_policy_prompt) as policy_prompt, banned_keywords, max_warn_retries, cooldown_seconds, supervisor_extension FROM virtual_qa_configs WHERE id = CAST(:id AS uuid)",
            {"id": cfg_id}
        )
    if not qa_cfg:
        qa_cfg = await execute_query_one(
            "SELECT id, COALESCE(policy_prompt, qa_policy_prompt) as policy_prompt, banned_keywords, max_warn_retries, cooldown_seconds, supervisor_extension FROM virtual_qa_configs WHERE tenant_id = CAST(:tid AS uuid) AND enabled = true LIMIT 1",
            {"tid": tid}
        )

    policy_prompt = (qa_cfg.get("policy_prompt") if qa_cfg else None) or "Agent must be courteous, empathetic, and professional at all times. Never use profanity, insults, or rude tone."
    banned_keywords = qa_cfg.get("banned_keywords") if qa_cfg else ["shut up", "idiot", "stupid", "hell", "damn"]
    if isinstance(banned_keywords, str):
        try:
            banned_keywords = json.loads(banned_keywords)
        except Exception:
            banned_keywords = ["shut up", "idiot", "stupid", "hell", "damn"]

    utterance = payload.utterance.strip() if payload and payload.utterance else "Look you stupid idiot, didn't I tell you your payment failed?"

    from src.services.virtual_qa_service import local_policy_check, evaluate_with_gemini
    local_hit = local_policy_check(utterance, banned_keywords)
    gemini_res = await evaluate_with_gemini(utterance, policy_prompt)

    is_violation = bool(local_hit or gemini_res.get("violation"))
    reason = gemini_res.get("reason") or (f"Banned keyword detected: {local_hit['keyword']}" if local_hit else "Policy violation")
    coaching = gemini_res.get("coaching") or (local_hit.get("coaching") if local_hit else "Hey, stay calm and keep your tone polite and professional.")

    if not is_violation:
        return {
            "status": "compliant",
            "violation": False,
            "transcript": utterance,
            "reason": reason or "Complies with policy",
            "coaching": None
        }

    call_info = {
        "call_uuid": call_uuid,
        "tenant_id": tid,
        "qa_agent_ext": call_data.get("agent_ext", "1001"),
        "qa_customer_ext": call_data.get("customer_ext", "1002"),
        "qa_agent_leg": call_data.get("agent_leg", "bleg"),
        "qa_config_id": str(qa_cfg["id"]) if qa_cfg and "id" in qa_cfg else cfg_id,
        "max_warn_retries": int(qa_cfg.get("max_warn_retries", 3) if qa_cfg else 3),
        "cooldown_seconds": int(qa_cfg.get("cooldown_seconds", 3) if qa_cfg else 3),
        "qa_supervisor_ext": str(qa_cfg.get("supervisor_extension", "9001") if qa_cfg else "9001"),
    }

    violation_data = {
        "violation": True,
        "type": "simulated",
        "keyword": local_hit["keyword"] if local_hit else None,
        "reason": reason,
        "coaching": coaching
    }

    res = await trigger_qa_violation(
        call_info=call_info,
        violation_data=violation_data,
        transcript=utterance,
        redis_client=redis_client,
        esl_client=qa_service
    )
    return {
        "status": "violation_processed",
        "violation": True,
        "transcript": utterance,
        "reason": reason,
        "coaching": coaching,
        "result": res
    }


@router.get("/events")
async def list_qa_events(
    call_uuid: Optional[str] = None,
    event_type: Optional[str] = None,
    tenant_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(require_module("virtual-qa", allowed_roles=["SUPER_ADMIN", "TENANT_ADMIN", "SUB_ADMIN", "SUPERVISOR"]))
):
    """Returns paginated QA audit events for the tenant."""
    tid = tenant_id if current_user.is_super_admin and tenant_id else current_user.tenant_id
    clauses = []
    params = {"limit": limit, "offset": offset}

    if tid:
        clauses.append("tenant_id = CAST(:tid AS uuid)")
        params["tid"] = tid
    elif not current_user.is_super_admin:
        raise HTTPException(status_code=400, detail="Tenant context required")

    if call_uuid:
        clauses.append("call_uuid = :call_uuid")
        params["call_uuid"] = call_uuid
    if event_type:
        clauses.append("event_type = :event_type")
        params["event_type"] = event_type

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT id, tenant_id, call_uuid, event_type, agent_extension,
               warn_count, transcript, coaching_message, audio_file_path,
               details, created_at
        FROM qa_events
        {where_clause}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """
    events = await execute_query(sql, params)
    return events
