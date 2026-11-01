import logging
from xml.etree.ElementTree import Element, SubElement, tostring
from typing import Dict, Optional, Any
from src.core.database import execute_query, execute_query_one
from src.core.config import settings

logger = logging.getLogger("pbx.xml_curl.directory")

NOT_FOUND_XML = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<document type="freeswitch/xml">
  <section name="result">
    <result status="not found"/>
  </section>
</document>"""


async def handle_directory_request(form_data: Dict[str, Any]) -> str:
    """
    Handles section=directory XML-CURL requests from FreeSWITCH.
    Dynamically generates SIP authentication directory XML based on PostgreSQL tenant & extension data.
    """
    domain_name = form_data.get("domain") or form_data.get("key_value") or form_data.get("SIP-Auth-Realm")
    user_id = form_data.get("user") or form_data.get("SIP-Auth-User")

    # Fix Zoiper "double-@" bug: when outbound proxy is set separately, Zoiper sends
    # user="1001@pbx.aikyamlabs.local" and domain="127.0.0.1". Strip the embedded domain
    # from user_id so auth lookup works correctly.
    if user_id and "@" in user_id:
        embedded_user, embedded_domain = user_id.split("@", 1)
        logger.info(f"mod_xml_curl Directory: stripping embedded domain from user: '{user_id}' -> '{embedded_user}' (domain hint: '{embedded_domain}')")
        # If domain_name is an IP and embedded_domain is a real FQDN, prefer the FQDN
        if domain_name and (domain_name == "127.0.0.1" or domain_name == "localhost") and "." in embedded_domain and not embedded_domain.replace(".", "").isdigit():
            domain_name = embedded_domain
        user_id = embedded_user

    logger.info(f"mod_xml_curl Directory request: domain='{domain_name}', user='{user_id}'")

    if not domain_name:
        return NOT_FOUND_XML

    # 1. Lookup Tenant by sip_domain or domain
    tenant = await execute_query_one(
        "SELECT id, name, domain, sip_domain FROM tenants WHERE (sip_domain = :d OR domain = :d) AND enabled = true AND deleted_at IS NULL LIMIT 1",
        {"d": domain_name}
    )

    # Fallback to active tenant if registered via PBX host/IP or localhost
    if not tenant:
        tenant = await execute_query_one(
            "SELECT id, name, domain, sip_domain FROM tenants WHERE enabled = true AND deleted_at IS NULL ORDER BY created_at ASC LIMIT 1"
        )

    # Fallback to first active tenant if still not found
    if not tenant:
        tenant = await execute_query_one(
            "SELECT id, name, domain, sip_domain FROM tenants WHERE enabled = true AND deleted_at IS NULL ORDER BY created_at ASC LIMIT 1"
        )

    if not tenant:
        logger.warning(f"mod_xml_curl Directory: Tenant not found for domain '{domain_name}'")
        return NOT_FOUND_XML

    tenant_id = str(tenant["id"])
    sip_domain = tenant["sip_domain"]

    # 2. Fetch extension(s) with voicemail configuration
    if user_id:
        extensions = await execute_query(
            """
            SELECT e.extension_number, e.display_name, e.email, e.sip_password, e.voicemail_pin,
                   e.caller_id_name, e.caller_id_number, e.outbound_caller_id, e.enabled,
                   COALESCE(e.is_virtual, false) as is_virtual,
                   vb.password as vb_password, vb.email_notification, vb.email_attach_file,
                   vb.email_address as vb_email, vb.delete_after_email
            FROM extensions e
            LEFT JOIN voicemail_boxes vb ON e.id = vb.extension_id AND vb.tenant_id = e.tenant_id
            WHERE e.tenant_id = :tenant_id AND e.extension_number = :user_id AND e.enabled = true AND e.deleted_at IS NULL
            """,
            {"tenant_id": tenant_id, "user_id": user_id}
        )
    else:
        # Fetch all active extensions for this domain
        extensions = await execute_query(
            """
            SELECT e.extension_number, e.display_name, e.email, e.sip_password, e.voicemail_pin,
                   e.caller_id_name, e.caller_id_number, e.outbound_caller_id, e.enabled,
                   COALESCE(e.is_virtual, false) as is_virtual,
                   vb.password as vb_password, vb.email_notification, vb.email_attach_file,
                   vb.email_address as vb_email, vb.delete_after_email
            FROM extensions e
            LEFT JOIN voicemail_boxes vb ON e.id = vb.extension_id AND vb.tenant_id = e.tenant_id
            WHERE e.tenant_id = :tenant_id AND e.enabled = true AND e.deleted_at IS NULL
            """,
            {"tenant_id": tenant_id}
        )

    if not extensions:
        logger.warning(f"mod_xml_curl Directory: No active extension found for user='{user_id}' in tenant '{tenant_id}'")
        return NOT_FOUND_XML

    # 3. Construct XML Document
    doc = Element("document", type="freeswitch/xml")
    section = SubElement(doc, "section", name="directory")

    # Support queried domain, tenant sip_domain, and PBX server host aliases
    aliases = [domain_name, sip_domain, "pbx.terrixai.com", "8.234.117.15", "127.0.0.1"]
    domains_to_create = list(dict.fromkeys([a for a in aliases if a]))

    # Standard Sofia dial-string allows FreeSWITCH to locate the live Sofia registration contact
    sofia_dial_string = "{presence_id=${dialed_user}@${dialed_domain}}${sofia_contact(${dialed_user}@${dialed_domain})}"

    for d_name in domains_to_create:
        domain_elem = SubElement(section, "domain", name=d_name)

        # Domain level params
        d_params = SubElement(domain_elem, "params")
        has_only_virtual = all(ext.get("is_virtual") for ext in extensions)
        if has_only_virtual:
            SubElement(d_params, "param", name="dial-string", value="loopback/park")
        else:
            SubElement(d_params, "param", name="dial-string", value=sofia_dial_string)

        groups = SubElement(domain_elem, "groups")
        group = SubElement(groups, "group", name="default")
        users = SubElement(group, "users")

        for ext in extensions:
            ext_num = ext["extension_number"]
            sip_pwd = ext["sip_password"]
            cid_name = ext.get("caller_id_name") or ext.get("display_name") or ext_num
            cid_num = ext.get("caller_id_number") or ext_num
            outbound_cli = ext.get("outbound_caller_id") or cid_num
            email_str = ext.get("email") or ""
            is_virtual = bool(ext.get("is_virtual"))

            u_elem = SubElement(users, "user", id=ext_num)

            u_params = SubElement(u_elem, "params")
            if is_virtual:
                # Virtual QA extension: reject SIP registration and loopback to park
                SubElement(u_params, "param", name="dial-string", value="loopback/park")
            else:
                # User Parameters (Password & Auth & Voicemail)
                vm_pin = ext.get("vb_password") or ext.get("voicemail_pin") or "1234"
                vm_email = ext.get("vb_email") or ext.get("email") or ""
                keep_local = "false" if ext.get("delete_after_email") else "true"

                SubElement(u_params, "param", name="password", value=sip_pwd)
                SubElement(u_params, "param", name="vm-password", value=vm_pin)
                SubElement(u_params, "param", name="vm-enabled", value="true")
                SubElement(u_params, "param", name="vm-email-all-messages", value="true")
                SubElement(u_params, "param", name="vm-attach-file", value="true")
                SubElement(u_params, "param", name="vm-keep-local-after-email", value=keep_local)
                if vm_email:
                    SubElement(u_params, "param", name="vm-mailto", value=vm_email)

            # User Dialplan Variables
            u_vars = SubElement(u_elem, "variables")
            # Enforce strict multi-tenant dialplan context isolation
            SubElement(u_vars, "variable", name="user_context", value=f"tenant-{tenant_id}")
            SubElement(u_vars, "variable", name="effective_caller_id_name", value=cid_name)
            SubElement(u_vars, "variable", name="effective_caller_id_number", value=cid_num)
            SubElement(u_vars, "variable", name="outbound_caller_id_name", value=cid_name)
            SubElement(u_vars, "variable", name="outbound_caller_id_number", value=outbound_cli)
            SubElement(u_vars, "variable", name="accountcode", value=tenant_id)
            SubElement(u_vars, "variable", name="tenant_id", value=tenant_id)
            SubElement(u_vars, "variable", name="user_email", value=email_str)

    xml_header = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    return xml_header + tostring(doc, encoding="utf-8").decode("utf-8")
