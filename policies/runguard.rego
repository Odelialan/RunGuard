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

allowed_tools := {
  "prometheus.query",
  "loki.query",
  "kubernetes.get_pods",
  "kubernetes.get_events",
  "kubernetes.get_deployment",
  "github.get_deployments",
  "kubernetes.patch_deployment",
  "kubernetes.scale_deployment",
  "kubernetes.rollout_restart"
}

read_tools := {
  "prometheus.query",
  "loki.query",
  "kubernetes.get_pods",
  "kubernetes.get_events",
  "kubernetes.get_deployment",
  "github.get_deployments"
}

write_tools := {
  "kubernetes.patch_deployment",
  "kubernetes.scale_deployment",
  "kubernetes.rollout_restart"
}

effective_rollback if {
  input.tool == "kubernetes.patch_deployment"
  some key, _ in input.rollback
  key in {"memory_limit", "cpu_limit"}
}

effective_rollback if {
  input.tool == "kubernetes.scale_deployment"
  some key, _ in input.rollback
  key == "replicas"
}

decision := {
  "decision": "deny",
  "matched_policy": "destructive-operations-denied",
  "reason": "Destructive or arbitrary execution is outside the Agent permission boundary."
} if {
  input.tool in r3_tools
} else := {
  "decision": "deny",
  "matched_policy": "destructive-operations-denied",
  "reason": "Privileged execution is outside the Agent permission boundary."
} if {
  input.arguments.privileged == true
} else := {
  "decision": "deny",
  "matched_policy": "unknown-tool-denied",
  "reason": "Only exact allow-listed tools may cross the policy boundary."
} if {
  not input.tool in allowed_tools
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
  input.tool in write_tools
} else := {
  "decision": "require_approval",
  "matched_policy": "write-without-rollback-requires-human",
  "reason": "Write operation has no verified rollback action."
} if {
  input.tool in write_tools
  not effective_rollback
} else := {
  "decision": "allow",
  "matched_policy": "readonly-or-reversible-staging",
  "reason": "Operation is read-only or reversible within an isolated environment."
} if {
  input.tool in read_tools
} else := {
  "decision": "allow",
  "matched_policy": "readonly-or-reversible-staging",
  "reason": "Operation is read-only or reversible within an isolated environment."
} if {
  input.tool in write_tools
  input.environment in {"staging", "development", "test", "kind", "runguard-system"}
  effective_rollback
}
