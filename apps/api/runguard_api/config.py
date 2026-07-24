from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path
    cors_origins: tuple[str, ...]
    execution_mode: str
    prompt_version: str
    policy_version: str


def load_settings() -> Settings:
    root = Path(__file__).resolve().parents[4]
    database_path = Path(os.getenv("RUNGUARD_DATABASE_PATH", ".data/runguard.db"))
    if not database_path.is_absolute():
        database_path = root / database_path
    origins = os.getenv(
        "RUNGUARD_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return Settings(
        database_path=database_path,
        cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
        execution_mode=os.getenv("RUNGUARD_EXECUTION_MODE", "simulation"),
        prompt_version=os.getenv("RUNGUARD_PROMPT_VERSION", "1.0.0"),
        policy_version=os.getenv("RUNGUARD_POLICY_VERSION", "1.0.0"),
    )
