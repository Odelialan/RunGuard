from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import IncidentCreate, IncidentStatus
from .telemetry import Telemetry


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class _PostgresConnection:
    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.raw: Any = None

    def __enter__(self) -> _PostgresConnection:
        self.raw = self.pool.getconn()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.raw.commit()
        else:
            self.raw.rollback()
        self.pool.putconn(self.raw)

    @staticmethod
    def _query(query: str, parameters: Any) -> str:
        if isinstance(parameters, dict):
            return re.sub(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", query)
        return query.replace("?", "%s")

    def execute(self, query: str, parameters: Any = None):
        parameters = () if parameters is None else parameters
        return self.raw.execute(self._query(query, parameters), parameters, prepare=False)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self.raw.execute(statement, prepare=False)


class Store:
    def __init__(
        self,
        database_path: Path,
        database_url: str | None = None,
        *,
        seed: bool = True,
        vector_dimensions: int = 1536,
        database_pool_min_size: int = 2,
        database_pool_max_size: int = 20,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.database_path = database_path
        self.database_url = database_url
        self.backend = "postgresql" if database_url else "sqlite"
        self.seed = seed
        self.vector_dimensions = vector_dimensions
        self.telemetry = telemetry
        self._pool: Any = None
        if database_url:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            self._pool = ConnectionPool(
                database_url,
                min_size=database_pool_min_size,
                max_size=database_pool_max_size,
                kwargs={"row_factory": dict_row},
                check=ConnectionPool.check_connection,
                open=True,
                name="runguard-store",
            )
        if not database_url:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def connect(self) -> sqlite3.Connection | _PostgresConnection:
        if self.database_url:
            return _PostgresConnection(self._pool)
        connection = sqlite3.connect(self.database_path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            severity TEXT NOT NULL,
            service TEXT NOT NULL,
            environment TEXT NOT NULL,
            status TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT,
            current_run_id TEXT
        );
        CREATE TABLE IF NOT EXISTS incident_events (
            id TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL REFERENCES incidents(id),
            sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(incident_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS agent_runs (
            id TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL REFERENCES incidents(id),
            graph_version TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            model_config TEXT NOT NULL,
            status TEXT NOT NULL,
            token_usage INTEGER NOT NULL DEFAULT 0,
            tool_calls INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL REFERENCES incidents(id),
            source_type TEXT NOT NULL,
            source_uri TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            metadata TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hypotheses (
            id TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL REFERENCES incidents(id),
            cause TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_ids TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tool_intents (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES agent_runs(id),
            incident_id TEXT NOT NULL REFERENCES incidents(id),
            agent_name TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            environment TEXT NOT NULL,
            resource TEXT NOT NULL,
            arguments TEXT NOT NULL,
            rollback TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS policy_decisions (
            id TEXT PRIMARY KEY,
            tool_intent_id TEXT NOT NULL REFERENCES tool_intents(id),
            decision TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            matched_rule TEXT NOT NULL,
            reason TEXT NOT NULL,
            input_snapshot TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            tool_intent_id TEXT NOT NULL REFERENCES tool_intents(id),
            reviewer TEXT NOT NULL,
            decision TEXT NOT NULL,
            comment TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS execution_results (
            id TEXT PRIMARY KEY,
            tool_intent_id TEXT NOT NULL REFERENCES tool_intents(id),
            result TEXT NOT NULL,
            before_snapshot TEXT NOT NULL,
            after_snapshot TEXT NOT NULL,
            rollback_result TEXT,
            duration_ms INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trace_spans (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES agent_runs(id),
            incident_id TEXT NOT NULL REFERENCES incidents(id),
            span_type TEXT NOT NULL,
            name TEXT NOT NULL,
            agent TEXT,
            status TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            attributes TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS eval_runs (
            id TEXT PRIMARY KEY,
            suite TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            status TEXT NOT NULL,
            metrics TEXT NOT NULL,
            cases TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS postmortems (
            id TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL UNIQUE REFERENCES incidents(id),
            run_id TEXT,
            status TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            impact TEXT NOT NULL,
            root_cause TEXT NOT NULL,
            contributing_factors TEXT NOT NULL,
            timeline TEXT NOT NULL,
            remediation TEXT NOT NULL,
            action_items TEXT NOT NULL,
            lessons TEXT NOT NULL,
            generated_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workflow_checkpoints (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES agent_runs(id),
            incident_id TEXT NOT NULL REFERENCES incidents(id),
            phase TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, phase)
        );
        CREATE INDEX IF NOT EXISTS workflow_checkpoints_incident_created_idx
            ON workflow_checkpoints (incident_id, created_at);
        """
        with self._lock, self.connect() as connection:
            if self.backend == "postgresql":
                connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            connection.executescript(schema)
            if self.backend == "postgresql":
                connection.execute(
                    f"ALTER TABLE evidence ADD COLUMN IF NOT EXISTS "
                    f"embedding vector({self.vector_dimensions})"
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS evidence_embedding_hnsw
                    ON evidence USING hnsw (embedding vector_cosine_ops)
                    WHERE embedding IS NOT NULL
                    """
                )
                self._apply_postgres_migrations(connection)
            count_row = connection.execute("SELECT COUNT(*) AS count FROM incidents").fetchone()
            count = count_row["count"] if isinstance(count_row, dict) else count_row[0]
            if count == 0 and self.seed:
                self._seed(connection)

    @staticmethod
    def _apply_postgres_migrations(connection: _PostgresConnection) -> None:
        configured_dir = os.getenv("RUNGUARD_MIGRATIONS_DIR")
        candidates = [
            Path.cwd() / "deploy" / "postgres",
            Path(__file__).resolve().parents[3] / "deploy" / "postgres",
        ]
        migrations_dir = (
            Path(configured_dir)
            if configured_dir
            else next((path for path in candidates if path.is_dir()), candidates[0])
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        connection.execute("SELECT pg_advisory_xact_lock(2026072401)")
        if not migrations_dir.is_dir():
            return
        import hashlib

        for path in sorted(migrations_dir.glob("*.sql")):
            version = path.name
            script = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
            existing = connection.execute(
                "SELECT checksum FROM schema_migrations WHERE version = ?",
                (version,),
            ).fetchone()
            if existing:
                if existing["checksum"] != checksum:
                    raise RuntimeError(
                        f"Applied migration {version} has been modified; refusing startup."
                    )
                continue
            transactional_script = re.sub(
                r"(?im)^\s*(BEGIN|COMMIT)\s*;\s*$",
                "",
                script,
            )
            connection.executescript(transactional_script)
            connection.execute(
                """
                INSERT INTO schema_migrations (version, checksum)
                VALUES (?, ?)
                """,
                (version, checksum),
            )

    def _next_incident_id(self, connection: Any) -> str:
        if self.backend == "postgresql":
            connection.execute("SELECT pg_advisory_xact_lock(20260724)")
        year = datetime.now(UTC).year
        row = connection.execute(
            "SELECT id FROM incidents WHERE id LIKE ? ORDER BY id DESC LIMIT 1",
            (f"INC-{year}-%",),
        ).fetchone()
        next_number = int(row["id"].split("-")[-1]) + 1 if row else 17
        return f"INC-{year}-{next_number:05d}"

    def _seed(self, connection: sqlite3.Connection) -> None:
        now = datetime.now(UTC)
        seeds = [
            (
                IncidentCreate(
                    title="order-api P95 latency exceeds 2s",
                    severity="P1",
                    service="order-api",
                    environment="production",
                    description="P95 latency remained above 2 seconds for five minutes.",
                ),
                IncidentStatus.WAITING_APPROVAL,
                now - timedelta(minutes=22),
            ),
            (
                IncidentCreate(
                    title="payment-api pods restarting",
                    severity="P2",
                    service="payment-api",
                    environment="staging",
                    description="CrashLoopBackOff detected after configuration rollout.",
                ),
                IncidentStatus.INVESTIGATING,
                now - timedelta(minutes=11),
            ),
            (
                IncidentCreate(
                    title="Redis command latency elevated",
                    severity="P2",
                    service="redis",
                    environment="staging",
                    description="Redis command duration above baseline.",
                ),
                IncidentStatus.RESOLVED,
                now - timedelta(hours=3),
            ),
            (
                IncidentCreate(
                    title="frontend error budget burn",
                    severity="P3",
                    service="frontend",
                    environment="production",
                    description="5xx error ratio elevated but within remaining budget.",
                ),
                IncidentStatus.NEW,
                now - timedelta(minutes=4),
            ),
        ]
        for index, (payload, status, created_at) in enumerate(seeds):
            incident_id = f"INC-{now.year}-{17 + index:05d}"
            resolved_at = (
                created_at + timedelta(minutes=18)
                if status == IncidentStatus.RESOLVED
                else None
            )
            connection.execute(
                """
                INSERT INTO incidents
                    (id, title, severity, service, environment, status, description,
                     created_at, updated_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    payload.title,
                    str(payload.severity),
                    payload.service,
                    payload.environment,
                    str(status),
                    payload.description,
                    created_at.isoformat(),
                    (resolved_at or created_at).isoformat(),
                    resolved_at.isoformat() if resolved_at else None,
                ),
            )
            self._append_event_tx(
                connection,
                incident_id,
                "incident.created",
                "event-gateway",
                {"source": "seed", "status": "NEW"},
                created_at.isoformat(),
            )
            if status != IncidentStatus.NEW:
                self._append_event_tx(
                    connection,
                    incident_id,
                    "incident.status_changed",
                    "commander-agent",
                    {"from": "NEW", "to": str(status)},
                    (created_at + timedelta(seconds=30)).isoformat(),
                )
        self._seed_demo_run(connection, f"INC-{now.year}-00017", now - timedelta(minutes=21))
        self._seed_resolved_run(connection, f"INC-{now.year}-00019", now - timedelta(hours=3))

    def _seed_demo_run(
        self,
        connection: sqlite3.Connection,
        incident_id: str,
        started_at: datetime,
    ) -> None:
        run_id = f"RUN-{uuid4().hex[:8].upper()}"
        connection.execute(
            """
            INSERT INTO agent_runs
                (id, incident_id, graph_version, prompt_version, model_config, status,
                 token_usage, tool_calls, started_at)
            VALUES (?, ?, 'incident-response-v1', '1.0.0', ?, 'WAITING_APPROVAL', 3842, 7, ?)
            """,
            (
                run_id,
                incident_id,
                json_dumps({"provider": "demo", "model": "recorded"}),
                started_at.isoformat(),
            ),
        )
        connection.execute(
            "UPDATE incidents SET current_run_id = ? WHERE id = ?",
            (run_id, incident_id),
        )
        evidence = [
            (
                "EV-101",
                "prometheus",
                "prometheus://query/order-api-p95",
                "Latency increase",
                "P95 latency rose from 340ms to 2.84s at 14:03 UTC.",
                "2.84s P95",
            ),
            (
                "EV-103",
                "kubernetes",
                "k8s://production/deployment/order-api",
                "OOMKilled events",
                "Three order-api pods were OOMKilled 19 times since the rollout.",
                "19 restarts",
            ),
            (
                "EV-109",
                "github",
                "github://deployments/order-api/8a71c2d",
                "Resource limit changed",
                "Deployment 8a71c2d reduced the memory limit from 1Gi to 256Mi.",
                "1Gi → 256Mi",
            ),
            (
                "EV-111",
                "loki",
                "loki://query/order-api-oom",
                "Allocation failures",
                "Runtime allocation failures align with every latency spike.",
                "correlated",
            ),
        ]
        for ev_id, source, uri, title, content, summary in evidence:
            connection.execute(
                """
                INSERT INTO evidence
                    (id, incident_id, source_type, source_uri, title, content, observed_at,
                     content_hash, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ev_id,
                    incident_id,
                    source,
                    uri,
                    title,
                    content,
                    (started_at + timedelta(minutes=3)).isoformat(),
                    uuid4().hex,
                    json_dumps({"summary": summary, "confidence": 0.96}),
                ),
            )
        connection.execute(
            "INSERT INTO hypotheses VALUES (?, ?, ?, ?, ?)",
            (
                "HYP-101",
                incident_id,
                "order-api memory limit is too low after the latest deployment",
                0.94,
                json_dumps(["EV-101", "EV-103", "EV-109", "EV-111"]),
            ),
        )
        intent_id = f"INT-{uuid4().hex[:8].upper()}"
        connection.execute(
            """
            INSERT INTO tool_intents
                (id, run_id, incident_id, agent_name, tool_name, environment, resource,
                 arguments, rollback, risk_level, idempotency_key, status, created_at)
            VALUES (?, ?, ?, 'remediation-agent', 'kubernetes.patch_deployment',
                    'production', ?, ?, ?, 'R2', ?, 'WAITING_APPROVAL', ?)
            """,
            (
                intent_id,
                run_id,
                incident_id,
                json_dumps({"namespace": "production", "kind": "Deployment", "name": "order-api"}),
                json_dumps({"memory_limit": "1Gi"}),
                json_dumps({"memory_limit": "256Mi"}),
                f"{incident_id.lower()}-action-01",
                (started_at + timedelta(minutes=8)).isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO policy_decisions
                (id, tool_intent_id, decision, policy_version, matched_rule, reason,
                 input_snapshot, created_at)
            VALUES (?, ?, 'require_approval', '1.0.0', 'prod-write-requires-human',
                    'Production write operation requires SRE approval', ?, ?)
            """,
            (
                f"POL-{uuid4().hex[:8].upper()}",
                intent_id,
                json_dumps(
                    {
                        "environment": "production",
                        "tool": "kubernetes.patch_deployment",
                        "risk_level": "R2",
                        "has_rollback": True,
                    }
                ),
                (started_at + timedelta(minutes=8, seconds=200)).isoformat(),
            ),
        )
        spans = [
            ("router", "incident.classify", "commander", 188),
            ("agent", "commander.plan", "commander", 824),
            ("retrieval", "prometheus.query_range", "investigator", 312),
            ("tool", "kubernetes.get_events", "investigator", 438),
            ("retrieval", "loki.query", "investigator", 289),
            ("agent", "investigator.correlate", "investigator", 1440),
            ("llm", "remediation.generate_plan", "remediation", 1102),
            ("policy", "opa.evaluate", "policy-gateway", 42),
            ("approval", "human.interrupt", "orchestrator", 3),
        ]
        for offset, (span_type, name, agent, duration) in enumerate(spans):
            self._insert_trace_tx(
                connection,
                run_id,
                incident_id,
                span_type,
                name,
                agent,
                "OK" if span_type != "approval" else "PENDING",
                duration,
                {"sequence": offset + 1},
                (started_at + timedelta(minutes=offset)).isoformat(),
            )

    def _seed_resolved_run(
        self,
        connection: sqlite3.Connection,
        incident_id: str,
        started_at: datetime,
    ) -> None:
        run_id = f"RUN-{uuid4().hex[:8].upper()}"
        connection.execute(
            """
            INSERT INTO agent_runs VALUES
                (?, ?, 'incident-response-v1', '1.0.0', ?, 'RESOLVED', 2921, 6, ?, ?)
            """,
            (
                run_id,
                incident_id,
                json_dumps({"provider": "demo", "model": "recorded"}),
                started_at.isoformat(),
                (started_at + timedelta(minutes=18)).isoformat(),
            ),
        )
        connection.execute(
            "UPDATE incidents SET current_run_id = ? WHERE id = ?",
            (run_id, incident_id),
        )
        self._insert_trace_tx(
            connection,
            run_id,
            incident_id,
            "verification",
            "redis.latency.recovered",
            "orchestrator",
            "OK",
            1203,
            {"before_ms": 870, "after_ms": 34},
            (started_at + timedelta(minutes=17)).isoformat(),
        )

    def _append_event_tx(
        self,
        connection: sqlite3.Connection,
        incident_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        created_at: str | None = None,
    ) -> dict[str, Any]:
        sequence_row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
            FROM incident_events WHERE incident_id = ?
            """,
            (incident_id,),
        ).fetchone()
        sequence = (
            sequence_row["next_sequence"]
            if isinstance(sequence_row, dict)
            else sequence_row[0]
        )
        event = {
            "id": f"EVT-{uuid4().hex[:10].upper()}",
            "incident_id": incident_id,
            "sequence": sequence,
            "event_type": event_type,
            "actor": actor,
            "payload": payload,
            "created_at": created_at or utc_now(),
        }
        connection.execute(
            """
            INSERT INTO incident_events
                (id, incident_id, sequence, event_type, actor, payload, created_at)
            VALUES (:id, :incident_id, :sequence, :event_type, :actor, :payload, :created_at)
            """,
            {**event, "payload": json_dumps(payload)},
        )
        return event

    def create_incident(self, payload: IncidentCreate, source: str = "manual") -> dict[str, Any]:
        with self._lock, self.connect() as connection:
            incident_id = self._next_incident_id(connection)
            now = utc_now()
            connection.execute(
                """
                INSERT INTO incidents
                    (id, title, severity, service, environment, status, description,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'NEW', ?, ?, ?)
                """,
                (
                    incident_id,
                    payload.title,
                    str(payload.severity),
                    payload.service,
                    payload.environment,
                    payload.description,
                    now,
                    now,
                ),
            )
            self._append_event_tx(
                connection,
                incident_id,
                "incident.created",
                "event-gateway",
                {"source": source, "status": "NEW"},
            )
        return self.get_incident(incident_id)

    def list_incidents(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT i.*,
                    (SELECT COUNT(*) FROM evidence e WHERE e.incident_id = i.id) AS evidence_count,
                    (SELECT COUNT(*) FROM tool_intents ti
                     WHERE ti.incident_id = i.id
                       AND ti.status = 'WAITING_APPROVAL') AS pending_approvals
                FROM incidents i ORDER BY i.created_at DESC
                """
            ).fetchall()
        return [self._row(row) for row in rows]

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            incident_row = connection.execute(
                "SELECT * FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if not incident_row:
                raise KeyError(incident_id)
            incident = self._row(incident_row)
            incident["events"] = [
                self._decode(row, "payload")
                for row in connection.execute(
                    "SELECT * FROM incident_events WHERE incident_id = ? ORDER BY sequence",
                    (incident_id,),
                ).fetchall()
            ]
            incident["evidence"] = [
                self._decode(row, "metadata")
                for row in connection.execute(
                    "SELECT * FROM evidence WHERE incident_id = ? ORDER BY observed_at DESC",
                    (incident_id,),
                ).fetchall()
            ]
            incident["hypotheses"] = [
                self._decode(row, "evidence_ids")
                for row in connection.execute(
                    "SELECT * FROM hypotheses WHERE incident_id = ? ORDER BY confidence DESC",
                    (incident_id,),
                ).fetchall()
            ]
            incident["runs"] = [
                self._decode(row, "model_config")
                for row in connection.execute(
                    "SELECT * FROM agent_runs WHERE incident_id = ? ORDER BY started_at DESC",
                    (incident_id,),
                ).fetchall()
            ]
            incident["tool_intents"] = [
                self._decode(row, "resource", "arguments", "rollback")
                for row in connection.execute(
                    """
                    SELECT ti.*, pd.decision AS policy_decision, pd.matched_rule, pd.reason
                    FROM tool_intents ti
                    LEFT JOIN policy_decisions pd ON pd.id = (
                        SELECT latest.id FROM policy_decisions latest
                        WHERE latest.tool_intent_id = ti.id
                        ORDER BY latest.created_at DESC LIMIT 1
                    )
                    WHERE ti.incident_id = ? ORDER BY ti.created_at DESC
                    """,
                    (incident_id,),
                ).fetchall()
            ]
        return incident

    def update_status(
        self,
        incident_id: str,
        status: IncidentStatus,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock, self.connect() as connection:
            current = connection.execute(
                "SELECT status FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if not current:
                raise KeyError(incident_id)
            now = utc_now()
            connection.execute(
                """
                UPDATE incidents SET status = ?, updated_at = ?,
                    resolved_at = CASE WHEN ? = 'RESOLVED' THEN ? ELSE resolved_at END
                WHERE id = ?
                """,
                (str(status), now, str(status), now, incident_id),
            )
            self._append_event_tx(
                connection,
                incident_id,
                "incident.status_changed",
                actor,
                {"from": current["status"], "to": str(status), **(payload or {})},
            )
        return self.get_incident(incident_id)

    def create_run(
        self,
        incident_id: str,
        prompt_version: str,
        *,
        graph_version: str = "incident-response-v1",
        model_config: dict[str, Any] | None = None,
    ) -> str:
        run_id = f"RUN-{uuid4().hex[:8].upper()}"
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs
                    (id, incident_id, graph_version, prompt_version, model_config,
                     status, started_at)
                VALUES (?, ?, ?, ?, ?, 'RUNNING', ?)
                """,
                (
                    run_id,
                    incident_id,
                    graph_version,
                    prompt_version,
                    json_dumps(
                        model_config or {"provider": "deterministic", "model": "demo-v1"}
                    ),
                    utc_now(),
                ),
            )
            connection.execute(
                "UPDATE incidents SET current_run_id = ? WHERE id = ?",
                (run_id, incident_id),
            )
        return run_id

    def add_evidence(
        self,
        incident_id: str,
        evidence: Iterable[dict[str, Any]],
    ) -> list[str]:
        ids: list[str] = []
        with self._lock, self.connect() as connection:
            for item in evidence:
                evidence_id = f"EV-{uuid4().hex[:6].upper()}"
                ids.append(evidence_id)
                connection.execute(
                    """
                    INSERT INTO evidence
                        (id, incident_id, source_type, source_uri, title, content,
                         observed_at, content_hash, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence_id,
                        incident_id,
                        item["source_type"],
                        item["source_uri"],
                        item["title"],
                        item["content"],
                        utc_now(),
                        uuid4().hex,
                        json_dumps(item.get("metadata", {})),
                    ),
                )
        return ids

    def add_hypothesis(
        self,
        incident_id: str,
        cause: str,
        confidence: float,
        evidence_ids: list[str],
    ) -> str:
        hypothesis_id = f"HYP-{uuid4().hex[:8].upper()}"
        with self._lock, self.connect() as connection:
            connection.execute(
                "INSERT INTO hypotheses VALUES (?, ?, ?, ?, ?)",
                (hypothesis_id, incident_id, cause, confidence, json_dumps(evidence_ids)),
            )
        return hypothesis_id

    def add_trace(
        self,
        run_id: str,
        incident_id: str,
        span_type: str,
        name: str,
        agent: str | None,
        status: str,
        duration_ms: int,
        attributes: dict[str, Any] | None = None,
    ) -> str:
        trace_attributes = dict(attributes or {})
        if self.telemetry is not None:
            trace_id, span_id = self.telemetry.record(
                name,
                duration_ms,
                {
                    **trace_attributes,
                    "run_id": run_id,
                    "incident_id": incident_id,
                    "span_type": span_type,
                    "agent": agent or "system",
                },
                status,
            )
            if trace_id and span_id:
                trace_attributes.update({"otel_trace_id": trace_id, "otel_span_id": span_id})
        with self._lock, self.connect() as connection:
            return self._insert_trace_tx(
                connection,
                run_id,
                incident_id,
                span_type,
                name,
                agent,
                status,
                duration_ms,
                trace_attributes,
                utc_now(),
            )

    def _insert_trace_tx(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        incident_id: str,
        span_type: str,
        name: str,
        agent: str | None,
        status: str,
        duration_ms: int,
        attributes: dict[str, Any],
        created_at: str,
    ) -> str:
        span_id = f"SPN-{uuid4().hex[:10].upper()}"
        connection.execute(
            "INSERT INTO trace_spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                span_id,
                run_id,
                incident_id,
                span_type,
                name,
                agent,
                status,
                duration_ms,
                json_dumps(attributes),
                created_at,
            ),
        )
        return span_id

    def create_intent(
        self,
        run_id: str,
        incident_id: str,
        tool_name: str,
        environment: str,
        resource: dict[str, Any],
        arguments: dict[str, Any],
        rollback: dict[str, Any],
        risk_level: str,
    ) -> dict[str, Any]:
        intent_id = f"INT-{uuid4().hex[:8].upper()}"
        idempotency_key = f"{incident_id.lower()}-{tool_name.replace('.', '-')}-01"
        with self._lock, self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM tool_intents WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if existing["status"] not in {"EXECUTED", "ROLLED_BACK"}:
                    connection.execute(
                        """
                        UPDATE tool_intents
                        SET run_id = ?, resource = ?, arguments = ?, rollback = ?,
                            risk_level = ?, status = 'PENDING', created_at = ?
                        WHERE id = ?
                        """,
                        (
                            run_id,
                            json_dumps(resource),
                            json_dumps(arguments),
                            json_dumps(rollback),
                            risk_level,
                            utc_now(),
                            existing["id"],
                        ),
                    )
                    existing = connection.execute(
                        "SELECT * FROM tool_intents WHERE id = ?",
                        (existing["id"],),
                    ).fetchone()
                return self._decode(existing, "resource", "arguments", "rollback")
            connection.execute(
                """
                INSERT INTO tool_intents
                    (id, run_id, incident_id, agent_name, tool_name, environment,
                     resource, arguments, rollback, risk_level, idempotency_key,
                     status, created_at)
                VALUES (?, ?, ?, 'remediation-agent', ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                """,
                (
                    intent_id,
                    run_id,
                    incident_id,
                    tool_name,
                    environment,
                    json_dumps(resource),
                    json_dumps(arguments),
                    json_dumps(rollback),
                    risk_level,
                    idempotency_key,
                    utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM tool_intents WHERE id = ?", (intent_id,)
            ).fetchone()
        return self._decode(row, "resource", "arguments", "rollback")

    def record_policy(
        self,
        intent_id: str,
        policy_version: str,
        result: dict[str, Any],
        input_snapshot: dict[str, Any],
    ) -> None:
        status_map = {
            "allow": "APPROVED",
            "require_approval": "WAITING_APPROVAL",
            "deny": "DENIED",
        }
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO policy_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"POL-{uuid4().hex[:8].upper()}",
                    intent_id,
                    result["decision"],
                    policy_version,
                    result["matched_policy"],
                    result["reason"],
                    json_dumps(input_snapshot),
                    utc_now(),
                ),
            )
            connection.execute(
                "UPDATE tool_intents SET status = ? WHERE id = ?",
                (status_map[result["decision"]], intent_id),
            )

    def list_approvals(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT ti.*, i.title AS incident_title, i.severity, pd.decision,
                       pd.matched_rule, pd.reason, pd.input_snapshot
                FROM tool_intents ti
                JOIN incidents i ON i.id = ti.incident_id
                JOIN policy_decisions pd ON pd.id = (
                    SELECT latest.id FROM policy_decisions latest
                    WHERE latest.tool_intent_id = ti.id
                    ORDER BY latest.created_at DESC LIMIT 1
                )
                WHERE ti.status = 'WAITING_APPROVAL'
                ORDER BY ti.created_at DESC
                """
            ).fetchall()
        return [
            self._decode(
                row,
                "resource",
                "arguments",
                "rollback",
                "input_snapshot",
            )
            for row in rows
        ]

    def get_intent(self, intent_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT ti.*, pd.decision, pd.matched_rule, pd.reason
                FROM tool_intents ti
                LEFT JOIN policy_decisions pd ON pd.id = (
                    SELECT latest.id FROM policy_decisions latest
                    WHERE latest.tool_intent_id = ti.id
                    ORDER BY latest.created_at DESC LIMIT 1
                )
                WHERE ti.id = ?
                """,
                (intent_id,),
            ).fetchone()
        if not row:
            raise KeyError(intent_id)
        return self._decode(row, "resource", "arguments", "rollback")

    def decide_approval(
        self,
        intent_id: str,
        reviewer: str,
        decision: str,
        comment: str,
    ) -> dict[str, Any]:
        status = "APPROVED" if decision == "approved" else "REJECTED"
        with self._lock, self.connect() as connection:
            row = connection.execute(
                "SELECT incident_id, status FROM tool_intents WHERE id = ?", (intent_id,)
            ).fetchone()
            if not row:
                raise KeyError(intent_id)
            automatic = (
                decision == "approved"
                and reviewer == "policy-gateway"
                and row["status"] == "APPROVED"
            )
            if row["status"] != "WAITING_APPROVAL" and not automatic:
                raise ValueError(
                    f"Tool intent is not awaiting approval: {row['status']}"
                )
            if not automatic:
                updated = connection.execute(
                    """
                    UPDATE tool_intents SET status = ?
                    WHERE id = ? AND status = 'WAITING_APPROVAL'
                    """,
                    (status, intent_id),
                )
                if updated.rowcount != 1:
                    raise ValueError("Tool intent approval was decided concurrently.")
            connection.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"APR-{uuid4().hex[:8].upper()}",
                    intent_id,
                    reviewer,
                    decision,
                    comment,
                    utc_now(),
                ),
            )
            self._append_event_tx(
                connection,
                row["incident_id"],
                f"approval.{decision}",
                reviewer,
                {"tool_intent_id": intent_id, "comment": comment},
            )
        return self.get_intent(intent_id)

    def edit_intent(
        self,
        intent_id: str,
        arguments: dict[str, Any],
        reviewer: str,
        comment: str,
    ) -> dict[str, Any]:
        with self._lock, self.connect() as connection:
            row = connection.execute(
                "SELECT incident_id FROM tool_intents WHERE id = ?", (intent_id,)
            ).fetchone()
            if not row:
                raise KeyError(intent_id)
            current = connection.execute(
                "SELECT status FROM tool_intents WHERE id = ?",
                (intent_id,),
            ).fetchone()
            if current["status"] != "WAITING_APPROVAL":
                raise ValueError(
                    f"Tool intent is not editable: {current['status']}"
                )
            updated = connection.execute(
                """
                UPDATE tool_intents SET arguments = ?, status = 'PENDING'
                WHERE id = ? AND status = 'WAITING_APPROVAL'
                """,
                (json_dumps(arguments), intent_id),
            )
            if updated.rowcount != 1:
                raise ValueError("Tool intent was edited or approved concurrently.")
            now = utc_now()
            connection.execute(
                """
                UPDATE incidents SET status = 'POLICY_CHECKING', updated_at = ?
                WHERE id = ?
                """,
                (now, row["incident_id"]),
            )
            self._append_event_tx(
                connection,
                row["incident_id"],
                "incident.status_changed",
                reviewer,
                {
                    "from": "WAITING_APPROVAL",
                    "to": "POLICY_CHECKING",
                    "reason": "Edited intent requires policy reevaluation.",
                },
                now,
            )
            self._append_event_tx(
                connection,
                row["incident_id"],
                "approval.intent_edited",
                reviewer,
                {"tool_intent_id": intent_id, "comment": comment},
            )
        return self.get_intent(intent_id)

    def update_intent_risk(self, intent_id: str, risk_level: str) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                "UPDATE tool_intents SET risk_level = ? WHERE id = ?",
                (risk_level, intent_id),
            )

    def record_execution(
        self,
        intent_id: str,
        result: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
        duration_ms: int,
    ) -> dict[str, Any]:
        with self._lock, self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM execution_results WHERE tool_intent_id = ?",
                (intent_id,),
            ).fetchone()
            if existing:
                return self._decode(
                    existing,
                    "result",
                    "before_snapshot",
                    "after_snapshot",
                    "rollback_result",
                )
            execution_id = f"EXE-{uuid4().hex[:8].upper()}"
            connection.execute(
                """
                INSERT INTO execution_results
                    (id, tool_intent_id, result, before_snapshot, after_snapshot,
                     rollback_result, duration_ms, created_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    execution_id,
                    intent_id,
                    json_dumps(result),
                    json_dumps(before),
                    json_dumps(after),
                    duration_ms,
                    utc_now(),
                ),
            )
            connection.execute(
                "UPDATE tool_intents SET status = 'EXECUTED' WHERE id = ?",
                (intent_id,),
            )
            row = connection.execute(
                "SELECT * FROM execution_results WHERE id = ?", (execution_id,)
            ).fetchone()
        return self._decode(
            row,
            "result",
            "before_snapshot",
            "after_snapshot",
            "rollback_result",
        )

    def record_rollback(
        self,
        intent_id: str,
        rollback_result: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE execution_results SET rollback_result = ?
                WHERE tool_intent_id = ?
                """,
                (json_dumps(rollback_result), intent_id),
            )
            connection.execute(
                "UPDATE tool_intents SET status = 'ROLLED_BACK' WHERE id = ?",
                (intent_id,),
            )
            row = connection.execute(
                "SELECT * FROM execution_results WHERE tool_intent_id = ?",
                (intent_id,),
            ).fetchone()
        if not row:
            raise KeyError(intent_id)
        return self._decode(
            row,
            "result",
            "before_snapshot",
            "after_snapshot",
            "rollback_result",
        )

    def upsert_evidence_embedding(self, evidence_id: str, embedding: list[float]) -> None:
        if self.backend != "postgresql":
            return
        with self._lock, self.connect() as connection:
            from pgvector.psycopg import register_vector

            register_vector(connection.raw)
            connection.execute(
                "UPDATE evidence SET embedding = ? WHERE id = ?",
                (embedding, evidence_id),
            )

    def semantic_evidence(self, embedding: list[float], limit: int = 8) -> list[dict[str, Any]]:
        if self.backend != "postgresql":
            return []
        with self.connect() as connection:
            from pgvector.psycopg import register_vector

            register_vector(connection.raw)
            rows = connection.execute(
                """
                SELECT id, incident_id, source_type, source_uri, title, content, observed_at,
                       metadata, 1 - (embedding <=> ?) AS similarity
                FROM evidence
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> ?
                LIMIT ?
                """,
                (embedding, embedding, limit),
            ).fetchall()
        return [self._decode(row, "metadata") for row in rows]

    def upsert_postmortem(self, incident_id: str, document: dict[str, Any]) -> dict[str, Any]:
        postmortem_id = document.get("id") or f"PM-{uuid4().hex[:8].upper()}"
        now = utc_now()
        fields = {
            "id": postmortem_id,
            "incident_id": incident_id,
            "run_id": document.get("run_id"),
            "status": document.get("status", "FINAL"),
            "title": document["title"],
            "summary": document["summary"],
            "impact": document["impact"],
            "root_cause": document["root_cause"],
            "contributing_factors": json_dumps(document.get("contributing_factors", [])),
            "timeline": json_dumps(document.get("timeline", [])),
            "remediation": json_dumps(document.get("remediation", [])),
            "action_items": json_dumps(document.get("action_items", [])),
            "lessons": json_dumps(document.get("lessons", [])),
            "generated_by": document.get("generated_by", "reporter-agent"),
            "created_at": now,
            "updated_at": now,
        }
        with self._lock, self.connect() as connection:
            existing = connection.execute(
                "SELECT created_at FROM postmortems WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if existing:
                fields["created_at"] = existing["created_at"]
                connection.execute(
                    """
                    UPDATE postmortems SET run_id = :run_id, status = :status, title = :title,
                        summary = :summary, impact = :impact, root_cause = :root_cause,
                        contributing_factors = :contributing_factors, timeline = :timeline,
                        remediation = :remediation, action_items = :action_items,
                        lessons = :lessons, generated_by = :generated_by,
                        updated_at = :updated_at
                    WHERE incident_id = :incident_id
                    """,
                    fields,
                )
            else:
                connection.execute(
                    """
                    INSERT INTO postmortems
                        (id, incident_id, run_id, status, title, summary, impact, root_cause,
                         contributing_factors, timeline, remediation, action_items, lessons,
                         generated_by, created_at, updated_at)
                    VALUES (:id, :incident_id, :run_id, :status, :title, :summary, :impact,
                            :root_cause, :contributing_factors, :timeline, :remediation,
                            :action_items, :lessons, :generated_by, :created_at, :updated_at)
                    """,
                    fields,
                )
        return self.get_postmortem(incident_id)

    def get_postmortem(self, incident_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM postmortems WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
        if not row:
            raise KeyError(incident_id)
        return self._decode(
            row,
            "contributing_factors",
            "timeline",
            "remediation",
            "action_items",
            "lessons",
        )

    def database_health(self) -> str:
        try:
            with self.connect() as connection:
                connection.execute("SELECT 1").fetchone()
            return "ready"
        except Exception:
            return "unavailable"

    def database_pool_stats(self) -> dict[str, int] | None:
        if self._pool is None:
            return None
        stats = self._pool.get_stats()
        allowed = {
            "pool_min",
            "pool_max",
            "pool_size",
            "pool_available",
            "requests_waiting",
        }
        return {
            key: int(value)
            for key, value in stats.items()
            if key in allowed
        }

    def finish_run(
        self,
        run_id: str,
        status: str,
        token_usage: int,
        tool_calls: int,
    ) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE agent_runs SET status = ?, token_usage = ?, tool_calls = ?,
                    finished_at = CASE WHEN ? IN ('RESOLVED', 'DENIED', 'ROLLED_BACK')
                                       THEN ? ELSE finished_at END
                WHERE id = ?
                """,
                (status, token_usage, tool_calls, status, utc_now(), run_id),
            )

    def checkpoint_workflow(
        self,
        run_id: str,
        incident_id: str,
        phase: str,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        checkpoint = {
            "id": f"CHK-{uuid4().hex[:10].upper()}",
            "run_id": run_id,
            "incident_id": incident_id,
            "phase": phase,
            "state": state or {},
            "created_at": utc_now(),
        }
        with self._lock, self.connect() as connection:
            connection.execute(
                "DELETE FROM workflow_checkpoints WHERE run_id = ? AND phase = ?",
                (run_id, phase),
            )
            connection.execute(
                """
                INSERT INTO workflow_checkpoints
                    (id, run_id, incident_id, phase, state, created_at)
                VALUES (:id, :run_id, :incident_id, :phase, :state, :created_at)
                """,
                {**checkpoint, "state": json_dumps(checkpoint["state"])},
            )
        return checkpoint

    def list_workflow_checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_checkpoints
                WHERE run_id = ? ORDER BY created_at
                """,
                (run_id,),
            ).fetchall()
        return [self._decode(row, "state") for row in rows]

    def latest_workflow_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM workflow_checkpoints
                WHERE run_id = ? ORDER BY created_at DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return self._decode(row, "state") if row else None

    def list_recoverable_incidents(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id FROM incidents
                WHERE status IN (
                    'TRIAGING',
                    'INVESTIGATING',
                    'PLAN_READY',
                    'POLICY_CHECKING',
                    'EXECUTING',
                    'VERIFYING',
                    'ROLLING_BACK'
                )
                ORDER BY updated_at
                """
            ).fetchall()
        return [row["id"] for row in rows]

    def list_traces(self, run_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if run_id:
                rows = connection.execute(
                    """
                    SELECT ts.*, i.title AS incident_title
                    FROM trace_spans ts JOIN incidents i ON i.id = ts.incident_id
                    WHERE ts.run_id = ? ORDER BY ts.created_at
                    """,
                    (run_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT ts.*, i.title AS incident_title
                    FROM trace_spans ts JOIN incidents i ON i.id = ts.incident_id
                    ORDER BY ts.created_at DESC LIMIT 100
                    """
                ).fetchall()
        return [self._decode(row, "attributes") for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            run = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if not run:
                raise KeyError(run_id)
            result = self._decode(run, "model_config")
            result["events"] = [
                self._decode(row, "attributes")
                for row in connection.execute(
                    "SELECT * FROM trace_spans WHERE run_id = ? ORDER BY created_at",
                    (run_id,),
                ).fetchall()
            ]
            result["checkpoints"] = [
                self._decode(row, "state")
                for row in connection.execute(
                    """
                    SELECT * FROM workflow_checkpoints
                    WHERE run_id = ? ORDER BY created_at
                    """,
                    (run_id,),
                ).fetchall()
            ]
        return result

    def record_eval_run(
        self,
        suite: str,
        model: str,
        prompt_version: str,
        metrics: dict[str, Any],
        cases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        eval_id = f"EVAL-{uuid4().hex[:8].upper()}"
        with self._lock, self.connect() as connection:
            connection.execute(
                "INSERT INTO eval_runs VALUES (?, ?, ?, ?, 'COMPLETED', ?, ?, ?)",
                (
                    eval_id,
                    suite,
                    model,
                    prompt_version,
                    json_dumps(metrics),
                    json_dumps(cases),
                    utc_now(),
                ),
            )
        return self.get_eval_run(eval_id)

    def list_eval_runs(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM eval_runs ORDER BY created_at DESC"
            ).fetchall()
        return [self._decode(row, "metrics", "cases") for row in rows]

    def get_eval_run(self, eval_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM eval_runs WHERE id = ?", (eval_id,)
            ).fetchone()
        if not row:
            raise KeyError(eval_id)
        return self._decode(row, "metrics", "cases")

    def overview(self) -> dict[str, Any]:
        with self.connect() as connection:
            status_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM incidents GROUP BY status"
            ).fetchall()
            severity_rows = connection.execute(
                "SELECT severity, COUNT(*) AS count FROM incidents GROUP BY severity"
            ).fetchall()
            totals = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM incidents) AS incidents,
                    (SELECT COUNT(*) FROM incidents
                     WHERE status NOT IN ('RESOLVED','CANCELLED','DENIED','ROLLED_BACK')) AS active,
                    (SELECT COUNT(*) FROM tool_intents
                     WHERE status = 'WAITING_APPROVAL') AS approvals,
                    (SELECT COUNT(*) FROM trace_spans) AS spans,
                    (SELECT COALESCE(SUM(token_usage),0) FROM agent_runs) AS tokens
                """
            ).fetchone()
            recent = connection.execute(
                """
                SELECT i.*, ar.token_usage, ar.tool_calls
                FROM incidents i LEFT JOIN agent_runs ar ON ar.id = i.current_run_id
                ORDER BY i.created_at DESC LIMIT 5
                """
            ).fetchall()
        return {
            **self._row(totals),
            "by_status": {row["status"]: row["count"] for row in status_rows},
            "by_severity": {row["severity"]: row["count"] for row in severity_rows},
            "recent_incidents": [self._row(row) for row in recent],
            "automation_rate": 78,
            "mttr_minutes": 12.4,
            "policy_block_rate": 100,
        }

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _decode(row: Any, *fields: str) -> dict[str, Any]:
        result = dict(row)
        for field in fields:
            value = result.get(field)
            if value is not None and isinstance(value, str):
                result[field] = json.loads(value)
        return result
