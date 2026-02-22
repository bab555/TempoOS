-- TempoOS Platform Database Schema
-- PostgreSQL + pgvector

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- Workflow Sessions
CREATE TABLE IF NOT EXISTS workflow_sessions (
    session_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL,
    flow_id         VARCHAR(128),
    current_state   VARCHAR(64) NOT NULL DEFAULT 'idle',
    session_state   VARCHAR(32) NOT NULL DEFAULT 'idle',
    params          JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    ttl_seconds     INTEGER DEFAULT 1800
);
CREATE INDEX IF NOT EXISTS idx_sessions_tenant ON workflow_sessions(tenant_id);

-- Workflow Flows
CREATE TABLE IF NOT EXISTS workflow_flows (
    flow_id         VARCHAR(128) PRIMARY KEY,
    name            VARCHAR(256) NOT NULL,
    description     TEXT,
    yaml_content    TEXT NOT NULL,
    param_schema    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Workflow Events (Audit Log)
CREATE TABLE IF NOT EXISTS workflow_events (
    event_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL,
    session_id      UUID NOT NULL REFERENCES workflow_sessions(session_id),
    event_type      VARCHAR(64) NOT NULL,
    source          VARCHAR(64) NOT NULL,
    target          VARCHAR(64),
    tick            BIGINT DEFAULT 0,
    trace_id        VARCHAR(128),
    priority        INTEGER DEFAULT 5,
    from_state      VARCHAR(64),
    to_state        VARCHAR(64),
    payload         JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_tenant_session ON workflow_events(tenant_id, session_id, created_at);

-- Idempotency Log
CREATE TABLE IF NOT EXISTS idempotency_log (
    session_id      UUID NOT NULL,
    step            VARCHAR(64) NOT NULL,
    attempt         INTEGER NOT NULL DEFAULT 1,
    status          VARCHAR(32) NOT NULL,
    result_hash     VARCHAR(64),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_id, step, attempt)
);

-- Registry Nodes
CREATE TABLE IF NOT EXISTS registry_nodes (
    node_id         VARCHAR(128) PRIMARY KEY,
    node_type       VARCHAR(32) NOT NULL,
    name            VARCHAR(256) NOT NULL,
    description     TEXT,
    endpoint        VARCHAR(512),
    param_schema    JSONB,
    status          VARCHAR(32) DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Session Snapshots (Redis ↔ PG cold swap, managed by Tonglu SessionEvictor)
CREATE TABLE IF NOT EXISTS tl_session_snapshots (
    session_id      VARCHAR(128) PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    chat_history    JSONB NOT NULL DEFAULT '[]',
    blackboard      JSONB NOT NULL DEFAULT '{}',
    tool_results    JSONB NOT NULL DEFAULT '{}',
    chat_summary    TEXT,
    routed_scene    VARCHAR(64),
    archived_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    restored_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tl_snapshots_tenant ON tl_session_snapshots(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tl_snapshots_archived ON tl_session_snapshots(archived_at);
