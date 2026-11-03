import logging
import json
import os
import re
import asyncio
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, Request, Response
from src.xml_curl.directory import handle_directory_request
from src.xml_curl.dialplan import handle_dialplan_request
from src.core.database import execute_query_one, execute_query
from src.core.email_service import send_voicemail_notification
from src.core.websocket_manager import ws_manager

logger = logging.getLogger("pbx.xml_curl.router")

router = APIRouter(prefix="/freeswitch", tags=["FreeSWITCH mod_xml_curl"])

NOT_FOUND_XML = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<document type="freeswitch/xml">
  <section name="result">
    <result status="not found"/>
  </section>
</document>"""

XML_CDR_CONF_XML = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<document type="freeswitch/xml">
  <section name="configuration" description="XML CDR Module">
    <configuration name="xml_cdr.conf" description="XML CDR Module">
      <settings>
        <param name="url" value="http://api:8000/freeswitch/cdr"/>
        <param name="retries" value="3"/>
        <param name="delay" value="5"/>
        <param name="log-b-leg" value="false"/>
        <param name="prefix-a-leg" value="false"/>
        <param name="encode" value="true"/>
      </settings>
    </configuration>
  </section>
</document>"""

JSON_CDR_CONF_XML = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<document type="freeswitch/xml">
  <section name="configuration" description="JSON CDR Module">
    <configuration name="json_cdr.conf" description="JSON CDR Module">
      <settings>
        <param name="url" value="http://api:8000/freeswitch/cdr"/>
        <param name="delay" value="0"/>
        <param name="retries" value="2"/>
        <param name="log-b-leg" value="false"/>
        <param name="prefix-a-leg" value="false"/>
        <param name="encode" value="true"/>
      </settings>
    </configuration>
  </section>
</document>"""

VOICEMAIL_CONF_XML = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<document type="freeswitch/xml">
  <section name="configuration" description="Voicemail Module">
    <configuration name="voicemail.conf" description="Voicemail Module">
      <settings>
        <param name="file-extension" value="wav"/>
        <param name="record-space" value="wav"/>
        <param name="max-record-len" value="300"/>
        <param name="max-retries" value="3"/>
        <param name="tone-spec" value="%(1000, 0, 640)"/>
        <param name="digit-timeout" value="10000"/>
        <param name="max-login-attempts" value="3"/>
        <param name="record-silence-hits" value="2"/>
        <param name="record-silence-threshold" value="200"/>
      </settings>
      <profiles>
        <profile name="default">
          <param name="file-extension" value="wav"/>
          <param name="record-space" value="wav"/>
          <param name="max-record-len" value="300"/>
          <param name="max-retries" value="3"/>
          <param name="tone-spec" value="%(1000, 0, 640)"/>
          <param name="digit-timeout" value="10000"/>
          <param name="max-login-attempts" value="3"/>
          <param name="record-silence-hits" value="2"/>
          <param name="record-silence-threshold" value="200"/>
        </profile>
      </profiles>
    </configuration>
  </section>
</document>"""

CURL_CONF_XML = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<document type="freeswitch/xml">
  <section name="configuration" description="CURL Module">
    <configuration name="curl.conf" description="CURL Module">
      <settings>
        <param name="gateway-url" value="http://api:8000/freeswitch/"/>
        <param name="gateway-credentials" value="none"/>
      </settings>
    </configuration>
  </section>
</document>"""


@router.post("/xml")
async def handle_xml_curl(request: Request):
    """
    Main HTTP POST handler for FreeSWITCH mod_xml_curl integration.
    FreeSWITCH sends form-encoded parameter requests for directory, dialplan, configuration, etc.
    """
    try:
        form_data = await request.form()
        form_dict = {k: v for k, v in form_data.items()}

        section = form_dict.get("section")
        key_name = form_dict.get("key_name")
        key_value = form_dict.get("key_value")

        logger.info(f"mod_xml_curl request received: section='{section}', key_name='{key_name}', key_value='{key_value}'")

        if section == "directory":
            xml_response = await handle_directory_request(form_dict)
            return Response(content=xml_response, media_type="text/xml")
        elif section == "dialplan":
            xml_response = await handle_dialplan_request(form_dict)
            return Response(content=xml_response, media_type="text/xml")
        elif section == "configuration":
            if key_value == "callcenter.conf":
                from src.xml_curl.configuration import generate_callcenter_conf_xml
                xml_content = await generate_callcenter_conf_xml()
                return Response(content=xml_content, media_type="text/xml")
            elif key_value == "conference.conf":
                from src.xml_curl.configuration import generate_conference_conf_xml
                return Response(content=generate_conference_conf_xml(), media_type="text/xml")
            elif key_value == "json_cdr.conf":
                return Response(content=JSON_CDR_CONF_XML, media_type="text/xml")
            elif key_value in ("xml_cdr.conf", "mod_xml_cdr.conf"):
                return Response(content=XML_CDR_CONF_XML, media_type="text/xml")
            elif key_value == "voicemail.conf":
                return Response(content=VOICEMAIL_CONF_XML, media_type="text/xml")
            elif key_value == "curl.conf":
                return Response(content=CURL_CONF_XML, media_type="text/xml")
            return Response(content=NOT_FOUND_XML, media_type="text/xml")

        logger.debug(f"Unhandled mod_xml_curl section: '{section}'")
        return Response(content=NOT_FOUND_XML, media_type="text/xml")

    except Exception as exc:
        logger.error(f"Error handling mod_xml_curl request: {exc}", exc_info=True)
        return Response(content=NOT_FOUND_XML, media_type="text/xml")


def parse_xml_cdr_to_dict(xml_content: str) -> dict:
    """
    Parses FreeSWITCH mod_xml_cdr XML string into a structured dict with 'variables' and 'callflow'.
    """
    root = ET.fromstring(xml_content.strip())
    variables = {}
    var_elem = root.find("variables")
    if var_elem is not None:
        for child in var_elem:
            tag = child.tag
            text = child.text or ""
            variables[tag] = text

    callflow = []
    for cf in root.findall("callflow"):
        profile = {}
        prof_elem = cf.find("caller_profile")
        if prof_elem is not None:
            for child in prof_elem:
                profile[child.tag] = child.text or ""
        times = {}
        times_elem = cf.find("times")
        if times_elem is not None:
            for child in times_elem:
                times[child.tag] = child.text or ""
        callflow.append({"caller_profile": profile, "times": times})

    return {
        "variables": variables,
        "callflow": callflow,
        "core_uuid": root.attrib.get("core-uuid", "")
    }


def determine_call_direction(variables: dict, caller_number: str, destination: str) -> str:
    """
    Accurately classifies call direction into: 'inbound', 'outbound', or 'internal'.
    """
    explicit_dir = (variables.get("direction") or variables.get("call_direction") or "").lower()
    if explicit_dir in ("inbound", "outbound", "internal"):
        return explicit_dir

    clean_caller = str(caller_number or "").replace("+", "").strip()
    clean_dest = str(destination or "").replace("+", "").strip()

    is_caller_ext = bool(clean_caller and len(clean_caller) <= 6 and clean_caller.isdigit())
    is_dest_ext = bool(clean_dest and len(clean_dest) <= 6 and (clean_dest.isdigit() or clean_dest.startswith("ext/")))

    if is_caller_ext and is_dest_ext:
        return "internal"
    elif is_caller_ext and not is_dest_ext:
        return "outbound"
    elif not is_caller_ext and is_dest_ext:
        return "inbound"

    return "internal"


async def process_cdr_record(cdr_data: dict) -> dict:
    """
    Processes a structured CDR dictionary (from either mod_xml_cdr or mod_json_cdr),
    registers recordings, saves to public.cdr, and returns the processed dict.
    """
    variables = cdr_data.get("variables", {})
    callflow_list = cdr_data.get("callflow", [])
    callflow = callflow_list[0] if callflow_list else {}
    caller_profile = callflow.get("caller_profile", {})
    times = callflow.get("times", {})

    call_uuid = variables.get("uuid") or str(uuid4())
    caller_number = variables.get("caller_id_number") or caller_profile.get("caller_id_number") or ""
    caller_name = variables.get("caller_id_name") or caller_profile.get("caller_id_name") or caller_number
    destination = variables.get("destination_number") or caller_profile.get("destination_number") or ""
    direction = determine_call_direction(variables, caller_number, destination)

    # Determine Tenant ID
    tenant_id = variables.get("tenant_id") or variables.get("accountcode")
    if not tenant_id:
        domain = variables.get("domain_name") or variables.get("sip_req_host")
        if domain:
            t_row = await execute_query_one(
                "SELECT id FROM tenants WHERE (sip_domain = :d OR domain = :d) AND enabled = true LIMIT 1",
                {"d": domain}
            )
            if t_row:
                tenant_id = str(t_row["id"])

    # Fallback to DID match if destination matches a configured tenant DID
    if not tenant_id and destination:
        did_match = await execute_query_one(
            """
            SELECT tenant_id FROM dids 
            WHERE (did_number = :dest OR did_number = '+' || :dest OR did_number = TRIM(LEADING '+' FROM :dest))
              AND enabled = true AND deleted_at IS NULL LIMIT 1
            """,
            {"dest": destination}
        )
        if did_match and did_match.get("tenant_id"):
            tenant_id = str(did_match["tenant_id"])

    # Fallback to Extension match if caller is a known extension
    if not tenant_id and caller_number:
        ext_match = await execute_query_one(
            "SELECT tenant_id FROM extensions WHERE extension_number = :ext AND enabled = true AND deleted_at IS NULL LIMIT 1",
            {"ext": caller_number}
        )
        if ext_match and ext_match.get("tenant_id"):
            tenant_id = str(ext_match["tenant_id"])

    # Drop unauthenticated scanner attacks that have no matching tenant or DID
    if not tenant_id:
        logger.info(f"CDR: Ignoring unauthenticated external probe {call_uuid} to '{destination}'")
        return {"status": "ignored", "reason": "no_tenant_probe", "uuid": call_uuid}

    # Parse Timestamps
    start_epoch = int(times.get("created_time") or variables.get("start_epoch") or 0)
    answer_epoch = int(times.get("answered_time") or variables.get("answer_epoch") or 0)
    end_epoch = int(times.get("hangup_time") or variables.get("end_epoch") or 0)

    # FreeSWITCH times may be in microseconds
    if start_epoch > 1e11:
        start_epoch //= 1000000
    if answer_epoch > 1e11:
        answer_epoch //= 1000000
    if end_epoch > 1e11:
        end_epoch //= 1000000

    now = datetime.now(timezone.utc)
    start_time = datetime.fromtimestamp(start_epoch, tz=timezone.utc) if start_epoch > 0 else now
    answer_time = datetime.fromtimestamp(answer_epoch, tz=timezone.utc) if answer_epoch > 0 else None
    end_time = datetime.fromtimestamp(end_epoch, tz=timezone.utc) if end_epoch > 0 else now

    duration = int(variables.get("duration") or (end_epoch - start_epoch if end_epoch > start_epoch else 0))
    billsec = int(variables.get("billsec") or (end_epoch - answer_epoch if answer_epoch > 0 and end_epoch > answer_epoch else 0))
    hangup_cause = variables.get("hangup_cause") or "NORMAL_CLEARING"
    hangup_code = int(variables.get("hangup_cause_q850") or 16)

    # Check for Voice Call Recording file
    recording_id = None
    raw_rec_file = variables.get("recording_file") or f"/var/lib/freeswitch/recordings/{tenant_id}/{call_uuid}.wav"
    expanded_rec = raw_rec_file.replace("${uuid}", call_uuid)

    candidate_files = [
        expanded_rec,
        f"/var/lib/freeswitch/recordings/{tenant_id}/{call_uuid}.wav",
        f"/var/lib/freeswitch/recordings/{call_uuid}.wav",
        f"/var/lib/freeswitch/recordings/archive/{call_uuid}.wav",
        f"/var/lib/freeswitch/recordings/voicemail/{call_uuid}.wav",
        raw_rec_file
    ]

    matched_file = None
    for cand in candidate_files:
        if cand and os.path.exists(cand) and os.path.getsize(cand) > 0:
            matched_file = cand
            break

    if matched_file:
        file_size = os.path.getsize(matched_file)
        recording_id = str(uuid4())
        await execute_query(
            """
            INSERT INTO recordings (id, tenant_id, call_uuid, source_extension, caller_number, destination_number,
                                    direction, start_time, end_time, duration, file_path, storage_provider, file_format, file_size)
            VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :call_uuid, :src_ext, :caller_num, :dest_num,
                    :dir, :start_time, :end_time, :duration, :file_path, 'local', 'wav', :file_size)
            ON CONFLICT (id) DO NOTHING
            """,
            {
                "id": recording_id,
                "tid": tenant_id,
                "call_uuid": call_uuid,
                "src_ext": caller_number,
                "caller_num": caller_number,
                "dest_num": destination,
                "dir": direction,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "file_path": matched_file,
                "file_size": file_size
            }
        )
        logger.info(f"Saved recording {recording_id} for call {call_uuid} from {matched_file} (size: {file_size} bytes)")

    # Insert CDR
    await execute_query(
        """
        INSERT INTO cdr (tenant_id, call_uuid, direction, caller_number, caller_name, destination,
                         source_extension, destination_extension, start_time, answer_time, end_time,
                         duration, billsec, hangup_cause, hangup_code, recording_id)
        VALUES (CAST(:tid AS uuid), :call_uuid, :dir, :caller_num, :caller_name, :dest,
                :src_ext, :dest_ext, :start_time, :answer_time, :end_time,
                :duration, :billsec, :hangup_cause, :hangup_code, CAST(:rec_id AS uuid))
        ON CONFLICT (call_uuid) DO UPDATE SET
            end_time = EXCLUDED.end_time,
            duration = EXCLUDED.duration,
            billsec = EXCLUDED.billsec,
            hangup_cause = EXCLUDED.hangup_cause,
            recording_id = COALESCE(cdr.recording_id, EXCLUDED.recording_id)
        """,
        {
            "tid": tenant_id,
            "call_uuid": call_uuid,
            "dir": direction,
            "caller_num": caller_number,
            "caller_name": caller_name,
            "dest": destination,
            "src_ext": caller_number,
            "dest_ext": destination,
            "start_time": start_time,
            "answer_time": answer_time,
            "end_time": end_time,
            "duration": duration,
            "billsec": billsec,
            "hangup_cause": hangup_cause,
            "hangup_code": hangup_code,
            "rec_id": recording_id
        }
    )
    logger.info(f"Recorded CDR for call_uuid={call_uuid} (duration={duration}s, billsec={billsec}s, dir={direction})")

    # Check for Voicemail Recording file
    vm_file = variables.get("voicemail_file")
    vm_target = variables.get("voicemail_target") or destination
    if not vm_file:
        cand_vm = f"/var/lib/freeswitch/recordings/voicemail/{vm_target}_{call_uuid}.wav"
        if os.path.exists(cand_vm):
            vm_file = cand_vm

    if vm_file and os.path.exists(vm_file) and os.path.getsize(vm_file) > 1000:
        try:
            vm_size = os.path.getsize(vm_file)
            vm_dur = int(variables.get("billsec") or variables.get("duration") or 5)
            saved = await execute_query_one(
                "SELECT id FROM voicemail_messages WHERE file_path = :fp",
                {"fp": vm_file}
            )
            if not saved:
                ext_row = await execute_query_one(
                    """
                    SELECT id, email, display_name
                    FROM extensions
                    WHERE extension_number = :ext AND tenant_id = CAST(:tid AS uuid)
                    """,
                    {"ext": vm_target, "tid": tenant_id}
                )
                ext_id = str(ext_row["id"]) if ext_row else None
                recip_email = ext_row.get("email") if ext_row else "alice@acme.com"

                vm_box = None
                if ext_id:
                    vm_box = await execute_query_one(
                        "SELECT id FROM voicemail_boxes WHERE extension_id = CAST(:ext_id AS uuid)",
                        {"ext_id": ext_id}
                    )
                if not vm_box and ext_id:
                    vm_box_id = str(uuid4())
                    await execute_query(
                        """
                        INSERT INTO voicemail_boxes (id, tenant_id, extension_id, mailbox, email_notification, email_address)
                        VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:ext_id AS uuid), :mailbox, true, :email)
                        """,
                        {"id": vm_box_id, "tid": tenant_id, "ext_id": ext_id, "mailbox": vm_target, "email": recip_email}
                    )
                elif vm_box:
                    vm_box_id = str(vm_box["id"])
                else:
                    vm_box_id = str(uuid4())

                vm_msg_id = str(uuid4())
                await execute_query(
                    """
                    INSERT INTO voicemail_messages (id, voicemail_box_id, caller_id_name, caller_id_number, file_path, duration)
                    VALUES (CAST(:id AS uuid), CAST(:vbox_id AS uuid), :cname, :cnum, :fpath, :dur)
                    """,
                    {"id": vm_msg_id, "vbox_id": vm_box_id, "cname": caller_name, "cnum": caller_number, "fpath": vm_file, "dur": vm_dur}
                )
                logger.info(f"Voicemail successfully captured via CDR: id={vm_msg_id}, target={vm_target}, caller={caller_number}, size={vm_size} bytes")

                asyncio.create_task(
                    send_voicemail_notification(
                        extension_number=vm_target,
                        caller_id_number=caller_number,
                        caller_id_name=caller_name,
                        file_path=vm_file,
                        duration=vm_dur,
                        tenant_id=tenant_id
                    )
                )
        except Exception as vm_exc:
            logger.warning(f"Error saving voicemail from CDR: {vm_exc}")

    processed = {
        "call_uuid": call_uuid,
        "tenant_id": tenant_id,
        "direction": direction,
        "caller_number": caller_number,
        "caller_name": caller_name,
        "destination": destination,
        "source_extension": caller_number,
        "destination_extension": destination,
        "start_time": start_time.isoformat() if start_time else None,
        "answer_time": answer_time.isoformat() if answer_time else None,
        "end_time": end_time.isoformat() if end_time else None,
        "duration": duration,
        "billsec": billsec,
        "hangup_cause": hangup_cause,
        "hangup_code": hangup_code,
        "recording_id": recording_id
    }

    try:
        from src.core.websocket_manager import ws_manager
        asyncio.create_task(ws_manager.broadcast_cdr_created(processed))
    except Exception as ws_err:
        logger.warning(f"Failed to broadcast CDR over websocket: {ws_err}")

    return processed


@router.post("/cdr")
@router.post("/xml-cdr")
async def handle_cdr(request: Request):
    """
    HTTP POST handler for FreeSWITCH mod_xml_cdr and mod_json_cdr.
    Parses completed call XML or JSON and writes records to public.cdr and public.recordings.
    """
    try:
        body = await request.body()
        if not body:
            return Response(content="OK", media_type="text/plain")

        decoded = body.decode("utf-8", errors="ignore").strip()
        if decoded.startswith("cdr="):
            raw_str = urllib.parse.unquote_plus(decoded[4:]).strip()
        elif "&cdr=" in decoded or "cdr=" in decoded:
            parsed = urllib.parse.parse_qs(decoded)
            if "cdr" in parsed and parsed["cdr"]:
                raw_str = parsed["cdr"][0].strip()
            else:
                raw_str = decoded
        else:
            raw_str = decoded

        if raw_str.startswith("<?xml") or raw_str.startswith("<cdr"):
            cdr_data = parse_xml_cdr_to_dict(raw_str)
        else:
            try:
                cdr_data = json.loads(raw_str, strict=False)
            except Exception:
                try:
                    unquoted = urllib.parse.unquote_plus(raw_str)
                    cdr_data = json.loads(unquoted, strict=False)
                except Exception:
                    sanitized = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", raw_str)
                    cdr_data = json.loads(sanitized, strict=False)

        await process_cdr_record(cdr_data)
        return Response(content="OK", media_type="text/plain")
    except Exception as exc:
        logger.warning(f"Could not parse CDR callback payload: {exc}")
        return Response(content="OK", media_type="text/plain")


@router.post("/voicemail")
async def handle_voicemail_webhook(request: Request):
    """
    Webhook called when a caller leaves a voicemail recording.
    Saves record to voicemail_messages and dispatches an email notification.
    """
    try:
        data = {}
        try:
            data = await request.json()
        except Exception:
            pass
        if not data:
            data = dict(request.query_params)
        if not data:
            form = await request.form()
            data = dict(form)
        extension_number = data.get("extension_number")
        caller_id_number = data.get("caller_id_number", "Unknown")
        caller_id_name = data.get("caller_id_name") or caller_id_number
        file_path = data.get("file_path")
        duration = int(data.get("duration") or 0)
        tenant_id = data.get("tenant_id")

        logger.info(f"Voicemail webhook received: ext={extension_number}, caller={caller_id_number}, file={file_path}")

        # 1. Lookup Extension and Email
        if tenant_id:
            ext_row = await execute_query_one(
                """
                SELECT e.id, e.email, e.display_name, e.tenant_id, t.name as tenant_name
                FROM extensions e
                JOIN tenants t ON e.tenant_id = t.id
                WHERE e.extension_number = :ext AND e.tenant_id = CAST(:tid AS uuid)
                """,
                {"ext": extension_number, "tid": tenant_id}
            )
        else:
            ext_row = await execute_query_one(
                """
                SELECT e.id, e.email, e.display_name, e.tenant_id, t.name as tenant_name
                FROM extensions e
                JOIN tenants t ON e.tenant_id = t.id
                WHERE e.extension_number = :ext
                ORDER BY e.created_at ASC
                LIMIT 1
                """,
                {"ext": extension_number}
            )

        if not ext_row:
            logger.warning(f"Voicemail webhook aborted: extension {extension_number} not found in database.")
            return {"status": "error", "detail": f"Extension {extension_number} not found"}

        ext_id = str(ext_row["id"])
        effective_tenant_id = str(ext_row.get("tenant_id") or tenant_id or "")
        recipient_email = ext_row.get("email") or "voicemail@pbx.local"

        # 2. Get or Create Voicemail Box
        vm_box = await execute_query_one(
            """
            SELECT id FROM voicemail_boxes 
            WHERE extension_id = CAST(:ext_id AS uuid)
               OR (tenant_id = CAST(:tid AS uuid) AND mailbox = :mailbox)
            LIMIT 1
            """,
            {"ext_id": ext_id, "tid": effective_tenant_id, "mailbox": extension_number}
        )
        if not vm_box:
            vm_box_id = str(uuid4())
            await execute_query(
                """
                INSERT INTO voicemail_boxes (id, tenant_id, extension_id, mailbox, email_notification, email_address)
                VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:ext_id AS uuid), :mailbox, true, :email)
                ON CONFLICT (tenant_id, mailbox) DO UPDATE 
                SET extension_id = EXCLUDED.extension_id, email_address = COALESCE(EXCLUDED.email_address, voicemail_boxes.email_address)
                """,
                {"id": vm_box_id, "tid": effective_tenant_id, "ext_id": ext_id, "mailbox": extension_number, "email": recipient_email}
            )
            vm_box = await execute_query_one(
                "SELECT id FROM voicemail_boxes WHERE tenant_id = CAST(:tid AS uuid) AND mailbox = :mailbox",
                {"tid": effective_tenant_id, "mailbox": extension_number}
            )
            vm_box_id = str(vm_box["id"]) if vm_box else vm_box_id
        else:
            vm_box_id = str(vm_box["id"])
            if str(vm_box.get("extension_id")) != ext_id:
                await execute_query(
                    "UPDATE voicemail_boxes SET extension_id = CAST(:ext_id AS uuid) WHERE id = CAST(:id AS uuid)",
                    {"ext_id": ext_id, "id": vm_box_id}
                )

        # 3. Insert into voicemail_messages
        msg_id = str(uuid4())
        await execute_query(
            """
            INSERT INTO voicemail_messages (id, voicemail_box_id, caller_id_name, caller_id_number, file_path, duration)
            VALUES (CAST(:id AS uuid), CAST(:vbox_id AS uuid), :cname, :cnum, :fpath, :dur)
            """,
            {"id": msg_id, "vbox_id": vm_box_id, "cname": caller_id_name, "cnum": caller_id_number, "fpath": file_path, "dur": duration}
        )

        # Asynchronously dispatch voicemail email notification
        asyncio.create_task(
            send_voicemail_notification(
                extension_number=extension_number,
                caller_id_number=caller_id_number,
                caller_id_name=caller_id_name,
                file_path=file_path,
                duration=duration,
                tenant_id=effective_tenant_id
            )
        )

        # Broadcast real-time websocket event
        asyncio.create_task(
            ws_manager.broadcast_voicemail_created({
                "message_id": msg_id,
                "extension_number": extension_number,
                "caller_id_name": caller_id_name,
                "caller_id_number": caller_id_number,
                "duration": duration,
                "tenant_id": effective_tenant_id
            })
        )

        logger.info(
            f" [VOICEMAIL NOTIFICATION DISPATCHED] To: {recipient_email} | Subject: New Voicemail from {caller_id_number} "
            f"| Duration: {duration}s | Attachment: {file_path}"
        )
        return {"status": "success", "message_id": msg_id, "email_sent_to": recipient_email}
    except Exception as exc:
        logger.error(f"Error saving voicemail webhook: {exc}", exc_info=True)
        return {"status": "error", "detail": str(exc)}
