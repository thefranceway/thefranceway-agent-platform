-- Agent Platform D1 Schema
-- Apply with: wrangler d1 execute agent_platform_db --file=registry/schema.sql

CREATE TABLE IF NOT EXISTS agents (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    type               TEXT NOT NULL,
    model              TEXT NOT NULL DEFAULT 'claude-sonnet-4-6',
    system_prompt      TEXT,
    tools              TEXT,          -- JSON array
    knowledge_base     TEXT,
    behavioral_profile TEXT,
    created_by         TEXT DEFAULT 'user',
    created_at         TEXT,
    enabled            INTEGER DEFAULT 1,
    metadata           TEXT           -- JSON object
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    agent_type  TEXT,
    agent_id    TEXT,
    status      TEXT DEFAULT 'pending',
    priority    INTEGER DEFAULT 5,
    input       TEXT,           -- JSON
    output      TEXT,           -- JSON
    error       TEXT,
    created_at  TEXT,
    started_at  TEXT,
    ended_at    TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    task_id     TEXT,
    agent_id    TEXT,
    agent_name  TEXT,
    task_text   TEXT,
    output      TEXT,
    tool_calls  TEXT,           -- JSON array
    iterations  INTEGER,
    started_at  TEXT,
    ended_at    TEXT
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_agent_type ON tasks(agent_type);
CREATE INDEX IF NOT EXISTS idx_tasks_created    ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agents_type      ON agents(type);
CREATE INDEX IF NOT EXISTS idx_runs_task_id     ON runs(task_id);
