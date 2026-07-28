from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def load_evaluation_module() -> ModuleType:
    script = Path(__file__).resolve().parents[1] / "scripts" / "kind-live-evaluation.py"
    spec = importlib.util.spec_from_file_location("kind_live_evaluation", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_crashloop_detection_survives_transient_non_waiting_snapshot() -> None:
    module = load_evaluation_module()
    observed, evidence = module.crashloop_observation(
        {
            "status": {
                "containerStatuses": [
                    {
                        "state": {"terminated": {"reason": "Error", "exitCode": 17}},
                        "lastState": {"terminated": {"reason": "Error", "exitCode": 17}},
                        "restartCount": 4,
                    }
                ]
            }
        },
        17,
    )

    assert observed is True
    assert evidence["last_exit_code"] == 17
    assert evidence["restart_count"] == 4


def test_crashloop_detection_rejects_single_or_unexpected_crash() -> None:
    module = load_evaluation_module()
    observed, _ = module.crashloop_observation(
        {
            "status": {
                "containerStatuses": [
                    {
                        "state": {"running": {}},
                        "lastState": {"terminated": {"reason": "Error", "exitCode": 23}},
                        "restartCount": 1,
                    }
                ]
            }
        },
        17,
    )

    assert observed is False
