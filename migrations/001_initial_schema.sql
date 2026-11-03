--
-- PostgreSQL database dump
--


-- Dumped from database version 14.24
-- Dumped by pg_dump version 14.24

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: announcements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.announcements (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    name character varying(100) NOT NULL,
    file_path character varying(500) NOT NULL,
    file_size bigint DEFAULT 0,
    duration integer DEFAULT 0,
    tts_text text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    category character varying(50) DEFAULT 'ivr'::character varying,
    description text
);


--
-- Name: audio_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audio_files (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    name character varying(255) NOT NULL,
    file_name character varying(255) NOT NULL,
    category character varying(50) DEFAULT 'ivr_greeting'::character varying NOT NULL,
    file_path character varying(512),
    file_size bigint,
    duration_seconds integer,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    user_id uuid,
    action character varying(100) NOT NULL,
    resource_type character varying(50) NOT NULL,
    resource_id character varying(100),
    old_value jsonb,
    new_value jsonb,
    ip_address character varying(45),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: business_hours; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.business_hours (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    timezone character varying(50) DEFAULT 'UTC'::character varying,
    schedule jsonb NOT NULL,
    open_destination_type character varying(30) NOT NULL,
    open_destination_target character varying(100) NOT NULL,
    closed_destination_type character varying(30) NOT NULL,
    closed_destination_target character varying(100) NOT NULL,
    holiday_destination_type character varying(30),
    holiday_destination_target character varying(100),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: call_forwarding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.call_forwarding (
    extension_id uuid NOT NULL,
    forward_always_enabled boolean DEFAULT false,
    forward_always_destination character varying(100),
    forward_busy_enabled boolean DEFAULT false,
    forward_busy_destination character varying(100),
    forward_no_answer_enabled boolean DEFAULT false,
    forward_no_answer_destination character varying(100),
    forward_no_answer_timeout integer DEFAULT 20,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: call_routes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.call_routes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    did_number character varying(50),
    route_type character varying(50) DEFAULT 'inbound_did'::character varying,
    destination_type character varying(50) DEFAULT 'queue'::character varying,
    destination character varying(255) NOT NULL,
    priority integer DEFAULT 1,
    regex_pattern character varying(255),
    gateway_id uuid,
    tenant_id uuid,
    enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    deleted_at timestamp with time zone
);


--
-- Name: call_waiting_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.call_waiting_settings (
    extension_id uuid NOT NULL,
    enabled boolean DEFAULT true,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cdr; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cdr (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    call_uuid character varying(100) NOT NULL,
    direction character varying(20) NOT NULL,
    caller_number character varying(50),
    caller_name character varying(100),
    destination character varying(50),
    source_extension character varying(20),
    destination_extension character varying(20),
    did character varying(50),
    sip_trunk_id uuid,
    start_time timestamp with time zone NOT NULL,
    answer_time timestamp with time zone,
    end_time timestamp with time zone NOT NULL,
    duration integer DEFAULT 0 NOT NULL,
    billsec integer DEFAULT 0 NOT NULL,
    hangup_cause character varying(50),
    hangup_code integer,
    recording_id uuid,
    queue_id uuid,
    ivr_id uuid,
    ring_group_id uuid,
    conference_id uuid,
    transfer_history jsonb DEFAULT '[]'::jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: conference_participants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conference_participants (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    conference_id uuid NOT NULL,
    member_id integer NOT NULL,
    call_uuid character varying(100) NOT NULL,
    caller_id_name character varying(100),
    caller_id_number character varying(50),
    is_moderator boolean DEFAULT false,
    is_muted boolean DEFAULT false,
    joined_at timestamp with time zone DEFAULT now() NOT NULL,
    left_at timestamp with time zone
);


--
-- Name: conferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conferences (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    extension_number character varying(20) NOT NULL,
    pin character varying(20),
    moderator_pin character varying(20),
    max_members integer DEFAULT 50,
    record_conference boolean DEFAULT false,
    wait_for_moderator boolean DEFAULT false,
    announce_join_leave boolean DEFAULT true,
    enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dids; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dids (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    did_number character varying(50) NOT NULL,
    destination_type character varying(30) DEFAULT 'extension'::character varying NOT NULL,
    destination_target character varying(100) DEFAULT ''::character varying,
    enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    trunk_id uuid,
    destination character varying(255),
    deleted_at timestamp with time zone
);


--
-- Name: dnd_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dnd_settings (
    extension_id uuid NOT NULL,
    enabled boolean DEFAULT false,
    destination_type character varying(30) DEFAULT 'voicemail'::character varying,
    destination character varying(100),
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: email_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.email_settings (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    smtp_host character varying(255) NOT NULL,
    smtp_port integer DEFAULT 587 NOT NULL,
    smtp_username character varying(100),
    smtp_password character varying(255),
    from_email character varying(255) NOT NULL,
    from_name character varying(100) DEFAULT 'PBX Voicemail'::character varying,
    use_tls boolean DEFAULT true,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: extension_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.extension_settings (
    extension_id uuid NOT NULL,
    codecs jsonb DEFAULT '["opus", "G722", "PCMU", "PCMA"]'::jsonb,
    max_contacts integer DEFAULT 5,
    allow_anonymous boolean DEFAULT false,
    record_inbound boolean DEFAULT false,
    record_outbound boolean DEFAULT false,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: extensions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.extensions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    user_id uuid,
    extension_number character varying(20) NOT NULL,
    display_name character varying(100) NOT NULL,
    email character varying(255),
    sip_password character varying(255) NOT NULL,
    voicemail_pin character varying(20) DEFAULT '1234'::character varying NOT NULL,
    caller_id_name character varying(100),
    caller_id_number character varying(50),
    outbound_caller_id character varying(50),
    emergency_caller_id character varying(50),
    timezone character varying(50) DEFAULT 'UTC'::character varying,
    enabled boolean DEFAULT true NOT NULL,
    webrtc_enabled boolean DEFAULT true NOT NULL,
    no_answer_timeout integer DEFAULT 20 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    voicemail_email character varying(255),
    voicemail_to_email boolean DEFAULT false,
    follow_me_enabled boolean DEFAULT false,
    follow_me_destination character varying(100),
    follow_me_timeout integer DEFAULT 20,
    call_forward_enabled boolean DEFAULT false,
    call_forward_type character varying(20) DEFAULT 'always'::character varying,
    call_forward_destination character varying(100)
);


--
-- Name: gateways; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gateways (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    proxy character varying(255) NOT NULL,
    username character varying(100),
    password character varying(255),
    realm character varying(255),
    from_domain character varying(255),
    codecs character varying(255) DEFAULT 'PCMU,PCMA,G722'::character varying,
    tenant_id uuid,
    register boolean DEFAULT true,
    caller_id_in_from boolean DEFAULT false,
    enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    deleted_at timestamp with time zone
);


--
-- Name: holidays; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.holidays (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    business_hours_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    holiday_date date NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hunt_group_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hunt_group_members (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    hunt_group_id uuid NOT NULL,
    destination_type character varying(30) NOT NULL,
    destination character varying(100) NOT NULL,
    priority integer DEFAULT 1 NOT NULL,
    timeout integer DEFAULT 15 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hunt_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hunt_groups (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    extension_id uuid,
    enabled boolean DEFAULT false NOT NULL,
    strategy character varying(30) DEFAULT 'sequential'::character varying NOT NULL,
    confirm_call boolean DEFAULT false,
    confirm_key character varying(5) DEFAULT '1'::character varying,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    name character varying(100),
    members text,
    extension_number character varying(20) DEFAULT '8001'::character varying,
    deleted_at timestamp with time zone,
    timeout integer DEFAULT 20
);


--
-- Name: ivr_actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ivr_actions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    ivr_node_id uuid NOT NULL,
    step_order integer DEFAULT 1 NOT NULL,
    action character varying(50) NOT NULL,
    parameters jsonb DEFAULT '{}'::jsonb
);


--
-- Name: ivr_menus; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ivr_menus (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    name character varying(100) NOT NULL,
    extension_number character varying(20),
    greeting_announcement_id uuid,
    invalid_announcement_id uuid,
    timeout_announcement_id uuid,
    timeout integer DEFAULT 10,
    max_retries integer DEFAULT 3,
    allow_direct_extension boolean DEFAULT true,
    enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    greeting_audio character varying(255) DEFAULT 'welcome_prompt.wav'::character varying,
    direct_extension_dial boolean DEFAULT true
);


--
-- Name: ivr_nodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ivr_nodes (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    ivr_menu_id uuid NOT NULL,
    dtmf_key character varying(5) NOT NULL,
    action_type character varying(30) NOT NULL,
    action_target character varying(100) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: music_on_hold; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.music_on_hold (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    directory_path character varying(500) NOT NULL,
    mode character varying(20) DEFAULT 'playlist'::character varying,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: outbound_route_patterns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbound_route_patterns (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    outbound_route_id uuid NOT NULL,
    pattern character varying(100) NOT NULL,
    sip_trunk_id uuid NOT NULL,
    priority integer DEFAULT 1
);


--
-- Name: outbound_routes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbound_routes (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    name character varying(100) NOT NULL,
    priority integer DEFAULT 1 NOT NULL,
    strip_digits integer DEFAULT 0,
    prepend_digits character varying(20) DEFAULT ''::character varying,
    enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: parking_slots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parking_slots (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    slot_number character varying(10) NOT NULL,
    timeout integer DEFAULT 60,
    timeout_destination_type character varying(30) DEFAULT 'extension'::character varying,
    timeout_destination character varying(100),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.permissions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    code character varying(100) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pickup_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pickup_groups (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    group_number character varying(20) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: queues; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.queues (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    queue_number character varying(20) NOT NULL,
    strategy character varying(50) DEFAULT 'round_robin'::character varying,
    agent_timeout integer DEFAULT 30,
    wrap_up_time integer DEFAULT 10,
    max_wait_time integer DEFAULT 300,
    agents text,
    announce_position boolean DEFAULT true,
    announce_wait_time boolean DEFAULT true,
    tenant_id uuid,
    enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    deleted_at timestamp with time zone
);


--
-- Name: recording_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recording_policies (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    scope character varying(30) NOT NULL,
    target_id uuid,
    record_inbound boolean DEFAULT false,
    record_outbound boolean DEFAULT false,
    record_internal boolean DEFAULT false,
    format character varying(10) DEFAULT 'wav'::character varying,
    storage_provider character varying(20) DEFAULT 'local'::character varying,
    retention_days integer DEFAULT 90,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: recordings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recordings (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    call_uuid character varying(100) NOT NULL,
    source_extension character varying(20),
    caller_number character varying(50),
    destination_number character varying(50),
    direction character varying(20) NOT NULL,
    start_time timestamp with time zone NOT NULL,
    end_time timestamp with time zone,
    duration integer DEFAULT 0,
    file_path character varying(500) NOT NULL,
    storage_provider character varying(20) DEFAULT 'local'::character varying NOT NULL,
    file_format character varying(10) DEFAULT 'wav'::character varying,
    file_size bigint DEFAULT 0,
    encryption_status character varying(20) DEFAULT 'none'::character varying,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ring_group_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ring_group_members (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    ring_group_id uuid NOT NULL,
    extension_id uuid,
    external_number character varying(50),
    priority integer DEFAULT 1 NOT NULL,
    delay_seconds integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ring_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ring_groups (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    extension_number character varying(20) NOT NULL,
    strategy character varying(30) DEFAULT 'simultaneous'::character varying NOT NULL,
    ring_timeout integer DEFAULT 20 NOT NULL,
    distinctive_ring character varying(50),
    failover_destination_type character varying(30) DEFAULT 'voicemail'::character varying,
    failover_destination character varying(100),
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.roles (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name character varying(50) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sip_trunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sip_trunks (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    name character varying(100) NOT NULL,
    provider character varying(100),
    host character varying(255) NOT NULL,
    port integer DEFAULT 5060,
    transport character varying(10) DEFAULT 'udp'::character varying,
    username character varying(100),
    password character varying(255),
    realm character varying(255),
    caller_id character varying(50),
    codecs jsonb DEFAULT '["PCMU", "PCMA", "G722"]'::jsonb,
    register boolean DEFAULT true,
    enabled boolean DEFAULT true,
    priority integer DEFAULT 1,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    srtp boolean DEFAULT false,
    deleted_at timestamp with time zone
);


--
-- Name: storage_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.storage_settings (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    provider character varying(20) DEFAULT 'local'::character varying NOT NULL,
    config jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: system_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_settings (
    key character varying(100) NOT NULL,
    value jsonb NOT NULL,
    description text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: tenant_gateways; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_gateways (
    tenant_id uuid NOT NULL,
    gateway_id uuid NOT NULL,
    direction character varying(50) DEFAULT 'inbound_outbound'::character varying,
    priority integer DEFAULT 1,
    caller_id_policy character varying(50) DEFAULT 'tenant_default'::character varying,
    allow_outbound boolean DEFAULT true,
    accept_inbound boolean DEFAULT true,
    allow_international boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: tenants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenants (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name character varying(100) NOT NULL,
    domain character varying(255) NOT NULL,
    sip_domain character varying(255) NOT NULL,
    branding jsonb DEFAULT '{}'::jsonb,
    timezone character varying(50) DEFAULT 'UTC'::character varying,
    enabled boolean DEFAULT true NOT NULL,
    max_extensions integer DEFAULT 100,
    max_concurrent_calls integer DEFAULT 20,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);


--
-- Name: user_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_roles (
    user_id uuid NOT NULL,
    role_id uuid NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    username character varying(100) NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(255) NOT NULL,
    first_name character varying(50),
    last_name character varying(50),
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    allowed_modules jsonb DEFAULT '[]'::jsonb
);


--
-- Name: voicemail_boxes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.voicemail_boxes (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    extension_id uuid NOT NULL,
    mailbox character varying(20) NOT NULL,
    password character varying(20) DEFAULT '1234'::character varying NOT NULL,
    email_notification boolean DEFAULT true,
    email_attach_file boolean DEFAULT true,
    email_address character varying(255),
    delete_after_email boolean DEFAULT false,
    greeting_path character varying(500),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: voicemail_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.voicemail_messages (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    voicemail_box_id uuid NOT NULL,
    caller_id_name character varying(100),
    caller_id_number character varying(50),
    file_path character varying(500) NOT NULL,
    duration integer NOT NULL,
    read boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: announcements announcements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.announcements
    ADD CONSTRAINT announcements_pkey PRIMARY KEY (id);


--
-- Name: audio_files audio_files_file_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audio_files
    ADD CONSTRAINT audio_files_file_name_key UNIQUE (file_name);


--
-- Name: audio_files audio_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audio_files
    ADD CONSTRAINT audio_files_pkey PRIMARY KEY (id);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: business_hours business_hours_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.business_hours
    ADD CONSTRAINT business_hours_pkey PRIMARY KEY (id);


--
-- Name: call_forwarding call_forwarding_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_forwarding
    ADD CONSTRAINT call_forwarding_pkey PRIMARY KEY (extension_id);


--
-- Name: call_routes call_routes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_routes
    ADD CONSTRAINT call_routes_pkey PRIMARY KEY (id);


--
-- Name: call_waiting_settings call_waiting_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_waiting_settings
    ADD CONSTRAINT call_waiting_settings_pkey PRIMARY KEY (extension_id);


--
-- Name: cdr cdr_call_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cdr
    ADD CONSTRAINT cdr_call_uuid_key UNIQUE (call_uuid);


--
-- Name: cdr cdr_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cdr
    ADD CONSTRAINT cdr_pkey PRIMARY KEY (id);


--
-- Name: conference_participants conference_participants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conference_participants
    ADD CONSTRAINT conference_participants_pkey PRIMARY KEY (id);


--
-- Name: conferences conferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conferences
    ADD CONSTRAINT conferences_pkey PRIMARY KEY (id);


--
-- Name: conferences conferences_tenant_id_extension_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conferences
    ADD CONSTRAINT conferences_tenant_id_extension_number_key UNIQUE (tenant_id, extension_number);


--
-- Name: dids dids_did_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dids
    ADD CONSTRAINT dids_did_number_key UNIQUE (did_number);


--
-- Name: dids dids_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dids
    ADD CONSTRAINT dids_pkey PRIMARY KEY (id);


--
-- Name: dnd_settings dnd_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dnd_settings
    ADD CONSTRAINT dnd_settings_pkey PRIMARY KEY (extension_id);


--
-- Name: email_settings email_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_settings
    ADD CONSTRAINT email_settings_pkey PRIMARY KEY (id);


--
-- Name: email_settings email_settings_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_settings
    ADD CONSTRAINT email_settings_tenant_id_key UNIQUE (tenant_id);


--
-- Name: extension_settings extension_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extension_settings
    ADD CONSTRAINT extension_settings_pkey PRIMARY KEY (extension_id);


--
-- Name: extensions extensions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extensions
    ADD CONSTRAINT extensions_pkey PRIMARY KEY (id);


--
-- Name: extensions extensions_tenant_id_extension_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extensions
    ADD CONSTRAINT extensions_tenant_id_extension_number_key UNIQUE (tenant_id, extension_number);


--
-- Name: gateways gateways_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateways
    ADD CONSTRAINT gateways_pkey PRIMARY KEY (id);


--
-- Name: holidays holidays_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.holidays
    ADD CONSTRAINT holidays_pkey PRIMARY KEY (id);


--
-- Name: hunt_group_members hunt_group_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunt_group_members
    ADD CONSTRAINT hunt_group_members_pkey PRIMARY KEY (id);


--
-- Name: hunt_groups hunt_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunt_groups
    ADD CONSTRAINT hunt_groups_pkey PRIMARY KEY (id);


--
-- Name: ivr_actions ivr_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ivr_actions
    ADD CONSTRAINT ivr_actions_pkey PRIMARY KEY (id);


--
-- Name: ivr_menus ivr_menus_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ivr_menus
    ADD CONSTRAINT ivr_menus_pkey PRIMARY KEY (id);


--
-- Name: ivr_menus ivr_menus_tenant_id_extension_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ivr_menus
    ADD CONSTRAINT ivr_menus_tenant_id_extension_number_key UNIQUE (tenant_id, extension_number);


--
-- Name: ivr_nodes ivr_nodes_ivr_menu_id_dtmf_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ivr_nodes
    ADD CONSTRAINT ivr_nodes_ivr_menu_id_dtmf_key_key UNIQUE (ivr_menu_id, dtmf_key);


--
-- Name: ivr_nodes ivr_nodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ivr_nodes
    ADD CONSTRAINT ivr_nodes_pkey PRIMARY KEY (id);


--
-- Name: music_on_hold music_on_hold_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.music_on_hold
    ADD CONSTRAINT music_on_hold_pkey PRIMARY KEY (id);


--
-- Name: outbound_route_patterns outbound_route_patterns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_route_patterns
    ADD CONSTRAINT outbound_route_patterns_pkey PRIMARY KEY (id);


--
-- Name: outbound_routes outbound_routes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_routes
    ADD CONSTRAINT outbound_routes_pkey PRIMARY KEY (id);


--
-- Name: parking_slots parking_slots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parking_slots
    ADD CONSTRAINT parking_slots_pkey PRIMARY KEY (id);


--
-- Name: parking_slots parking_slots_tenant_id_slot_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parking_slots
    ADD CONSTRAINT parking_slots_tenant_id_slot_number_key UNIQUE (tenant_id, slot_number);


--
-- Name: permissions permissions_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permissions
    ADD CONSTRAINT permissions_code_key UNIQUE (code);


--
-- Name: permissions permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permissions
    ADD CONSTRAINT permissions_pkey PRIMARY KEY (id);


--
-- Name: pickup_groups pickup_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pickup_groups
    ADD CONSTRAINT pickup_groups_pkey PRIMARY KEY (id);


--
-- Name: pickup_groups pickup_groups_tenant_id_group_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pickup_groups
    ADD CONSTRAINT pickup_groups_tenant_id_group_number_key UNIQUE (tenant_id, group_number);


--
-- Name: queues queues_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.queues
    ADD CONSTRAINT queues_pkey PRIMARY KEY (id);


--
-- Name: recording_policies recording_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recording_policies
    ADD CONSTRAINT recording_policies_pkey PRIMARY KEY (id);


--
-- Name: recordings recordings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings
    ADD CONSTRAINT recordings_pkey PRIMARY KEY (id);


--
-- Name: ring_group_members ring_group_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ring_group_members
    ADD CONSTRAINT ring_group_members_pkey PRIMARY KEY (id);


--
-- Name: ring_groups ring_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ring_groups
    ADD CONSTRAINT ring_groups_pkey PRIMARY KEY (id);


--
-- Name: ring_groups ring_groups_tenant_id_extension_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ring_groups
    ADD CONSTRAINT ring_groups_tenant_id_extension_number_key UNIQUE (tenant_id, extension_number);


--
-- Name: roles roles_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_name_key UNIQUE (name);


--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- Name: sip_trunks sip_trunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sip_trunks
    ADD CONSTRAINT sip_trunks_pkey PRIMARY KEY (id);


--
-- Name: storage_settings storage_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.storage_settings
    ADD CONSTRAINT storage_settings_pkey PRIMARY KEY (id);


--
-- Name: storage_settings storage_settings_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.storage_settings
    ADD CONSTRAINT storage_settings_tenant_id_key UNIQUE (tenant_id);


--
-- Name: system_settings system_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_settings
    ADD CONSTRAINT system_settings_pkey PRIMARY KEY (key);


--
-- Name: tenant_gateways tenant_gateways_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_gateways
    ADD CONSTRAINT tenant_gateways_pkey PRIMARY KEY (tenant_id, gateway_id);


--
-- Name: tenants tenants_domain_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_domain_key UNIQUE (domain);


--
-- Name: tenants tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);


--
-- Name: tenants tenants_sip_domain_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_sip_domain_key UNIQUE (sip_domain);


--
-- Name: user_roles user_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_pkey PRIMARY KEY (user_id, role_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_tenant_id_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_tenant_id_email_key UNIQUE (tenant_id, email);


--
-- Name: users users_tenant_id_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_tenant_id_username_key UNIQUE (tenant_id, username);


--
-- Name: voicemail_boxes voicemail_boxes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voicemail_boxes
    ADD CONSTRAINT voicemail_boxes_pkey PRIMARY KEY (id);


--
-- Name: voicemail_boxes voicemail_boxes_tenant_id_mailbox_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voicemail_boxes
    ADD CONSTRAINT voicemail_boxes_tenant_id_mailbox_key UNIQUE (tenant_id, mailbox);


--
-- Name: voicemail_messages voicemail_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voicemail_messages
    ADD CONSTRAINT voicemail_messages_pkey PRIMARY KEY (id);


--
-- Name: idx_announcements_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_announcements_tenant ON public.announcements USING btree (tenant_id);


--
-- Name: idx_audio_files_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audio_files_category ON public.audio_files USING btree (category);


--
-- Name: idx_audio_files_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audio_files_tenant ON public.audio_files USING btree (tenant_id);


--
-- Name: idx_audit_logs_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_tenant ON public.audit_logs USING btree (tenant_id);


--
-- Name: idx_audit_logs_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_user ON public.audit_logs USING btree (user_id);


--
-- Name: idx_business_hours_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_business_hours_tenant ON public.business_hours USING btree (tenant_id);


--
-- Name: idx_cdr_call_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cdr_call_uuid ON public.cdr USING btree (call_uuid);


--
-- Name: idx_cdr_dates; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cdr_dates ON public.cdr USING btree (tenant_id, start_time);


--
-- Name: idx_cdr_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cdr_destination ON public.cdr USING btree (tenant_id, destination);


--
-- Name: idx_cdr_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cdr_source ON public.cdr USING btree (tenant_id, source_extension);


--
-- Name: idx_cdr_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cdr_tenant ON public.cdr USING btree (tenant_id);


--
-- Name: idx_conferences_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conferences_tenant ON public.conferences USING btree (tenant_id);


--
-- Name: idx_dids_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dids_number ON public.dids USING btree (did_number);


--
-- Name: idx_dids_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dids_tenant ON public.dids USING btree (tenant_id);


--
-- Name: idx_extensions_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_extensions_lookup ON public.extensions USING btree (tenant_id, extension_number);


--
-- Name: idx_extensions_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_extensions_tenant ON public.extensions USING btree (tenant_id);


--
-- Name: idx_hunt_groups_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hunt_groups_tenant ON public.hunt_groups USING btree (tenant_id);


--
-- Name: idx_ivr_menus_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ivr_menus_tenant ON public.ivr_menus USING btree (tenant_id);


--
-- Name: idx_music_on_hold_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_music_on_hold_tenant ON public.music_on_hold USING btree (tenant_id);


--
-- Name: idx_parking_slots_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_parking_slots_tenant ON public.parking_slots USING btree (tenant_id);


--
-- Name: idx_recording_policies_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_recording_policies_tenant ON public.recording_policies USING btree (tenant_id);


--
-- Name: idx_recordings_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_recordings_tenant ON public.recordings USING btree (tenant_id);


--
-- Name: idx_recordings_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_recordings_uuid ON public.recordings USING btree (call_uuid);


--
-- Name: idx_ring_groups_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ring_groups_tenant ON public.ring_groups USING btree (tenant_id);


--
-- Name: idx_tenants_domain; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenants_domain ON public.tenants USING btree (domain);


--
-- Name: idx_tenants_sip_domain; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenants_sip_domain ON public.tenants USING btree (sip_domain);


--
-- Name: idx_users_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_tenant ON public.users USING btree (tenant_id);


--
-- Name: idx_voicemail_boxes_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_voicemail_boxes_tenant ON public.voicemail_boxes USING btree (tenant_id);


--
-- Name: announcements announcements_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.announcements
    ADD CONSTRAINT announcements_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: audio_files audio_files_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audio_files
    ADD CONSTRAINT audio_files_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: audio_files audio_files_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audio_files
    ADD CONSTRAINT audio_files_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: audit_logs audit_logs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: audit_logs audit_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: business_hours business_hours_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.business_hours
    ADD CONSTRAINT business_hours_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: call_forwarding call_forwarding_extension_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_forwarding
    ADD CONSTRAINT call_forwarding_extension_id_fkey FOREIGN KEY (extension_id) REFERENCES public.extensions(id) ON DELETE CASCADE;


--
-- Name: call_routes call_routes_gateway_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_routes
    ADD CONSTRAINT call_routes_gateway_id_fkey FOREIGN KEY (gateway_id) REFERENCES public.gateways(id) ON DELETE SET NULL;


--
-- Name: call_routes call_routes_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_routes
    ADD CONSTRAINT call_routes_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: call_waiting_settings call_waiting_settings_extension_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_waiting_settings
    ADD CONSTRAINT call_waiting_settings_extension_id_fkey FOREIGN KEY (extension_id) REFERENCES public.extensions(id) ON DELETE CASCADE;


--
-- Name: cdr cdr_recording_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cdr
    ADD CONSTRAINT cdr_recording_id_fkey FOREIGN KEY (recording_id) REFERENCES public.recordings(id) ON DELETE SET NULL;


--
-- Name: cdr cdr_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cdr
    ADD CONSTRAINT cdr_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: conference_participants conference_participants_conference_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conference_participants
    ADD CONSTRAINT conference_participants_conference_id_fkey FOREIGN KEY (conference_id) REFERENCES public.conferences(id) ON DELETE CASCADE;


--
-- Name: conferences conferences_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conferences
    ADD CONSTRAINT conferences_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: dids dids_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dids
    ADD CONSTRAINT dids_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: dids dids_trunk_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dids
    ADD CONSTRAINT dids_trunk_id_fkey FOREIGN KEY (trunk_id) REFERENCES public.sip_trunks(id) ON DELETE SET NULL;


--
-- Name: dnd_settings dnd_settings_extension_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dnd_settings
    ADD CONSTRAINT dnd_settings_extension_id_fkey FOREIGN KEY (extension_id) REFERENCES public.extensions(id) ON DELETE CASCADE;


--
-- Name: email_settings email_settings_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_settings
    ADD CONSTRAINT email_settings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: extension_settings extension_settings_extension_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extension_settings
    ADD CONSTRAINT extension_settings_extension_id_fkey FOREIGN KEY (extension_id) REFERENCES public.extensions(id) ON DELETE CASCADE;


--
-- Name: extensions extensions_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extensions
    ADD CONSTRAINT extensions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: extensions extensions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extensions
    ADD CONSTRAINT extensions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: gateways gateways_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateways
    ADD CONSTRAINT gateways_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: holidays holidays_business_hours_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.holidays
    ADD CONSTRAINT holidays_business_hours_id_fkey FOREIGN KEY (business_hours_id) REFERENCES public.business_hours(id) ON DELETE CASCADE;


--
-- Name: hunt_group_members hunt_group_members_hunt_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunt_group_members
    ADD CONSTRAINT hunt_group_members_hunt_group_id_fkey FOREIGN KEY (hunt_group_id) REFERENCES public.hunt_groups(id) ON DELETE CASCADE;


--
-- Name: hunt_groups hunt_groups_extension_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunt_groups
    ADD CONSTRAINT hunt_groups_extension_id_fkey FOREIGN KEY (extension_id) REFERENCES public.extensions(id) ON DELETE CASCADE;


--
-- Name: hunt_groups hunt_groups_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunt_groups
    ADD CONSTRAINT hunt_groups_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: ivr_actions ivr_actions_ivr_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ivr_actions
    ADD CONSTRAINT ivr_actions_ivr_node_id_fkey FOREIGN KEY (ivr_node_id) REFERENCES public.ivr_nodes(id) ON DELETE CASCADE;


--
-- Name: ivr_menus ivr_menus_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ivr_menus
    ADD CONSTRAINT ivr_menus_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: ivr_nodes ivr_nodes_ivr_menu_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ivr_nodes
    ADD CONSTRAINT ivr_nodes_ivr_menu_id_fkey FOREIGN KEY (ivr_menu_id) REFERENCES public.ivr_menus(id) ON DELETE CASCADE;


--
-- Name: music_on_hold music_on_hold_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.music_on_hold
    ADD CONSTRAINT music_on_hold_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: outbound_route_patterns outbound_route_patterns_outbound_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_route_patterns
    ADD CONSTRAINT outbound_route_patterns_outbound_route_id_fkey FOREIGN KEY (outbound_route_id) REFERENCES public.outbound_routes(id) ON DELETE CASCADE;


--
-- Name: outbound_route_patterns outbound_route_patterns_sip_trunk_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_route_patterns
    ADD CONSTRAINT outbound_route_patterns_sip_trunk_id_fkey FOREIGN KEY (sip_trunk_id) REFERENCES public.sip_trunks(id) ON DELETE CASCADE;


--
-- Name: outbound_routes outbound_routes_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_routes
    ADD CONSTRAINT outbound_routes_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: parking_slots parking_slots_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parking_slots
    ADD CONSTRAINT parking_slots_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: pickup_groups pickup_groups_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pickup_groups
    ADD CONSTRAINT pickup_groups_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: queues queues_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.queues
    ADD CONSTRAINT queues_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: recording_policies recording_policies_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recording_policies
    ADD CONSTRAINT recording_policies_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: recordings recordings_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings
    ADD CONSTRAINT recordings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: ring_group_members ring_group_members_extension_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ring_group_members
    ADD CONSTRAINT ring_group_members_extension_id_fkey FOREIGN KEY (extension_id) REFERENCES public.extensions(id) ON DELETE CASCADE;


--
-- Name: ring_group_members ring_group_members_ring_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ring_group_members
    ADD CONSTRAINT ring_group_members_ring_group_id_fkey FOREIGN KEY (ring_group_id) REFERENCES public.ring_groups(id) ON DELETE CASCADE;


--
-- Name: ring_groups ring_groups_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ring_groups
    ADD CONSTRAINT ring_groups_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: sip_trunks sip_trunks_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sip_trunks
    ADD CONSTRAINT sip_trunks_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: storage_settings storage_settings_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.storage_settings
    ADD CONSTRAINT storage_settings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_gateways tenant_gateways_gateway_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_gateways
    ADD CONSTRAINT tenant_gateways_gateway_id_fkey FOREIGN KEY (gateway_id) REFERENCES public.gateways(id) ON DELETE CASCADE;


--
-- Name: tenant_gateways tenant_gateways_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_gateways
    ADD CONSTRAINT tenant_gateways_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: user_roles user_roles_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.roles(id) ON DELETE CASCADE;


--
-- Name: user_roles user_roles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: users users_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: voicemail_boxes voicemail_boxes_extension_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voicemail_boxes
    ADD CONSTRAINT voicemail_boxes_extension_id_fkey FOREIGN KEY (extension_id) REFERENCES public.extensions(id) ON DELETE CASCADE;


--
-- Name: voicemail_boxes voicemail_boxes_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voicemail_boxes
    ADD CONSTRAINT voicemail_boxes_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: voicemail_messages voicemail_messages_voicemail_box_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voicemail_messages
    ADD CONSTRAINT voicemail_messages_voicemail_box_id_fkey FOREIGN KEY (voicemail_box_id) REFERENCES public.voicemail_boxes(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--



-- ============================================================================
-- SEED DATA: CORE ROLES & PERMISSIONS
-- ============================================================================

SET search_path = public, pg_catalog;

INSERT INTO public.roles (id, name, description) VALUES
    ('3fb08e00-1d89-4618-a783-c13e5e34d7f9', 'SUPER_ADMIN', 'Platform Super Administrator with complete system access'),
    ('89b9e390-729d-4855-b6e7-9387870d1db4', 'TENANT_ADMIN', 'Tenant Master Administrator with full access to tenant features'),
    ('6c1dfe90-5ec9-4587-997b-30eecaeaec0c', 'SUB_ADMIN', 'Tenant Sub-Administrator with configurable modular permissions'),
    ('e1b4d41a-382e-4ee9-99a3-4039e1ace468', 'SUPERVISOR', 'Call Center / Extension Supervisor with monitoring & reports access'),
    ('1b6dc9d3-247f-4bf3-b3f7-af1acc82d439', 'AGENT', 'Standard Extension / Softphone User with voicemail & forwarding access')
ON CONFLICT (name) DO UPDATE SET
    description = EXCLUDED.description;

-- ============================================================================
-- SEED DATA: TENANTS, USERS, EXTENSIONS, TRUNKS, IVR & VOICEMAIL
-- ============================================================================

-- 1. Tenants
INSERT INTO public.tenants (id, name, domain, sip_domain, enabled) VALUES
    ('9c61b161-db6f-4825-b7db-cb76d88155c5', 'aikyamlabs', 'pbx.aikyamlabs.local', 'pbx.aikyamlabs.local', true),
    ('45a5c3e7-a7a2-4b81-95ea-3ae21d3d7656', 'Acme Corporation', 'acme.pbx.com', 'acme.local', true)
ON CONFLICT (id) DO UPDATE SET
    domain = EXCLUDED.domain,
    sip_domain = EXCLUDED.sip_domain,
    enabled = EXCLUDED.enabled;

-- 2. Users (Superadmin & Aikyam Admin)
INSERT INTO public.users (id, tenant_id, username, email, password_hash, first_name, last_name, is_active) VALUES
    ('0c909c30-0116-45ab-91c7-b16ec662ee4a', NULL, 'superadmin', 'admin@pbx.com', '$2b$12$6eUuzglJ9MP/3ZFdSCYYke9i4CGZ0U02e2dCKlfeoGazMDeqs1C0y', 'Super', 'Admin', true),
    ('9c262465-b5af-4158-93b8-75e074b30972', '9c61b161-db6f-4825-b7db-cb76d88155c5', 'aikyamadmin', 'chetang.jha@gmail.com', '$2b$12$R8oz.I.umm6nZs8ifGit4.xdcglEhRIKkxwB2xsQrxZm2W3qwwGqC', 'Chetan', 'Jha', true)
ON CONFLICT (id) DO UPDATE SET
    email = EXCLUDED.email,
    is_active = EXCLUDED.is_active;

-- User Roles Mapping
INSERT INTO public.user_roles (user_id, role_id) VALUES
    ('0c909c30-0116-45ab-91c7-b16ec662ee4a', '3fb08e00-1d89-4618-a783-c13e5e34d7f9'),
    ('9c262465-b5af-4158-93b8-75e074b30972', '89b9e390-729d-4855-b6e7-9387870d1db4')
ON CONFLICT (user_id, role_id) DO NOTHING;

-- 3. Extensions (1001 with password 10011001 for Chetan Jha on pbx.aikyamlabs.local)
INSERT INTO public.extensions (id, tenant_id, extension_number, display_name, email, sip_password, voicemail_pin, caller_id_name, caller_id_number, no_answer_timeout, enabled, webrtc_enabled) VALUES
    ('2bdc3019-5370-47cf-8ce9-4c17548c7199', '9c61b161-db6f-4825-b7db-cb76d88155c5', '1001', 'Chetan Jha', 'chetang.jha@gmail.com', '10011001', '1234', 'Chetan Jha', '1001', 20, true, true),
    ('883d9ff1-ecff-4235-9ffc-fd70f1ab9b7d', '9c61b161-db6f-4825-b7db-cb76d88155c5', '9001', 'Dinesh Patil', 'dinesh@gmail.com', '90019001', '1234', 'Dinesh Patil', '9001', 20, true, true),
    ('4ba34070-a5ca-40e9-b7ef-1e3838830922', '9c61b161-db6f-4825-b7db-cb76d88155c5', '1002', 'Alice Smith', 'alice@acme.com', 'SIPPassword123!', '1234', 'Alice Smith', '1002', 20, true, true),
    ('fa10e1dd-0ee4-4569-a2b4-f8f198ce67a8', '9c61b161-db6f-4825-b7db-cb76d88155c5', '1003', 'Alice Smith', 'alice@acme.com', '10031003', '1234', 'Alice Smith', '1003', 20, true, true),
    ('17d356c0-bb75-4b05-a2ca-d9fe1181eadc', '9c61b161-db6f-4825-b7db-cb76d88155c5', '1004', 'Dinesh', 'dineshpatil2207@gmail.com', '10045678', '1234', 'Dinesh', '1004', 20, true, true),
    ('2b5be2f8-7aed-4a12-b596-69489c34c06d', '9c61b161-db6f-4825-b7db-cb76d88155c5', '1005', 'OPERATOR', 'alice@acme.com', '10061006', '1006', 'OPERATOR', '1005', 20, true, true),
    ('6dbe3e1a-ee6a-4aef-a9bd-9d3bd1078a8e', '9c61b161-db6f-4825-b7db-cb76d88155c5', '1009', 'Test Op', 'chetang.jha@gmail.com', '10091009', '1234', 'Test Op', '1009', 20, true, true)
ON CONFLICT (tenant_id, extension_number) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    email = EXCLUDED.email,
    sip_password = EXCLUDED.sip_password,
    voicemail_pin = EXCLUDED.voicemail_pin,
    caller_id_name = EXCLUDED.caller_id_name,
    caller_id_number = EXCLUDED.caller_id_number,
    no_answer_timeout = EXCLUDED.no_answer_timeout,
    enabled = EXCLUDED.enabled;

-- 4. Voicemail Box for Extension 1001
INSERT INTO public.voicemail_boxes (id, tenant_id, extension_id, mailbox, email_notification, email_address) VALUES
    ('8b057424-3640-460d-b9ca-df438db43bba', '9c61b161-db6f-4825-b7db-cb76d88155c5', '2bdc3019-5370-47cf-8ce9-4c17548c7199', '1001', true, 'chetang.jha@gmail.com')
ON CONFLICT (tenant_id, mailbox) DO UPDATE SET
    email_notification = EXCLUDED.email_notification,
    email_address = EXCLUDED.email_address;

-- 5. SIP Trunks
INSERT INTO public.sip_trunks (id, tenant_id, name, host, port, priority, enabled) VALUES
    ('e0036b4d-04d7-4c6b-85e0-8e664cc2b8fa', '9c61b161-db6f-4825-b7db-cb76d88155c5', 'tata', '1.2.3.4', 5060, 1, true),
    ('cc8f9e5e-2d80-46cb-b9c5-bef206cd64e9', NULL, 'Twilio Primary Trunk', 'sip.twilio.com', 5060, 1, true),
    ('039b6b62-0e67-4c0b-a260-c61600ce1950', NULL, 'Telnyx Secondary Trunk', 'sip.telnyx.com', 5060, 2, true)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    host = EXCLUDED.host,
    port = EXCLUDED.port,
    enabled = EXCLUDED.enabled;

-- 6. IVR Menus
INSERT INTO public.ivr_menus (id, tenant_id, name, extension_number, timeout, greeting_audio, enabled) VALUES
    ('46765586-c906-497f-83ba-0d39776cfbe4', '9c61b161-db6f-4825-b7db-cb76d88155c5', 'Test Welcome IVR', '6001', 10, 'welcome_prompt.wav', true)
ON CONFLICT (id) DO UPDATE SET
    extension_number = EXCLUDED.extension_number,
    greeting_audio = EXCLUDED.greeting_audio,
    enabled = EXCLUDED.enabled;

-- ============================================================================
-- END OF SCHEMA & INITIAL SEED
-- ============================================================================

-- ============================================================================
-- 7. Call Block (Blacklist) & Contacts
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.call_block (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    number character varying(50) NOT NULL,
    description character varying(255),
    action character varying(20) DEFAULT 'reject' NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT call_block_pkey PRIMARY KEY (id),
    CONSTRAINT call_block_tenant_number_unique UNIQUE (tenant_id, number)
);
CREATE INDEX IF NOT EXISTS idx_call_block_lookup ON public.call_block (tenant_id, number);

CREATE TABLE IF NOT EXISTS public.contacts (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    first_name character varying(100) NOT NULL,
    last_name character varying(100),
    organization character varying(150),
    phone_primary character varying(50) NOT NULL,
    phone_mobile character varying(50),
    email character varying(255),
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT contacts_pkey PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_contacts_tenant ON public.contacts (tenant_id);
