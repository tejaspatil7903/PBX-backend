import os
import re
import json
import logging
import socket
from xml.etree.ElementTree import Element, SubElement, tostring
from typing import Dict, Any, List, Optional
from src.core.database import execute_query_one, execute_query
from src.core.redis import get_redis
from src.services.voicemail_prompt import (
    ensure_voicemail_prompt,
    ensure_conference_pin_prompt,
    ensure_conference_bad_pin_prompt,
    ensure_conference_no_moderator_prompt,
    ensure_conference_participant_count_prompt,
    ensure_wav_format
)
from src.services.conference_service import get_conference_live_status

logger = logging.getLogger("pbx.xml_curl.dialplan")

NOT_FOUND_XML = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<document type="freeswitch/xml">
  <section name="result">
    <result status="not found"/>
  </section>
</document>"""

# Complete list of failure dispositions to continue to voicemail (excluding NORMAL_CLEARING)
VOICEMAIL_CONTINUE_ON_FAIL = (
    "NORMAL_TEMPORARY_FAILURE,USER_BUSY,NO_ANSWER,TIMEOUT,ALLOTTED_TIMEOUT,"
    "NO_ROUTE_DESTINATION,USER_NOT_REGISTERED,CALL_REJECTED,NO_USER_RESPONSE,"
    "PROGRESS_TIMEOUT,RECOVERY_ON_TIMER_EXPIRE,SERVICE_NOT_IMPLEMENTED,"
    "SUBSCRIBER_ABSENT,UNALLOCATED_NUMBER,INCOMPATIBLE_DESTINATION"
)





def get_beep_file() -> str:
    beep = "/var/lib/freeswitch/recordings/prompts/beep.wav"
    if os.path.exists(beep) and os.path.getsize(beep) > 100:
        return beep
    return "tone_stream://%(250,50,440)"


async def resolve_ivr_prompt(db_audio: Optional[str] = None) -> str:
    """
    Resolves the IVR prompt audio file path.
    Checks:
    1. Direct file path if exists (transcodes MP3/OGG to 8kHz mono WAV)
    2. /var/lib/freeswitch/recordings/audio/{db_audio}
    3. /var/lib/freeswitch/recordings/prompts/{db_audio}
    4. Database lookup in audio_files table
    5. Fallback synthesized /var/lib/freeswitch/recordings/prompts/ivr_welcome.wav
    6. Callie system sounds or tone_stream
    """
    if db_audio:
        db_audio = str(db_audio).strip()
        if os.path.isabs(db_audio) and os.path.exists(db_audio) and os.path.getsize(db_audio) > 500:
            return ensure_wav_format(db_audio)
        cand_audio = f"/var/lib/freeswitch/recordings/audio/{db_audio}"
        if os.path.exists(cand_audio) and os.path.getsize(cand_audio) > 500:
            return ensure_wav_format(cand_audio)
        cand_prompts = f"/var/lib/freeswitch/recordings/prompts/{db_audio}"
        if os.path.exists(cand_prompts) and os.path.getsize(cand_prompts) > 500:
            return ensure_wav_format(cand_prompts)
        try:
            af = await execute_query_one(
                "SELECT file_path FROM audio_files WHERE file_name = :a OR id::text = :a OR name = :a LIMIT 1",
                {"a": db_audio}
            )
            if af and af.get("file_path"):
                fp = af["file_path"]
                if os.path.exists(fp) and os.path.getsize(fp) > 500:
                    return ensure_wav_format(fp)
                base_fp, _ = os.path.splitext(fp)
                cand_wav = f"{base_fp}.wav"
                if os.path.exists(cand_wav) and os.path.getsize(cand_wav) > 500:
                    return cand_wav
        except Exception:
            pass

        # Check if an 8kHz WAV equivalent exists in audio directory
        base_a, ext_a = os.path.splitext(db_audio)
        if ext_a.lower() != ".wav":
            cand_converted = f"/var/lib/freeswitch/recordings/audio/{base_a}.wav"
            if os.path.exists(cand_converted) and os.path.getsize(cand_converted) > 500:
                return cand_converted

    # Ensure and return default synthesized IVR welcome prompt
    try:
        from src.services.voicemail_prompt import ensure_ivr_prompt
        return ensure_ivr_prompt()
    except Exception as e:
        logger.warning(f"Could not ensure IVR prompt: {e}")
        return "tone_stream://%(2000,4000,440,480)"


async def handle_dialplan_request(form_data: Dict[str, Any]) -> str:
    """
    Handles section=dialplan XML-CURL requests from FreeSWITCH.
    Dynamically generates dialplan XML for:
    1. Internal Extension-to-Extension calling with recording & voicemail fallback
    2. Outbound calling via assigned gateway / SIP trunk (with carrier simulation fallback)
    3. Voicemail with audio prompt, tone beep, recording, and webhook notification
    4. Incoming call routing with Test DIDs to Queue, IVR, and Hunt Group
    5. Automatic CDR generation and Call Recording tagging
    """
    context = form_data.get("Caller-Context", "public")
    dest_number = form_data.get("Caller-Destination-Number", "").strip()
    caller_id_number = form_data.get("Caller-Caller-ID-Number", "").strip()
    caller_id_name = form_data.get("Caller-Caller-ID-Name", "").strip()
    domain_name = form_data.get("domain") or form_data.get("variable_domain_name") or form_data.get("variable_sip_req_host") or "pbx.terrixai.com"
    tenant_id = form_data.get("variable_tenant_id") or form_data.get("variable_accountcode")

    logger.info(
        f"mod_xml_curl Dialplan request: context='{context}', dest='{dest_number}', "
        f"caller='{caller_id_number}', domain='{domain_name}', tenant='{tenant_id}'"
    )

    if not dest_number:
        return NOT_FOUND_XML

    # Resolve Tenant ID from caller extension if present
    if not tenant_id and caller_id_number:
        caller_ext = await execute_query_one(
            "SELECT tenant_id FROM extensions WHERE extension_number = :ext AND enabled = true AND deleted_at IS NULL LIMIT 1",
            {"ext": caller_id_number}
        )
        if caller_ext and caller_ext.get("tenant_id"):
            tenant_id = str(caller_ext["tenant_id"])

    # Resolve Tenant ID if not already passed in channel variables
    if not tenant_id and context.startswith("tenant-"):
        tenant_id = context.replace("tenant-", "")

    if not tenant_id and domain_name:
        t_row = await execute_query_one(
            """
            SELECT id FROM tenants 
            WHERE (sip_domain = :d OR domain = :d OR :d LIKE '%' || domain OR domain LIKE '%' || :d) 
              AND enabled = true AND deleted_at IS NULL
            LIMIT 1
            """,
            {"d": domain_name}
        )
        if t_row:
            tenant_id = str(t_row["id"])

    # Build XML Document
    doc = Element("document", type="freeswitch/xml")
    section = SubElement(doc, "section", name="dialplan", description="Dynamic Multi-Tenant Dialplan")
    context_elem = SubElement(section, "context", name=context)

    # =========================================================================
    # SCENARIO 1: SPECIAL FEATURE CODES
    # =========================================================================
    if dest_number in ("*96", "9196"):
        ext_elem = SubElement(context_elem, "extension", name="echo_test")
        cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=r"^(\*96|9196)$")
        SubElement(cond_elem, "action", application="set", data=f"tenant_id={tenant_id}")
        SubElement(cond_elem, "action", application="set", data="direction=internal")
        SubElement(cond_elem, "action", application="answer")
        SubElement(cond_elem, "action", application="sleep", data="500")
        SubElement(cond_elem, "action", application="echo")
        return format_xml(doc)

    if dest_number in ("*95", "9195"):
        ext_elem = SubElement(context_elem, "extension", name="moh_test")
        cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=r"^(\*95|9195)$")
        SubElement(cond_elem, "action", application="set", data=f"tenant_id={tenant_id}")
        SubElement(cond_elem, "action", application="set", data="direction=internal")
        SubElement(cond_elem, "action", application="answer")
        SubElement(cond_elem, "action", application="playback", data="local_stream://moh")
        return format_xml(doc)

    if dest_number == "park":
        ext_elem = SubElement(context_elem, "extension", name="park_loopback")
        cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression="^park$")
        SubElement(cond_elem, "action", application="park")
        return format_xml(doc)

    # Supervisor Live Intervention: *88<mode><agent_ext>
    # Modes: 0=Listen (spy), 1=Whisper aleg, 2=Whisper bleg, 3=Barge (both legs)
    sup_match = re.match(r"^\*88([0-3])(\d{2,10})$", dest_number)
    if sup_match:
        mode = sup_match.group(1)
        target_agent = sup_match.group(2)
        redis_obj = get_redis()
        if hasattr(redis_obj, "__await__"):
            redis_client = await redis_obj
        else:
            redis_client = redis_obj

        active_uuid = None
        if redis_client:
            if tenant_id:
                active_uuid = await redis_client.get(f"tenant:{tenant_id}:qa_agent_call:{target_agent}")
            if not active_uuid:
                active_uuid = await redis_client.get(f"qa_agent_call:{target_agent}")

        if active_uuid:
            if isinstance(active_uuid, bytes):
                active_uuid = active_uuid.decode("utf-8")
            ext_elem = SubElement(context_elem, "extension", name=f"supervisor_eavesdrop_{target_agent}")
            cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")
            SubElement(cond_elem, "action", application="set", data=f"tenant_id={tenant_id}")
            SubElement(cond_elem, "action", application="set", data="direction=internal")
            SubElement(cond_elem, "action", application="set", data="eavesdrop_enable_dtmf=true")
            if mode == "1":
                SubElement(cond_elem, "action", application="set", data="eavesdrop_whisper_aleg=true")
            elif mode == "2":
                SubElement(cond_elem, "action", application="set", data="eavesdrop_whisper_bleg=true")
            # mode 0 is listen only; mode 3 is barge (neither whisper flag set, FreeSWITCH eavesdrop bridges both)

            SubElement(cond_elem, "action", application="answer")
            SubElement(cond_elem, "action", application="eavesdrop", data=active_uuid)
            return format_xml(doc)
        else:
            ext_elem = SubElement(context_elem, "extension", name="supervisor_eavesdrop_not_found")
            cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")
            SubElement(cond_elem, "action", application="respond", data="404 Agent Not in Active Call")
            SubElement(cond_elem, "action", application="hangup", data="UNALLOCATED_NUMBER")
            return format_xml(doc)

    # Dedicated Voicemail Destination (triggered via transfer_on_fail or direct transfer)
    if dest_number.startswith("voicemail_"):
        target_ext_num = dest_number.replace("voicemail_", "").strip()
        ext_elem = SubElement(context_elem, "extension", name=f"voicemail_route_{target_ext_num}")
        cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")
        SubElement(cond_elem, "action", application="set", data=f"tenant_id={tenant_id}")
        SubElement(cond_elem, "action", application="set", data="direction=internal")
        add_voicemail_block(cond_elem, target_ext_num, domain_name, tenant_id)
        return format_xml(doc)

    # Dedicated Forwarding Failover Destination (triggered via transfer_on_fail or no-answer transfer)
    if dest_number.startswith("fwd_no_answer_"):
        target_ext_num = dest_number.replace("fwd_no_answer_", "").strip()
        logger.info(f"Handling failover forward on no-answer for extension: {target_ext_num}")

        # Look up forwarding destination
        cf_row = await execute_query_one(
            """
            SELECT cf.forward_no_answer_destination
            FROM call_forwarding cf
            JOIN extensions e ON cf.extension_id = e.id
            WHERE e.tenant_id = CAST(:tid AS uuid) AND e.extension_number = :ext
              AND cf.forward_no_answer_enabled = true
            """,
            {"tid": tenant_id, "ext": target_ext_num}
        )
        fwd_dest = cf_row.get("forward_no_answer_destination") if cf_row else None
        if fwd_dest:
            fwd_dest = str(fwd_dest).strip()
            if "@" in fwd_dest:
                fwd_dest = fwd_dest.split("@")[0].strip()

        ext_elem = SubElement(context_elem, "extension", name=f"fwd_route_{target_ext_num}")
        cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")
        SubElement(cond_elem, "action", application="set", data=f"tenant_id={tenant_id}")
        SubElement(cond_elem, "action", application="set", data="direction=internal")

        # Avoid self-referential loop if forward destination is the target extension itself
        if fwd_dest and fwd_dest != target_ext_num:
            SubElement(cond_elem, "action", application="log", data=f"INFO Forwarding call for ext {target_ext_num} to {fwd_dest}")
            SubElement(cond_elem, "action", application="set", data=f"transfer_on_fail=voicemail_{target_ext_num} XML {context}")
            SubElement(cond_elem, "action", application="transfer", data=f"{fwd_dest} XML {context}")
        else:
            logger.info(f"Forward destination invalid or self-referential (fwd_dest={fwd_dest}, target={target_ext_num}). Routing to voicemail_{target_ext_num}")
            add_voicemail_block(cond_elem, target_ext_num, domain_name, tenant_id)

        return format_xml(doc)

    # =========================================================================
    # SCENARIO 4: INCOMING CALLS WITH TEST DIDs / QUEUE / IVR / HUNT GROUP
    # =========================================================================
    # Dynamic Queue Lookup from Database or default test DIDs
    q_direct = await execute_query_one(
        """
        SELECT queue_number, name, tenant_id FROM queues
        WHERE (queue_number = :dest OR name = :dest OR id::text = :dest)
          AND enabled = true AND deleted_at IS NULL
        LIMIT 1
        """,
        {"dest": dest_number}
    )
    if q_direct or dest_number in ("5551", "+15551001", "7777"):
        q_num = q_direct["queue_number"] if q_direct else "default"
        q_tid = str(q_direct.get("tenant_id") if q_direct else tenant_id or "")
        ext_elem = SubElement(context_elem, "extension", name=f"route_queue_{q_num}")
        cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")
        SubElement(cond_elem, "action", application="set", data=f"tenant_id={q_tid}")
        SubElement(cond_elem, "action", application="set", data="direction=inbound")
        SubElement(cond_elem, "action", application="set", data=f"did={dest_number}")
        SubElement(cond_elem, "action", application="set", data="hangup_after_bridge=true")
        SubElement(cond_elem, "action", application="export", data="hangup_after_bridge=true")
        SubElement(cond_elem, "action", application="answer")
        SubElement(cond_elem, "action", application="callcenter", data=q_num)
        return format_xml(doc)

    # Check for IVR Menu configured in database or default test DIDs
    ivr_menu = None
    if tenant_id:
        ivr_menu = await execute_query_one(
            """
            SELECT id, name, extension_number, timeout, max_retries, greeting_audio
            FROM ivr_menus
            WHERE tenant_id = CAST(:tid AS uuid)
              AND (extension_number = :dest OR name = :dest OR id::text = :dest)
              AND enabled = true AND deleted_at IS NULL
            ORDER BY updated_at DESC LIMIT 1
            """,
            {"tid": tenant_id, "dest": dest_number}
        )
    if not ivr_menu:
        ivr_menu = await execute_query_one(
            """
            SELECT id, name, extension_number, timeout, max_retries, greeting_audio
            FROM ivr_menus
            WHERE (extension_number = :dest OR name = :dest OR id::text = :dest)
              AND enabled = true AND deleted_at IS NULL
            ORDER BY updated_at DESC LIMIT 1
            """,
            {"dest": dest_number}
        )

    # Test DID 2 & IVR (6001 / 5552 / +15551002 or matched ivr_menus)
    if dest_number in ("5552", "+15551002", "6001") or ivr_menu:
        menu_id = str(ivr_menu["id"]) if ivr_menu else "46765586-c906-497f-83ba-0d39776cfbe4"
        ext_elem = SubElement(context_elem, "extension", name="route_ivr")
        cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")
        SubElement(cond_elem, "action", application="set", data=f"tenant_id={tenant_id}")
        SubElement(cond_elem, "action", application="set", data="direction=inbound")
        SubElement(cond_elem, "action", application="set", data=f"did={dest_number}")
        SubElement(cond_elem, "action", application="set", data=f"ivr_menu_id={menu_id}")
        SubElement(cond_elem, "action", application="set", data="hangup_after_bridge=true")
        SubElement(cond_elem, "action", application="export", data="hangup_after_bridge=true")
        SubElement(cond_elem, "action", application="answer")
        SubElement(cond_elem, "action", application="sleep", data="500")
        SubElement(cond_elem, "action", application="set", data=f"recording_file=/var/lib/freeswitch/recordings/{tenant_id}/${{uuid}}.wav")
        SubElement(cond_elem, "action", application="record_session", data=f"/var/lib/freeswitch/recordings/{tenant_id}/${{uuid}}.wav")

        menu_timeout = 5000
        if ivr_menu and ivr_menu.get("timeout"):
            menu_timeout = int(ivr_menu["timeout"]) * 1000

        prompt_file = await resolve_ivr_prompt(ivr_menu.get("greeting_audio") if ivr_menu else None)
        beep_file = get_beep_file()

        # Flush DTMF queue so residual line noise doesn't prematurely terminate IVR
        SubElement(cond_elem, "action", application="flush_dtmf")

        # Play IVR Welcome Greeting and capture DTMF (1-4 digits, with 3000ms inter-digit timeout)
        SubElement(
            cond_elem,
            "action",
            application="play_and_get_digits",
            data=f"1 4 3 {menu_timeout} # {prompt_file} {beep_file} dtmf_result \\d+ 3000"
        )
        # Transfer caller to ivr_choice_${dtmf_result}
        SubElement(cond_elem, "action", application="transfer", data="ivr_choice_${dtmf_result} XML " + context)
        # Fallback if no choice entered / timeout: route to 1001
        SubElement(cond_elem, "action", application="transfer", data="1001 XML " + context)
        return format_xml(doc)

    # Route IVR Captured Selection (ivr_choice_1, ivr_choice_2, ivr_choice_1002, etc.)
    if dest_number.startswith("ivr_choice_"):
        key = dest_number.replace("ivr_choice_", "").strip()
        ivr_menu_id = form_data.get("variable_ivr_menu_id") or "46765586-c906-497f-83ba-0d39776cfbe4"
        logger.info(f"Processing IVR Choice DTMF key='{key}', menu_id='{ivr_menu_id}' for context='{context}', tenant='{tenant_id}'")

        ext_elem = SubElement(context_elem, "extension", name=f"ivr_exec_{key or 'default'}")
        cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")
        SubElement(cond_elem, "action", application="set", data=f"tenant_id={tenant_id}")
        SubElement(cond_elem, "action", application="set", data="hangup_after_bridge=true")
        SubElement(cond_elem, "action", application="export", data="hangup_after_bridge=true")

        # 1. Direct Extension Dialing (2 to 4 digits, e.g. 1001, 1002, 9001)
        if len(key) >= 2 and key.isdigit():
            logger.info(f"IVR: Direct extension dial to {key}")
            SubElement(cond_elem, "action", application="transfer", data=f"{key} XML {context}")
            return format_xml(doc)

        # 2. Check IVR Nodes in Database for key
        ivr_node = None
        if key:
            if ivr_menu_id:
                ivr_node = await execute_query_one(
                    """
                    SELECT action_type, action_target
                    FROM ivr_nodes
                    WHERE ivr_menu_id = CAST(:mid AS uuid) AND dtmf_key = :key
                    LIMIT 1
                    """,
                    {"mid": ivr_menu_id, "key": key}
                )
            if not ivr_node and tenant_id:
                ivr_node = await execute_query_one(
                    """
                    SELECT n.action_type, n.action_target
                    FROM ivr_nodes n
                    JOIN ivr_menus m ON n.ivr_menu_id = m.id
                    WHERE m.tenant_id = CAST(:tid AS uuid) AND n.dtmf_key = :key
                    ORDER BY m.updated_at DESC LIMIT 1
                    """,
                    {"tid": tenant_id, "key": key}
                )

        if ivr_node:
            act_type = ivr_node.get("action_type")
            act_target = ivr_node.get("action_target")
            logger.info(f"IVR Node matched: key={key} -> {act_type}:{act_target}")

            if act_type == "extension":
                SubElement(cond_elem, "action", application="transfer", data=f"{act_target} XML {context}")
                return format_xml(doc)
            elif act_type == "queue":
                SubElement(cond_elem, "action", application="transfer", data=f"{act_target or '7777'} XML {context}")
                return format_xml(doc)
            elif act_type == "hunt_group":
                SubElement(cond_elem, "action", application="transfer", data=f"{act_target or '8888'} XML {context}")
                return format_xml(doc)
            elif act_type == "voicemail":
                add_voicemail_block(cond_elem, act_target or "1001", domain_name, tenant_id)
                return format_xml(doc)

        # 3. Standard built-in default menu map
        # 1 -> 1001 (Sales), 2 -> 1002 (Support), 3 -> Queue 7777, 4 -> Hunt Group 8888, 9 -> Voicemail 1001
        if key == "1":
            SubElement(cond_elem, "action", application="transfer", data=f"1001 XML {context}")
        elif key == "2":
            SubElement(cond_elem, "action", application="transfer", data=f"1002 XML {context}")
        elif key == "3":
            SubElement(cond_elem, "action", application="transfer", data=f"7777 XML {context}")
        elif key == "4":
            SubElement(cond_elem, "action", application="transfer", data=f"8888 XML {context}")
        elif key == "9":
            add_voicemail_block(cond_elem, "1001", domain_name, tenant_id)
        else:
            # Check if IVR has a configured timeout node (key 't' or 'timeout')
            timeout_node = None
            if not key and ivr_menu_id:
                timeout_node = await execute_query_one(
                    """
                    SELECT action_type, action_target
                    FROM ivr_nodes
                    WHERE ivr_menu_id = CAST(:mid AS uuid) AND dtmf_key IN ('t', 'timeout')
                    LIMIT 1
                    """,
                    {"mid": ivr_menu_id}
                )
            if timeout_node:
                t_act = timeout_node.get("action_type")
                t_tgt = timeout_node.get("action_target")
                logger.info(f"IVR Timeout node routed: {t_act}:{t_tgt}")
                if t_act == "extension":
                    SubElement(cond_elem, "action", application="transfer", data=f"{t_tgt} XML {context}")
                    return format_xml(doc)
                elif t_act == "queue":
                    SubElement(cond_elem, "action", application="transfer", data=f"{t_tgt or '7777'} XML {context}")
                    return format_xml(doc)
                elif t_act == "hunt_group":
                    SubElement(cond_elem, "action", application="transfer", data=f"{t_tgt or '8888'} XML {context}")
                    return format_xml(doc)
                elif t_act == "voicemail":
                    add_voicemail_block(cond_elem, t_tgt or "1001", domain_name, tenant_id)
                    return format_xml(doc)

            # Fallback for timeout or unrecognized key
            logger.info(f"IVR: Fallback transfer to default 1001")
            SubElement(cond_elem, "action", application="transfer", data=f"1001 XML {context}")
        return format_xml(doc)

    # =========================================================================
    # SCENARIO: CONFERENCE PIN AUTHENTICATION & EXECUTION
    # =========================================================================
    if dest_number.startswith("conf_exec_"):
        parts = dest_number.replace("conf_exec_", "").split("_", 1)
        conf_ext = parts[0]
        entered_pin = parts[1] if len(parts) > 1 else ""

        conf_row = await execute_query_one(
            """
            SELECT id, name, extension_number, pin, moderator_pin, max_members,
                   record_conference, wait_for_moderator, announce_join_leave, tenant_id
            FROM conferences
            WHERE (extension_number = :ext OR id::text = :ext) AND enabled = true
            LIMIT 1
            """,
            {"ext": conf_ext}
        )
        if conf_row:
            conf_name = conf_row.get("name") or f"conf_{conf_ext}"
            c_mod_pin = str(conf_row.get("moderator_pin") or "").strip()
            c_part_pin = str(conf_row.get("pin") or "").strip()
            if c_mod_pin.lower() in ("none", "null"):
                c_mod_pin = ""
            if c_part_pin.lower() in ("none", "null"):
                c_part_pin = ""
            conf_tid = str(conf_row.get("tenant_id") or tenant_id or "")
            rec_conf = bool(conf_row.get("record_conference"))

            ext_elem = SubElement(context_elem, "extension", name=f"conf_exec_{conf_ext}")
            cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")
            SubElement(cond_elem, "action", application="set", data=f"tenant_id={conf_tid}")
            SubElement(cond_elem, "action", application="set", data="direction=internal")
            SubElement(cond_elem, "action", application="answer")

            if rec_conf and conf_tid:
                rec_path = f"/var/lib/freeswitch/recordings/{conf_tid}/conf_{conf_row['id']}_${{uuid}}.wav"
                SubElement(cond_elem, "action", application="record_session", data=rec_path)

            if c_mod_pin and entered_pin == c_mod_pin:
                logger.info(f"Joining conference '{conf_name}' as MODERATOR")
                SubElement(cond_elem, "action", application="conference", data=f"{conf_name}@default+flags{{moderator}}")
            elif (c_part_pin and entered_pin == c_part_pin) or (not c_mod_pin and not c_part_pin):
                # Participant join path - verify moderator presence and announce participant count
                live_status = get_conference_live_status(conf_name)
                wait_mod = bool(conf_row.get("wait_for_moderator")) or bool(c_mod_pin)
                if wait_mod and not live_status["has_moderator"]:
                    logger.warning(f"Participant rejected from conference '{conf_name}': No moderator present in room")
                    no_mod_audio = ensure_conference_no_moderator_prompt()
                    SubElement(cond_elem, "action", application="playback", data=no_mod_audio)
                    SubElement(cond_elem, "action", application="hangup", data="NORMAL_CLEARING")
                else:
                    participant_number = live_status["member_count"] + 1
                    logger.info(f"Participant entering conference '{conf_name}' as member #{participant_number}")
                    count_audio = ensure_conference_participant_count_prompt(participant_number)
                    SubElement(cond_elem, "action", application="playback", data=count_audio)
                    SubElement(cond_elem, "action", application="conference", data=f"{conf_name}@default")
            else:
                logger.warning(f"Invalid conference PIN '{entered_pin}' for room '{conf_name}'")
                bad_pin_audio = ensure_conference_bad_pin_prompt()
                SubElement(cond_elem, "action", application="playback", data=bad_pin_audio)
                SubElement(cond_elem, "action", application="hangup", data="CALL_REJECTED")
            return format_xml(doc)

    # Dynamic Conference Room Lookup
    conf_record = await execute_query_one(
        """
        SELECT id, name, extension_number, pin, moderator_pin, max_members,
               record_conference, wait_for_moderator, announce_join_leave, tenant_id
        FROM conferences
        WHERE (extension_number = :dest OR id::text = :dest) AND enabled = true
        LIMIT 1
        """,
        {"dest": dest_number}
    )
    if conf_record:
        conf_ext = conf_record.get("extension_number") or dest_number
        conf_name = conf_record.get("name") or f"conf_{conf_ext}"
        conf_tid = str(conf_record.get("tenant_id") or tenant_id or "")
        c_part_pin = str(conf_record.get("pin") or "").strip()
        c_mod_pin = str(conf_record.get("moderator_pin") or "").strip()
        if c_part_pin.lower() in ("none", "null"):
            c_part_pin = ""
        if c_mod_pin.lower() in ("none", "null"):
            c_mod_pin = ""
        has_pin = bool(c_part_pin or c_mod_pin)

        ext_elem = SubElement(context_elem, "extension", name=f"conf_entry_{conf_ext}")
        cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")
        SubElement(cond_elem, "action", application="set", data=f"tenant_id={conf_tid}")
        SubElement(cond_elem, "action", application="set", data="direction=internal")
        SubElement(cond_elem, "action", application="answer")
        SubElement(cond_elem, "action", application="sleep", data="500")

        if has_pin:
            pin_audio = ensure_conference_pin_prompt()
            bad_pin_audio = ensure_conference_bad_pin_prompt()
            SubElement(cond_elem, "action", application="flush_dtmf")
            SubElement(
                cond_elem,
                "action",
                application="play_and_get_digits",
                data=f"1 10 3 5000 # {pin_audio} {bad_pin_audio} entered_conf_pin \\d+ 3000"
            )
            SubElement(cond_elem, "action", application="transfer", data=f"conf_exec_{conf_ext}_${{entered_conf_pin}} XML {context}")
        else:
            SubElement(cond_elem, "action", application="transfer", data=f"conf_exec_{conf_ext}_none XML {context}")
        return format_xml(doc)

    # Dynamic Hunt Group Lookup from Database
    hg_query = """
        SELECT id, name, extension_number, strategy, members, timeout, tenant_id
        FROM hunt_groups
        WHERE (extension_number = :dest OR name = :dest OR id::text = :dest)
          AND enabled = true AND deleted_at IS NULL
        LIMIT 1
    """
    hunt_group = await execute_query_one(hg_query, {"dest": dest_number})

    # Fallback for default test DIDs if not in database
    if not hunt_group and dest_number in ("5553", "+15551003", "8888"):
        hunt_group = {
            "name": "TESTGROUP",
            "extension_number": dest_number,
            "strategy": "simultaneous",
            "members": "1001, 1002",
            "timeout": 25,
            "tenant_id": tenant_id
        }

    if hunt_group and isinstance(hunt_group, dict) and hunt_group.get("name"):
        hg_name = hunt_group["name"]
        hg_timeout = hunt_group.get("timeout") or 25
        raw_members = hunt_group.get("members") or ""
        hg_strategy = (hunt_group.get("strategy") or "simultaneous").lower()
        hg_tenant_id = hunt_group.get("tenant_id") or tenant_id
        if hg_tenant_id:
            t_row = await execute_query_one(
                "SELECT sip_domain FROM tenants WHERE id = CAST(:tid AS uuid) AND deleted_at IS NULL",
                {"tid": str(hg_tenant_id)}
            )
            if t_row and t_row.get("sip_domain"):
                domain_name = t_row["sip_domain"]
        logger.info(f"Routing to Hunt Group '{hg_name}' (ext {dest_number}) with members: {raw_members} domain: {domain_name}")

        # Parse member extensions: strip whitespace and any domain part (e.g. 1001@pbx.aikyamlabs.local -> 1001)
        member_list = []
        for m in raw_members.split(","):
            m = m.strip()
            if not m:
                continue
            if "@" in m:
                m_user = m.split("@")[0].strip()
            else:
                m_user = m
            if m_user:
                member_list.append(m_user)

        ext_elem = SubElement(context_elem, "extension", name=f"route_hunt_group_{dest_number}")
        cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")
        SubElement(cond_elem, "action", application="set", data=f"tenant_id={hg_tenant_id}")
        SubElement(cond_elem, "action", application="set", data="direction=inbound")
        SubElement(cond_elem, "action", application="set", data=f"did={dest_number}")
        SubElement(cond_elem, "action", application="set", data="hangup_after_bridge=true")
        SubElement(cond_elem, "action", application="export", data="hangup_after_bridge=true")
        SubElement(cond_elem, "action", application="set", data=f"continue_on_fail={VOICEMAIL_CONTINUE_ON_FAIL}")
        SubElement(cond_elem, "action", application="set", data=f"recording_file=/var/lib/freeswitch/recordings/{hg_tenant_id}/${{uuid}}.wav")
        SubElement(cond_elem, "action", application="set", data=f"execute_on_answer=record_session /var/lib/freeswitch/recordings/{hg_tenant_id}/${{uuid}}.wav")
        SubElement(cond_elem, "action", application="set", data="ringback=%(2000,4000,440.0,480.0)")
        SubElement(cond_elem, "action", application="set", data="instant_ringback=true")

        bridge_endpoints = [f"user/{m_ext}@{domain_name}" for m_ext in member_list]

        if bridge_endpoints:
            # Sequential uses '|', Simultaneous uses ','
            sep = "|" if hg_strategy == "sequential" else ","
            bridge_data = f"[leg_timeout={hg_timeout}]" + sep.join(bridge_endpoints)
            SubElement(cond_elem, "action", application="bridge", data=bridge_data)

        # Fallback to voicemail on the first member extension if nobody answers
        fallback_ext = member_list[0] if member_list else "1001"
        add_voicemail_block(cond_elem, fallback_ext, domain_name, hg_tenant_id)
        return format_xml(doc)

    # Check Inbound Database Call Routes & DIDs (globally or for tenant)
    inbound_route = await execute_query_one(
        """
        SELECT cr.id, cr.destination_type, cr.destination, cr.tenant_id, t.sip_domain
        FROM call_routes cr
        LEFT JOIN tenants t ON cr.tenant_id = t.id
        WHERE (
            cr.did_number = :dest 
            OR cr.did_number = '+' || :dest 
            OR cr.did_number = TRIM(LEADING '+' FROM :dest)
            OR :dest LIKE '%' || cr.did_number
            OR (LENGTH(:dest) >= 10 AND LENGTH(cr.did_number) >= 10 AND RIGHT(:dest, 10) = RIGHT(cr.did_number, 10))
        )
        AND cr.route_type = 'inbound_did' AND cr.enabled = true AND cr.deleted_at IS NULL
        ORDER BY cr.priority ASC LIMIT 1
        """,
        {"dest": dest_number}
    )
    if not inbound_route:
        inbound_route = await execute_query_one(
            """
            SELECT d.id, d.destination_type, COALESCE(d.destination_target, d.destination) as destination, d.tenant_id, t.sip_domain
            FROM dids d
            LEFT JOIN tenants t ON d.tenant_id = t.id
            WHERE (
                d.did_number = :dest 
                OR d.did_number = '+' || :dest 
                OR d.did_number = TRIM(LEADING '+' FROM :dest)
                OR :dest LIKE '%' || d.did_number
                OR (LENGTH(:dest) >= 10 AND LENGTH(d.did_number) >= 10 AND RIGHT(:dest, 10) = RIGHT(d.did_number, 10))
            )
            AND d.enabled = true AND d.deleted_at IS NULL
            LIMIT 1
            """,
            {"dest": dest_number}
        )

    if inbound_route:
        tenant_id = str(inbound_route.get("tenant_id") or tenant_id or "")
        domain_name = inbound_route.get("sip_domain") or domain_name or "pbx.terrixai.com"
        dest_type = inbound_route.get("destination_type") or "extension"
        target = (inbound_route.get("destination") or "1001").strip()
        ext_elem = SubElement(context_elem, "extension", name=f"did_{dest_number}")
        cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")
        SubElement(cond_elem, "action", application="set", data=f"tenant_id={tenant_id}")
        SubElement(cond_elem, "action", application="set", data="direction=inbound")
        SubElement(cond_elem, "action", application="set", data=f"did={dest_number}")
        SubElement(cond_elem, "action", application="set", data="hangup_after_bridge=true")
        SubElement(cond_elem, "action", application="export", data="hangup_after_bridge=true")
        SubElement(cond_elem, "action", application="set", data=f"continue_on_fail={VOICEMAIL_CONTINUE_ON_FAIL}")
        if tenant_id:
            try:
                os.makedirs(f"/var/lib/freeswitch/recordings/{tenant_id}", exist_ok=True)
            except OSError:
                pass
        SubElement(cond_elem, "action", application="set", data=f"recording_file=/var/lib/freeswitch/recordings/{tenant_id}/${{uuid}}.wav")
        SubElement(cond_elem, "action", application="record_session", data=f"/var/lib/freeswitch/recordings/{tenant_id}/${{uuid}}.wav")
        SubElement(cond_elem, "action", application="set", data="ringback=%(2000,4000,440.0,480.0)")
        SubElement(cond_elem, "action", application="set", data="instant_ringback=true")

        if dest_type == "extension":
            SubElement(cond_elem, "action", application="bridge", data=f"[leg_timeout=25]user/{target}@{domain_name}")
            add_voicemail_block(cond_elem, target, domain_name, tenant_id)
        elif dest_type == "queue":
            q_row = await execute_query_one(
                """
                SELECT queue_number, name FROM queues
                WHERE (queue_number = :t OR name = :t OR id::text = :t)
                  AND enabled = true AND deleted_at IS NULL
                LIMIT 1
                """,
                {"t": target}
            )
            q_target = q_row["queue_number"] if q_row else (target or "default")
            SubElement(cond_elem, "action", application="answer")
            SubElement(cond_elem, "action", application="callcenter", data=q_target)
        elif dest_type == "hunt_group":
            hg_row = await execute_query_one(
                """
                SELECT id, name, extension_number, strategy, members, timeout, tenant_id
                FROM hunt_groups
                WHERE (extension_number = :t OR name = :t OR id::text = :t) AND enabled = true AND deleted_at IS NULL
                LIMIT 1
                """,
                {"t": target}
            )
            if not hg_row and tenant_id:
                hg_row = await execute_query_one(
                    """
                    SELECT id, name, extension_number, strategy, members, timeout, tenant_id
                    FROM hunt_groups
                    WHERE tenant_id = CAST(:tid AS uuid) AND enabled = true AND deleted_at IS NULL
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    {"tid": tenant_id}
                )
            if hg_row:
                raw_m = hg_row.get("members") or ""
                hg_to = hg_row.get("timeout") or 25
                hg_strat = (hg_row.get("strategy") or "simultaneous").lower()
                m_list = [m.split("@")[0].strip() for m in raw_m.split(",") if m.strip()]
                b_endpoints = [f"user/{m_ext}@{domain_name}" for m_ext in m_list if m_ext]
                if b_endpoints:
                    sep = "|" if hg_strat == "sequential" else ","
                    SubElement(cond_elem, "action", application="bridge", data=f"[leg_timeout={hg_to}]" + sep.join(b_endpoints))
                fallback_ext = m_list[0] if m_list else "1001"
                add_voicemail_block(cond_elem, fallback_ext, domain_name, tenant_id)
            else:
                SubElement(cond_elem, "action", application="transfer", data=f"{target or '8888'} XML {context}")
        elif dest_type == "conference":
            SubElement(cond_elem, "action", application="transfer", data=f"{target} XML {context}")
        elif dest_type == "ivr":
            ivr_row = await execute_query_one(
                """
                SELECT id, name, extension_number, timeout, max_retries, greeting_audio
                FROM ivr_menus
                WHERE (name = :t OR extension_number = :t OR id::text = :t)
                  AND enabled = true AND deleted_at IS NULL
                ORDER BY updated_at DESC LIMIT 1
                """,
                {"t": target}
            )
            if not ivr_row and tenant_id:
                ivr_row = await execute_query_one(
                    """
                    SELECT id, name, extension_number, timeout, max_retries, greeting_audio
                    FROM ivr_menus
                    WHERE tenant_id = CAST(:tid AS uuid) AND enabled = true AND deleted_at IS NULL
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    {"tid": tenant_id}
                )
            menu_id = str(ivr_row["id"]) if ivr_row else "46765586-c906-497f-83ba-0d39776cfbe4"
            SubElement(cond_elem, "action", application="set", data=f"ivr_menu_id={menu_id}")
            SubElement(cond_elem, "action", application="answer")
            SubElement(cond_elem, "action", application="sleep", data="500")

            menu_timeout = 5000
            if ivr_row and ivr_row.get("timeout"):
                menu_timeout = int(ivr_row["timeout"]) * 1000

            prompt_file = await resolve_ivr_prompt(ivr_row.get("greeting_audio") if ivr_row else None)
            beep_file = get_beep_file()

            SubElement(
                cond_elem,
                "action",
                application="play_and_get_digits",
                data=f"1 4 3 {menu_timeout} # {prompt_file} {beep_file} dtmf_result \\d+"
            )
            SubElement(cond_elem, "action", application="transfer", data="ivr_choice_${dtmf_result} XML " + context)
            SubElement(cond_elem, "action", application="transfer", data="1001 XML " + context)
        elif dest_type == "voicemail":
            add_voicemail_block(cond_elem, target or "1001", domain_name, tenant_id)
        return format_xml(doc)

    # =========================================================================
    # SCENARIO 1 & 3: INTERNAL EXTENSION-TO-EXTENSION CALL WITH VOICEMAIL
    # =========================================================================
    target_ext = None
    if tenant_id:
        target_ext = await execute_query_one(
            """
            SELECT e.id, e.extension_number, e.display_name, e.caller_id_name, e.caller_id_number,
                   e.email, e.enabled, e.no_answer_timeout, e.tenant_id, t.sip_domain,
                   COALESCE(e.is_virtual, false) as is_virtual
            FROM extensions e
            JOIN tenants t ON e.tenant_id = t.id
            WHERE e.tenant_id = CAST(:tenant_id AS uuid) AND e.extension_number = :dest
              AND e.enabled = true AND e.deleted_at IS NULL
            """,
            {"tenant_id": tenant_id, "dest": dest_number}
        )

    if not target_ext:
        target_ext = await execute_query_one(
            """
            SELECT e.id, e.extension_number, e.display_name, e.caller_id_name, e.caller_id_number,
                   e.email, e.enabled, e.no_answer_timeout, e.tenant_id, t.sip_domain,
                   COALESCE(e.is_virtual, false) as is_virtual
            FROM extensions e
            JOIN tenants t ON e.tenant_id = t.id
            WHERE e.extension_number = :dest
              AND e.enabled = true AND e.deleted_at IS NULL
            LIMIT 1
            """,
            {"dest": dest_number}
        )

    if target_ext and not tenant_id:
        tenant_id = str(target_ext.get("tenant_id") or "")

    if target_ext:
        ext_num = target_ext["extension_number"]
        callee_domain = target_ext.get("sip_domain") or domain_name

        if target_ext.get("is_virtual"):
            logger.info(f"Target extension {ext_num} is a Virtual QA extension. Parking call.")
            ext_elem = SubElement(context_elem, "extension", name=f"virtual_ext_{ext_num}")
            cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=f"^{ext_num}$")
            SubElement(cond_elem, "action", application="answer")
            SubElement(cond_elem, "action", application="sleep", data="500")
            SubElement(cond_elem, "action", application="playback", data="ivr/ivr-this_is_an_automated_system.wav")
            SubElement(cond_elem, "action", application="hangup")
            return format_xml(doc)

        # Check tenant policy for internal calling
        tenant_row = await execute_query_one(
            "SELECT allow_internal_calls FROM tenants WHERE id = CAST(:tid AS uuid)",
            {"tid": tenant_id}
        )
        allow_internal = tenant_row.get("allow_internal_calls") if tenant_row else True
        if allow_internal is False:
            logger.info(f"Internal extension calling disabled for tenant {tenant_id}. Rejecting call to {ext_num}")
            ext_elem = SubElement(context_elem, "extension", name=f"reject_internal_{ext_num}")
            cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=f"^{ext_num}$")
            SubElement(cond_elem, "action", application="respond", data="403 Forbidden - Internal Calling Disabled")
            SubElement(cond_elem, "action", application="hangup", data="CALL_REJECTED")
            return format_xml(doc)

        # Check Call Forwarding
        cf_record = await execute_query_one(
            """
            SELECT forward_always_enabled, forward_always_destination,
                   forward_busy_enabled, forward_busy_destination,
                   forward_no_answer_enabled, forward_no_answer_destination,
                   forward_no_answer_timeout
            FROM call_forwarding
            WHERE extension_id = CAST(:ext_id AS uuid)
            """,
            {"ext_id": str(target_ext["id"])}
        )

        has_fwd_no_answer = bool(cf_record and cf_record.get("forward_no_answer_enabled") and cf_record.get("forward_no_answer_destination"))
        fwd_no_answer_dest = cf_record.get("forward_no_answer_destination", "").strip() if has_fwd_no_answer else ""
        if "@" in fwd_no_answer_dest:
            fwd_no_answer_dest = fwd_no_answer_dest.split("@")[0].strip()

        # Configurable no-answer timeout:
        # If call forwarding on no answer has a configured timeout, use it!
        # Otherwise fallback to extension's no_answer_timeout, default 15s.
        timeout = target_ext.get("no_answer_timeout")
        if not timeout or not isinstance(timeout, int) or timeout <= 0:
            timeout = 15

        if has_fwd_no_answer and cf_record.get("forward_no_answer_timeout"):
            try:
                cf_t = int(cf_record["forward_no_answer_timeout"])
                if cf_t > 0:
                    timeout = cf_t
            except (ValueError, TypeError):
                pass

        ext_elem = SubElement(context_elem, "extension", name=f"internal_ext_{ext_num}")
        cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=f"^{ext_num}$")

        # Set standard call & CDR variables
        SubElement(cond_elem, "action", application="set", data=f"tenant_id={tenant_id}")
        SubElement(cond_elem, "action", application="set", data="direction=internal")
        SubElement(cond_elem, "action", application="set", data="hangup_after_bridge=true")
        SubElement(cond_elem, "action", application="export", data="hangup_after_bridge=true")

        # Ringing timeout variables with ignore_early_media=true so timer counts while ringing
        SubElement(cond_elem, "action", application="set", data="ignore_early_media=true")
        SubElement(cond_elem, "action", application="set", data=f"call_timeout={timeout}")
        SubElement(cond_elem, "action", application="set", data=f"originate_timeout={timeout}")
        SubElement(cond_elem, "action", application="set", data=f"leg_timeout={timeout}")
        SubElement(cond_elem, "action", application="set", data="ringback=%(2000,4000,440.0,480.0)")
        SubElement(cond_elem, "action", application="set", data="instant_ringback=true")
        SubElement(cond_elem, "action", application="export", data="sip_contact_user=${destination_number}")

        # Check Virtual QA configuration for real-time monitoring
        qa_cfg = None
        if tenant_id:
            try:
                qa_cfg = await execute_query_one(
                    """
                    SELECT id, qa_extension_id, max_warn_retries, cooldown_seconds,
                           supervisor_extension, monitored_agents, monitored_callers
                    FROM virtual_qa_configs
                    WHERE tenant_id = CAST(:tid AS uuid) AND enabled = true
                    LIMIT 1
                    """,
                    {"tid": tenant_id}
                )
            except Exception as e:
                logger.warning(f"Error querying virtual_qa_configs: {e}")

        is_qa_monitored = False
        qa_agent_leg = "bleg"
        qa_agent_ext = ""
        qa_customer_ext = ""

        if qa_cfg:
            agents_cfg = qa_cfg.get("monitored_agents") or []
            if isinstance(agents_cfg, str):
                try:
                    agents_cfg = json.loads(agents_cfg)
                except Exception:
                    agents_cfg = [agents_cfg]
            callers_cfg = qa_cfg.get("monitored_callers") or ["*"]
            if isinstance(callers_cfg, str):
                try:
                    callers_cfg = json.loads(callers_cfg)
                except Exception:
                    callers_cfg = [callers_cfg]

            if caller_id_number in agents_cfg:
                is_qa_monitored = True
                qa_agent_leg = "aleg"
                qa_agent_ext = caller_id_number
                qa_customer_ext = ext_num
            elif ext_num in agents_cfg:
                is_qa_monitored = True
                qa_agent_leg = "bleg"
                qa_agent_ext = ext_num
                qa_customer_ext = caller_id_number

            if is_qa_monitored and callers_cfg and "*" not in callers_cfg:
                if qa_customer_ext not in callers_cfg:
                    is_qa_monitored = False

        if is_qa_monitored:
            logger.info(f"Call {caller_id_number} -> {ext_num} matches Virtual QA monitoring policy (agent={qa_agent_ext}, leg={qa_agent_leg})")
            SubElement(cond_elem, "action", application="set", data="RECORD_STEREO=true")
            SubElement(cond_elem, "action", application="set", data="enable_file_write_buffering=false")
            SubElement(cond_elem, "action", application="set", data="qa_enabled=true")
            SubElement(cond_elem, "action", application="set", data=f"qa_config_id={qa_cfg['id']}")
            SubElement(cond_elem, "action", application="set", data=f"qa_virtual_ext={qa_cfg.get('qa_extension_id') or ''}")
            SubElement(cond_elem, "action", application="set", data=f"qa_agent_leg={qa_agent_leg}")
            SubElement(cond_elem, "action", application="set", data=f"qa_agent_ext={qa_agent_ext}")
            SubElement(cond_elem, "action", application="set", data=f"qa_customer_ext={qa_customer_ext}")
            SubElement(cond_elem, "action", application="set", data=f"qa_max_retries={qa_cfg.get('max_warn_retries', 3)}")
            SubElement(cond_elem, "action", application="set", data=f"qa_cooldown={qa_cfg.get('cooldown_seconds', 10)}")
            SubElement(cond_elem, "action", application="set", data=f"qa_supervisor_ext={qa_cfg.get('supervisor_extension') or ''}")

        # Record call ONLY upon answer (so no premature early media during ringing)
        if tenant_id:
            try:
                os.makedirs(f"/var/lib/freeswitch/recordings/{tenant_id}", exist_ok=True)
            except OSError:
                pass
        rec_path = f"/var/lib/freeswitch/recordings/{tenant_id}/${{uuid}}.wav"
        SubElement(cond_elem, "action", application="set", data=f"recording_file={rec_path}")
        SubElement(cond_elem, "action", application="set", data=f"execute_on_answer=record_session {rec_path}")

        # Failover target on no-answer or failure: forward target if enabled, else voicemail
        # Prevent self-forwarding loop if target extension forwards to itself
        if has_fwd_no_answer and fwd_no_answer_dest and fwd_no_answer_dest == ext_num:
            logger.warning(f"Self-referential forward prevented: Extension {ext_num} cannot forward to itself. Falling back to voicemail.")
            has_fwd_no_answer = False
            fail_target = f"voicemail_{ext_num}"
        else:
            fail_target = f"fwd_no_answer_{ext_num}" if has_fwd_no_answer else f"voicemail_{ext_num}"

        SubElement(cond_elem, "action", application="set", data="failure_causes=USER_BUSY,NO_ANSWER,CALL_REJECTED,ORIGINATOR_CANCEL,DESTINATION_OUT_OF_ORDER,NORMAL_TEMPORARY_FAILURE,TIMEOUT,ALLOTTED_TIMEOUT,PROGRESS_TIMEOUT")
        SubElement(cond_elem, "action", application="set", data=f"transfer_on_fail={fail_target} XML {context}")
        SubElement(cond_elem, "action", application="set", data="continue_on_fail=true")

        if cf_record and cf_record.get("forward_always_enabled") and cf_record.get("forward_always_destination"):
            cf_dest = cf_record["forward_always_destination"].strip()
            logger.info(f"Extension {ext_num} has unconditional forward to {cf_dest}")
            SubElement(cond_elem, "action", application="transfer", data=f"{cf_dest} XML {context}")
        else:
            callee_domain = target_ext.get("sip_domain") or domain_name
            bridge_data = f"{{ignore_early_media=true,originate_timeout={timeout},call_timeout={timeout},leg_timeout={timeout},continue_on_fail=true}}user/{ext_num}@{callee_domain}"
            logger.info(f"Bridging internal call to endpoint 'user/{ext_num}@{callee_domain}' with timeout={timeout}s...")
            SubElement(cond_elem, "action", application="bridge", data=bridge_data)
            SubElement(cond_elem, "action", application="transfer", data=f"{fail_target} XML {context}")

        # Declare the forward-on-no-answer extension in context if enabled
        if has_fwd_no_answer:
            fwd_ext_elem = SubElement(context_elem, "extension", name=f"fwd_no_answer_{ext_num}")
            fwd_cond_elem = SubElement(fwd_ext_elem, "condition", field="destination_number", expression=f"^fwd_no_answer_{ext_num}$")
            SubElement(fwd_cond_elem, "action", application="log", data=f"INFO Forwarding call for ext {ext_num} on no answer to {fwd_no_answer_dest}")
            SubElement(fwd_cond_elem, "action", application="set", data=f"transfer_on_fail=voicemail_{ext_num} XML {context}")
            SubElement(fwd_cond_elem, "action", application="transfer", data=f"{fwd_no_answer_dest} XML {context}")

        # Also declare the voicemail extension in the same context for instantaneous in-memory transfer
        vm_ext_elem = SubElement(context_elem, "extension", name=f"voicemail_{ext_num}")
        vm_cond_elem = SubElement(vm_ext_elem, "condition", field="destination_number", expression=f"^voicemail_{ext_num}$")
        add_voicemail_block(vm_cond_elem, ext_num, callee_domain, tenant_id)

        return format_xml(doc)

    # =========================================================================
    # SCENARIO 2: OUTBOUND CALLS VIA ASSIGNED GATEWAY / SIP TRUNK
    # =========================================================================
    # Matches numbers starting with 9 (e.g. 918005551234), 10+ digits, or E.164 (+)
    is_outbound = (
        dest_number.startswith("9") and len(dest_number) >= 5
    ) or (
        dest_number.isdigit() and len(dest_number) >= 10
    ) or (
        dest_number.startswith("+")
    )

    if is_outbound:
        # Identify caller extension to verify authorization and tenant ownership
        caller_user = form_data.get("variable_sip_from_user") or caller_id_number
        caller_ext = None
        if caller_user:
            caller_ext = await execute_query_one(
                """
                SELECT e.id, e.tenant_id, e.extension_number, e.outbound_caller_id, e.caller_id_name, e.caller_id_number
                FROM extensions e
                WHERE e.extension_number = :ext
                  AND e.enabled = true
                  AND e.deleted_at IS NULL
                LIMIT 1
                """,
                {"ext": caller_user}
            )
            if caller_ext and caller_ext.get("tenant_id"):
                tenant_id = str(caller_ext["tenant_id"])

        # Prevent unauthorized external callers in context 'public' from placing outbound PSTN calls
        # Only registered extensions belonging to a tenant are permitted to dial outbound PSTN
        if context == "public" and not caller_ext:
            logger.warning(
                f"Rejecting unauthenticated outbound call attempt from context 'public' by non-extension '{caller_user}' to '{dest_number}'"
            )
            ext_elem = SubElement(context_elem, "extension", name="reject_public_outbound")
            cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")
            SubElement(cond_elem, "action", application="respond", data="403 Forbidden - Outbound Not Allowed")
            SubElement(cond_elem, "action", application="hangup", data="CALL_REJECTED")
            return format_xml(doc)

        # Smart dialed digits formatting:
        # 1. If dialed as 91 + 10 digits (12 digits, Indian E.164 without +), preserve 91...
        # 2. If dialed with outside line prefix 9 followed by local trunk 0 (e.g. 9 + 09930573730 = 12 digits), strip leading 9 -> 09930573730
        # 3. If dialed with outside line prefix 9 followed by 10-digit number (11 digits, e.g. 9 + 9930573730), strip leading 9 -> 9930573730
        if dest_number.startswith("91") and len(dest_number) == 12:
            dialed_digits = dest_number
        elif dest_number.startswith("90") and len(dest_number) == 12:
            dialed_digits = dest_number[1:]
        elif dest_number.startswith("9") and len(dest_number) == 11:
            dialed_digits = dest_number[1:]
        elif dest_number.startswith("9") and len(dest_number) > 12:
            dialed_digits = dest_number[1:]
        else:
            dialed_digits = dest_number

        # 1. Check for specific outbound call route configured in call_routes for this tenant
        outbound_gw = None
        if tenant_id:
            outbound_gw = await execute_query_one(
                """
                SELECT cr.id, g.name as gateway_name, g.proxy, g.profile
                FROM call_routes cr
                JOIN gateways g ON cr.gateway_id = g.id
                WHERE cr.tenant_id = CAST(:tid AS uuid)
                  AND cr.route_type = 'outbound'
                  AND cr.enabled = true AND cr.deleted_at IS NULL
                  AND g.enabled = true AND g.deleted_at IS NULL
                ORDER BY cr.priority ASC LIMIT 1
                """,
                {"tid": tenant_id}
            )

        # 2. Check for assigned Sofia Gateways via tenant_gateways or direct tenant assignment
        if not outbound_gw and tenant_id:
            outbound_gw = await execute_query_one(
                """
                SELECT g.name as gateway_name, g.proxy, g.profile
                FROM gateways g
                LEFT JOIN tenant_gateways tg ON g.id = tg.gateway_id
                WHERE (
                    (tg.tenant_id = CAST(:tid AS uuid) AND tg.allow_outbound = true)
                    OR g.tenant_id = CAST(:tid AS uuid)
                )
                AND g.enabled = true AND g.deleted_at IS NULL
                ORDER BY COALESCE(tg.priority, 1) ASC
                LIMIT 1
                """,
                {"tid": tenant_id}
            )

        # 3. Check for assigned SIP trunks for this tenant
        trunk = None
        target_gw_name = outbound_gw["gateway_name"] if outbound_gw else None
        target_host = outbound_gw.get("proxy") if outbound_gw else None
        target_profile = outbound_gw.get("profile") if outbound_gw else None

        if not target_gw_name and tenant_id:
            trunk = await execute_query_one(
                """
                SELECT t.name, t.host, t.port FROM sip_trunks t
                LEFT JOIN tenant_sip_trunks tst ON t.id = tst.trunk_id
                WHERE (tst.tenant_id = CAST(:tid AS uuid) OR t.tenant_id = CAST(:tid AS uuid))
                  AND t.enabled = true AND t.deleted_at IS NULL
                ORDER BY t.priority ASC LIMIT 1
                """,
                {"tid": tenant_id}
            )
            if trunk:
                target_gw_name = trunk["name"]
                target_host = trunk["host"]
                target_port = trunk.get("port") or 5060
                if target_port != 5060 and ":" not in str(target_host):
                    target_host = f"{target_host}:{target_port}"

        # 4. Fallback to any enabled gateway in gateways table
        if not target_gw_name:
            any_gw = await execute_query_one(
                "SELECT name as gateway_name, proxy, profile FROM gateways WHERE enabled = true AND deleted_at IS NULL ORDER BY created_at ASC LIMIT 1"
            )
            if any_gw:
                target_gw_name = any_gw["gateway_name"]
                target_host = any_gw.get("proxy")
                target_profile = any_gw.get("profile")

        # If tenant has no gateway assigned: OUTBOUND CALLS MUST FAIL!
        if not target_gw_name:
            logger.warning(
                f"Outbound call rejected: Tenant '{tenant_id}' has no assigned gateway or outbound trunk. "
                f"Destination: {dialed_digits}"
            )
            ext_elem = SubElement(context_elem, "extension", name=f"reject_unassigned_gateway_{dest_number}")
            cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")
            SubElement(cond_elem, "action", application="set", data=f"tenant_id={tenant_id}")
            SubElement(cond_elem, "action", application="respond", data="403 Forbidden - No Gateway Assigned")
            SubElement(cond_elem, "action", application="hangup", data="CALL_REJECTED")
            return format_xml(doc)

        if not target_profile:
            name_lower = target_gw_name.lower()
            host_str = str(target_host or "")
            target_profile = "external-crossconnect" if ("_fl" in name_lower or "fixedline" in name_lower or host_str.startswith("10.50.")) else "external"

        ext_elem = SubElement(context_elem, "extension", name=f"outbound_{dest_number}")
        cond_elem = SubElement(ext_elem, "condition", field="destination_number", expression=".*")

        SubElement(cond_elem, "action", application="set", data=f"tenant_id={tenant_id}")
        SubElement(cond_elem, "action", application="set", data="direction=outbound")
        SubElement(cond_elem, "action", application="set", data="hangup_after_bridge=true")
        SubElement(cond_elem, "action", application="export", data="hangup_after_bridge=true")
        SubElement(cond_elem, "action", application="set", data="continue_on_fail=NORMAL_TEMPORARY_FAILURE,USER_BUSY,NO_ANSWER,TIMEOUT,NO_ROUTE_DESTINATION")

        # Record outbound call session
        if tenant_id:
            try:
                os.makedirs(f"/var/lib/freeswitch/recordings/{tenant_id}", exist_ok=True)
            except OSError:
                pass
        rec_path = f"/var/lib/freeswitch/recordings/{tenant_id}/${{uuid}}.wav"
        SubElement(cond_elem, "action", application="set", data=f"recording_file={rec_path}")
        SubElement(cond_elem, "action", application="record_session", data=rec_path)

        # Determine outbound Caller ID (prioritize extension outbound_caller_id, then tenant DID, then extension number)
        outbound_cli = caller_ext.get("outbound_caller_id") if caller_ext else None
        if not outbound_cli and tenant_id:
            did_row = await execute_query_one(
                "SELECT did_number FROM dids WHERE tenant_id = CAST(:tid AS uuid) AND enabled = true AND deleted_at IS NULL ORDER BY created_at ASC LIMIT 1",
                {"tid": tenant_id}
            )
            if did_row and did_row.get("did_number"):
                outbound_cli = did_row["did_number"]

        if outbound_cli:
            SubElement(cond_elem, "action", application="set", data=f"effective_caller_id_number={outbound_cli}")
            SubElement(cond_elem, "action", application="set", data=f"outbound_caller_id_number={outbound_cli}")
        else:
            SubElement(cond_elem, "action", application="set", data="effective_caller_id_number=${outbound_caller_id_number}")

        cli_name = (caller_ext.get("caller_id_name") or caller_id_name or caller_id_number) if caller_ext else (caller_id_name or caller_id_number)
        if cli_name:
            SubElement(cond_elem, "action", application="set", data=f"effective_caller_id_name={cli_name}")
            SubElement(cond_elem, "action", application="set", data=f"outbound_caller_id_name={cli_name}")
        else:
            SubElement(cond_elem, "action", application="set", data="effective_caller_id_name=${outbound_caller_id_name}")

        # Primary: bridge via registered Sofia gateway
        # Failover: if gateway registration is down/invalid, failover to direct SIP URI via target profile
        bridge_targets = [f"sofia/gateway/{target_gw_name}/{dialed_digits}"]
        if dialed_digits.startswith("+"):
            bridge_targets.append(f"sofia/gateway/{target_gw_name}/{dialed_digits.lstrip('+')}")
        if target_host:
            bridge_targets.append(f"sofia/{target_profile}/{dialed_digits}@{target_host}")
            if dialed_digits.startswith("+"):
                bridge_targets.append(f"sofia/{target_profile}/{dialed_digits.lstrip('+')}@{target_host}")
        
        bridge_data = "|".join(bridge_targets)
        logger.info(f"Outbound route bridge data: '{bridge_data}'")
        SubElement(cond_elem, "action", application="bridge", data=bridge_data)
        return format_xml(doc)

    logger.warning(f"No dialplan route matched for dest='{dest_number}' in context='{context}'")
    return NOT_FOUND_XML


def add_voicemail_block(cond_elem: Element, ext_num: str, domain: str, tenant_id: str, custom_greeting_path: Optional[str] = None):
    """
    Appends full Voicemail execution actions:
    1. Answers the channel
    2. Sets channel variables for CDR fallback logging
    3. Plays custom or synthesized 'User not available, please leave a voicemail' greeting
    4. Plays 1000Hz beep tone
    5. Records caller's audio message to disk
    6. Calls API webhook to record to database and queue email dispatch
    7. Hangs up cleanly
    """
    # Ensure voicemail & recording directories exist so mod_sndfile never fails to create recording file
    try:
        os.makedirs("/var/lib/freeswitch/recordings/voicemail", exist_ok=True)
        os.makedirs("/var/lib/freeswitch/recordings/prompts", exist_ok=True)
        if tenant_id:
            os.makedirs(f"/var/lib/freeswitch/recordings/{tenant_id}", exist_ok=True)
    except OSError:
        pass

    vm_file = f"/var/lib/freeswitch/recordings/voicemail/{ext_num}_${{uuid}}.wav"
    rec_path = f"/var/lib/freeswitch/recordings/{tenant_id}/${{uuid}}.wav"

    SubElement(cond_elem, "action", application="stop_record_session", data=rec_path)
    SubElement(cond_elem, "action", application="answer")
    SubElement(cond_elem, "action", application="sleep", data="1000")
    SubElement(cond_elem, "action", application="set", data=f"voicemail_target={ext_num}")
    SubElement(cond_elem, "action", application="set", data=f"voicemail_file={vm_file}")
    SubElement(cond_elem, "action", application="set", data="playback_terminators=#*")

    # Configure hangup hook so that if caller hangs up during or after speaking, the email webhook always triggers
    curl_url = f"http://api:8000/freeswitch/voicemail?extension_number={ext_num}&caller_id_number=${{caller_id_number}}&caller_id_name=${{caller_id_name}}&file_path={vm_file}&duration=15&tenant_id={tenant_id}"
    SubElement(cond_elem, "action", application="set", data=f"api_hangup_hook=curl {curl_url} post")
    SubElement(cond_elem, "action", application="set", data="session_in_hangup_hook=true")

    # Resolve greeting audio prompt: custom uploaded audio file or synthesized dynamic prompt
    if custom_greeting_path and os.path.exists(custom_greeting_path):
        prompt_path = custom_greeting_path
    else:
        prompt_path = ensure_voicemail_prompt(ext_num)

    SubElement(cond_elem, "action", application="playback", data=prompt_path)
    # Play Beep Tone (native FreeSWITCH 1000Hz tone for 500ms)
    SubElement(cond_elem, "action", application="playback", data="tone_stream://%(500,0,1000)")
    # Record message: max 120s, threshold 200, silence terminate 4s
    SubElement(cond_elem, "action", application="record", data=f"{vm_file} 120 200 4")
    # Trigger webhook directly if caller completed record via # or silence without hanging up
    SubElement(
        cond_elem,
        "action",
        application="curl",
        data=f"{curl_url} post"
    )
    # Clear api_hangup_hook to avoid double invocation on final hangup
    SubElement(cond_elem, "action", application="set", data="api_hangup_hook=")
    SubElement(cond_elem, "action", application="hangup")


def format_xml(doc: Element) -> str:
    xml_header = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    return xml_header + tostring(doc, encoding="utf-8").decode("utf-8")
