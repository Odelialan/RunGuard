package runguard.tool_intent_test

import data.runguard.tool_intent

test_r3_is_denied if {
  result := tool_intent.decision with input as {
    "environment": "production",
    "tool": "shell.execute",
    "risk_level": "R3",
    "has_rollback": false
  }
  result.decision == "deny"
}

test_production_write_requires_human if {
  result := tool_intent.decision with input as {
    "environment": "production",
    "tool": "kubernetes.patch_deployment",
    "risk_level": "R2",
    "has_rollback": true,
    "rollback": {"memory_limit": "256Mi"}
  }
  result.decision == "require_approval"
}

test_reversible_staging_write_is_allowed if {
  result := tool_intent.decision with input as {
    "environment": "staging",
    "tool": "kubernetes.patch_deployment",
    "risk_level": "R1",
    "has_rollback": true,
    "rollback": {"memory_limit": "256Mi"}
  }
  result.decision == "allow"
}

test_write_without_rollback_requires_human if {
  result := tool_intent.decision with input as {
    "environment": "staging",
    "tool": "kubernetes.patch_deployment",
    "risk_level": "R1",
    "has_rollback": false,
    "rollback": {}
  }
  result.decision == "require_approval"
}

test_edited_staging_write_requires_fresh_human if {
  result := tool_intent.decision with input as {
    "environment": "staging",
    "tool": "kubernetes.patch_deployment",
    "risk_level": "R1",
    "has_rollback": true,
    "rollback": {"memory_limit": "256Mi"},
    "edited": true
  }
  result.decision == "require_approval"
  result.matched_policy == "edited-intent-requires-fresh-approval"
}

test_prefix_matched_unknown_tool_is_denied if {
  result := tool_intent.decision with input as {
    "environment": "production",
    "tool": "prometheus.delete_series",
    "risk_level": "R0",
    "has_rollback": true
  }
  result.decision == "deny"
  result.matched_policy == "unknown-tool-denied"
}

test_caller_cannot_downgrade_production_write_risk if {
  result := data.runguard.tool_intent.decision with input as {
    "environment": "production",
    "tool": "kubernetes.patch_deployment",
    "arguments": {},
    "risk_level": "R0",
    "has_rollback": true,
    "rollback": {"memory_limit": "256Mi"},
    "edited": false
  }
  result.decision == "require_approval"
  result.matched_policy == "prod-write-requires-human"
}
