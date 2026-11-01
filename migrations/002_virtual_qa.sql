-- Migration 002: Real-Time Virtual QA Extension, Configs & Audit Events
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'uq_roles_name'
  ) AND NOT EXISTS (
    SELECT 1 FROM pg_indexes WHERE tablename = 'roles' AND indexdef ILIKE '%UNIQUE%(name)%'
  ) THEN
    ALTER TABLE public.roles ADD CONSTRAINT uq_roles_name UNIQUE (name);
  END IF;
END $$;

-- 1. VIRTUAL_QA role
INSERT INTO public.roles (id, name, description)
VALUES (gen_random_uuid(), 'VIRTUAL_QA', 'Automated Virtual Quality Assurance Bot')
ON CONFLICT (name) DO NOTHING;

-- 2. Headless virtual extensions
ALTER TABLE public.extensions
  ADD COLUMN IF NOT EXISTS is_virtual BOOLEAN NOT NULL DEFAULT false;

-- 3. Virtual QA configuration
CREATE TABLE IF NOT EXISTS public.virtual_qa_configs (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    qa_extension_id UUID NOT NULL REFERENCES public.extensions(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL DEFAULT 'Virtual QA Bot Policy',
    enabled BOOLEAN NOT NULL DEFAULT true,
    scope_type VARCHAR(20) NOT NULL DEFAULT 'PBX_PAIR',
    monitored_agents JSONB NOT NULL DEFAULT '[]'::jsonb,
    monitored_callers JSONB NOT NULL DEFAULT '["*"]'::jsonb,
    campaign_queue_id UUID REFERENCES public.queues(id) ON DELETE SET NULL,
    max_warn_retries INTEGER NOT NULL DEFAULT 3,
    cooldown_seconds INTEGER NOT NULL DEFAULT 12,
    supervisor_extension VARCHAR(20) NOT NULL DEFAULT '9001',
    qa_policy_prompt TEXT DEFAULT 'Flag the agent if they use profanity, insults, aggressive or hostile tone, tell the caller to shut up, or refuse to assist.',
    policy_prompt TEXT DEFAULT 'Flag the agent if they use profanity, insults, aggressive or hostile tone, tell the caller to shut up, or refuse to assist.',
    banned_keywords JSONB NOT NULL DEFAULT '["shut up","idiot","stupid","useless","ridiculous","hell","damn"]'::jsonb,
    aggression_rms_threshold INTEGER DEFAULT 8000,
    elevenlabs_voice_id VARCHAR(100) DEFAULT 'JBFqnCBsd6RMkjVDRZzb',
    warning_audio_path VARCHAR(255) DEFAULT 'tone_stream://%(400,200,800,1000);loops=2',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT uq_tenant_qa_ext UNIQUE (tenant_id, qa_extension_id),
    CONSTRAINT ck_qa_scope CHECK (scope_type IN ('PBX_PAIR','CAMPAIGN')),
    CONSTRAINT ck_qa_max_warn CHECK (max_warn_retries >= 0),
    CONSTRAINT ck_qa_cooldown CHECK (cooldown_seconds >= 0)
);

CREATE INDEX IF NOT EXISTS idx_vqa_configs_tenant_enabled
  ON public.virtual_qa_configs (tenant_id) WHERE enabled;

-- updated_at trigger
CREATE OR REPLACE FUNCTION public.set_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_vqa_configs_updated ON public.virtual_qa_configs;
CREATE TRIGGER trg_vqa_configs_updated BEFORE UPDATE ON public.virtual_qa_configs
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- 4. Durable audit trail (Redis state expires after 2h; reports need history)
CREATE TABLE IF NOT EXISTS public.qa_events (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    config_id UUID REFERENCES public.virtual_qa_configs(id) ON DELETE SET NULL,
    call_uuid VARCHAR(64) NOT NULL,
    event_type VARCHAR(30) NOT NULL,   -- 'warning' | 'escalation' | 'intervention' | 'call_ended'
    agent_extension VARCHAR(20),
    customer_extension VARCHAR(20),
    warn_count INTEGER,
    reason TEXT,
    transcript_snippet TEXT,
    whisper_text TEXT,
    latency_ms INTEGER,                 -- utterance-to-whisper, for tuning
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_qa_events_tenant_time ON public.qa_events (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_events_call ON public.qa_events (tenant_id, call_uuid);
