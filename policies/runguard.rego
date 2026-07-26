package runguard.tool_intent

default decision := {
  "decision": "deny",
  "matched_policy": "default-deny",
  "reason": "No policy allows this tool intent."
}

r3_tools := {
  "kubernetes.delete_namespace",
  "database.drop",
  "shell.execute"
}

decision := {
  "decision": "deny",
  "matched_policy": "destructive-operations-denied",
  "reason": "Destructive or arbitrary execution is outside the Agent permission boundary."
} if {
  input.tool in r3_tools
} else := {
  "decision": "require_approval",
  "matched_policy": "edited-intent-requires-fresh-approval",
  "reason": "Every edited intent requires a fresh human approval."
} if {
  input.edited == true
} else := {
  "decision": "require_approval",
  "matched_policy": "prod-write-requires-human",
  "reason": "Production write operation requires SRE approval."
} if {
  input.environment in {"production", "prod"}
  input.risk_level in {"R1", "R2"}
} else := {
  "decision": "require_approval",
  "matched_policy": "write-without-rollback-requires-human",
  "reason": "Write operation has no verified rollback action."
} if {
  input.risk_level in {"R1", "R2"}
  not input.has_rollback
} else := {
  "decision": "allow",
  "matched_policy": "readonly-or-reversible-staging",
  "reason": "Operation is read-only or reversible within an isolated environment."
} if {
  input.risk_level == "R0"
} else := {
  "decision": "allow",
  "matched_policy": "readonly-or-reversible-staging",
  "reason": "Operation is read-only or reversible within an isolated environment."
} if {
  input.risk_level == "R1"
  input.environment in {"staging", "development", "test", "kind", "runguard-system"}
  input.has_rollback
}
