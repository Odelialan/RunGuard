BEGIN;

CREATE TABLE IF NOT EXISTS workflow_checkpoints (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES agent_runs(id),
  incident_id TEXT NOT NULL REFERENCES incidents(id),
  phase TEXT NOT NULL,
  state TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (run_id, phase)
);

CREATE INDEX IF NOT EXISTS workflow_checkpoints_incident_created_idx
  ON workflow_checkpoints (incident_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS execution_results_tool_intent_unique_idx
  ON execution_results (tool_intent_id);

CREATE INDEX IF NOT EXISTS agent_runs_recovery_idx
  ON agent_runs (status, started_at)
  WHERE status NOT IN ('RESOLVED', 'DENIED', 'ROLLED_BACK');

CREATE INDEX IF NOT EXISTS incidents_recovery_idx
  ON incidents (status, updated_at)
  WHERE status IN (
    'TRIAGING',
    'INVESTIGATING',
    'PLAN_READY',
    'POLICY_CHECKING',
    'EXECUTING',
    'VERIFYING',
    'ROLLING_BACK'
  );

COMMIT;
