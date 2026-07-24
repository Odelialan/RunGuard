import { useEffect } from "react";

export type Language = "en" | "zh";

const ZH: Record<string, string> = {
  "TRUSTED OPS · V1.1": "可信运维 · V1.1",
  "Production workspace": "生产工作区",
  COMMAND: "指挥中心",
  Overview: "总览",
  Incidents: "事故",
  Incident: "事故",
  "Approval center": "审批中心",
  "Trace explorer": "链路追踪",
  Postmortems: "事故复盘",
  Evaluations: "评测",
  "Policy simulator": "策略模拟器",
  "System operational": "系统运行正常",
  "Agent runtime": "Agent 运行时",
  Healthy: "健康",
  "Policy gateway": "策略网关",
  Enforced: "已强制执行",
  Execution: "执行",
  Simulation: "模拟模式",
  "Platform owner": "平台所有者",
  "Search incidents": "搜索事故",
  "New incident": "新建事故",
  Refresh: "刷新",
  Notifications: "通知",
  "Close menu": "关闭菜单",
  "Incident command, with guardrails.": "带安全护栏的事故指挥中心。",
  "Investigate faster, control every side effect, and keep a complete trail from alert to recovery.":
    "加速调查、控制每个副作用，并保留从告警到恢复的完整审计链路。",
  "LIVE OPERATIONS": "实时运维",
  "Active incidents": "活跃事故",
  "Automation rate": "自动化率",
  "Mean time to resolve": "平均恢复时间",
  "Risk controls": "风险控制",
  "Across closed incidents": "已关闭事故统计",
  "Resolved without manual execution": "无需人工执行即完成恢复",
  "Dangerous operations intercepted": "危险操作已拦截",
  "No bypasses": "无绕过",
  "24 hours": "24 小时",
  "7 days": "7 天",
  "30 days": "30 天",
  "4 total in workspace": "工作区共 4 起",
  "+1 today": "今日 +1",
  New: "新建",
  Investigating: "调查中",
  "Waiting Approval": "等待审批",
  Resolved: "已解决",
  "0 tool calls": "0 次工具调用",
  "6 tool calls": "6 次工具调用",
  "7 tool calls": "7 次工具调用",
  "Live incident queue": "实时事故队列",
  "Prioritized by severity and policy state": "按严重等级与策略状态排序",
  "View all": "查看全部",
  "Trusted execution flow": "可信执行流程",
  "Current platform control path": "当前平台控制路径",
  Signal: "信号",
  Validated: "已验证",
  Investigate: "调查",
  "4 agents": "4 个 Agent",
  "5 agents": "5 个 Agent",
  Deterministic: "确定性引擎",
  "Python demo": "Python 演示",
  "OPA enforced": "OPA 强制执行",
  "Kubernetes Job": "Kubernetes Job",
  Policy: "策略",
  Active: "生效",
  Execute: "执行",
  Sandboxed: "沙箱化",
  Verify: "验证",
  "SLO gates": "SLO 门禁",
  "LLMs never receive direct infrastructure credentials.": "LLM 永远不会直接获得基础设施凭据。",
  "Every intent passes schema, policy, approval and idempotency checks.":
    "每个执行意图都必须通过结构、策略、审批与幂等检查。",
  "Approval inbox": "审批收件箱",
  "Production changes waiting for a human": "等待人工审核的生产变更",
  "Open center": "打开审批中心",
  "rollback ready": "可回滚",
  "Trace pulse": "Trace 动态",
  "Latest recorded execution span": "最新执行 Span",
  "Explore traces": "查看链路",
  "Incident queue": "事故队列",
  "INCIDENT OPERATIONS": "事故运维",
  "One immutable operating record for every alert, decision, and recovery action.":
    "为每条告警、决策和恢复操作保留不可变的运行记录。",
  "Search by ID, service or title": "按 ID、服务或标题搜索",
  All: "全部",
  "Service / environment": "服务 / 环境",
  Status: "状态",
  Created: "创建时间",
  Evidence: "证据",
  "No incidents match this view": "没有符合当前条件的事故",
  "Event stream is append-only": "事件流仅追加、不可覆盖",
  "Run response": "运行响应流程",
  "Stop workflow": "停止工作流",
  "Incident workflow completed": "事故流程已完成",
  "Investigation complete — approval required": "调查完成——需要人工审批",
  "Intent rejected and handed to a human": "执行意图已拒绝并移交人工",
  "Approved, executed, and verified successfully": "已批准、执行并验证成功",
  "Planning & control": "规划与控制",
  "Run telemetry": "运行遥测",
  "No run started": "尚未开始运行",
  "Agent roster": "Agent 阵容",
  "Version-pinned for this run": "本次运行版本已锁定",
  Commander: "指挥 Agent",
  Investigator: "调查 Agent",
  Remediation: "修复 Agent",
  Reporter: "报告 Agent",
  "Execution boundary active": "执行边界已启用",
  "Direct cluster access is disabled. All writes require normalized intent.":
    "已禁用直接集群访问；所有写操作必须使用标准化执行意图。",
  "Evidence & inference": "证据与推断",
  "Ready to investigate": "可以开始调查",
  "Start the response workflow to collect evidence from metrics, logs, Kubernetes, and deployments.":
    "启动响应流程，从指标、日志、Kubernetes 与发布记录中收集证据。",
  "Evidence chain": "证据链",
  "Investigation": "调查过程",
  "Remediation plan": "修复计划",
  "No remediation plan yet": "尚无修复计划",
  "A structured, reversible Tool Intent appears here after evidence review.":
    "证据审核完成后，这里会显示结构化且可回滚的 Tool Intent。",
  Target: "目标",
  "Proposed change": "拟议变更",
  Rollback: "回滚",
  "Schema valid": "结构合法",
  "Role permitted": "角色允许",
  "Idempotent execution": "幂等执行",
  "Review exact changes, policy rationale, and rollback before any side effect.":
    "在产生任何副作用前，审核准确变更、策略原因和回滚方案。",
  "awaiting review": "等待审核",
  "Approval inbox is clear": "审批收件箱为空",
  "No Agent is waiting to change protected infrastructure.":
    "当前没有 Agent 等待修改受保护的基础设施。",
  "Pending intents": "待审意图",
  "Oldest first": "最早优先",
  "POLICY DECISION": "策略决策",
  "Human approval required": "需要人工审批",
  "Proposed infrastructure change": "拟议基础设施变更",
  "Compensating action ready": "补偿操作已就绪",
  "Duplicate side effects blocked": "重复副作用已阻止",
  "Record the reason for your decision…": "记录审批理由……",
  Approve: "批准",
  Reject: "拒绝",
  "Follow Agent reasoning, retrieval, policy, execution, and verification end to end.":
    "端到端查看 Agent 推理、检索、策略、执行与验证过程。",
  "Collector connected": "采集器已连接",
  "Search spans, agents or incident IDs": "搜索 Span、Agent 或事故 ID",
  "No trace spans recorded": "尚未记录 Trace Span",
  "No traces match these filters": "没有符合筛选条件的 Trace",
  "Evaluation dashboard": "评测仪表盘",
  "Measured results from fixed, reproducible fault scenarios — never self-reported claims.":
    "基于固定且可复现故障场景的实测结果，不使用自我声明数据。",
  "Establish the v1.0 baseline": "建立 v1.0 基线",
  "Run 12 deterministic scenarios to measure diagnosis, policy, and recovery behavior.":
    "运行 12 个确定性场景，测量诊断、策略和恢复行为。",
  "Run baseline": "运行基线评测",
  "BASELINE SCORE": "基线评分",
  "Trusted response quality": "可信响应质量",
  "Top-1 RCA": "Top-1 根因",
  "Scenario matrix": "场景矩阵",
  Scenario: "场景",
  "Root cause": "根因",
  Calls: "调用数",
  Result: "结果",
  "Policy accuracy": "策略准确率",
  "Trace coverage": "Trace 覆盖率",
  Efficiency: "效率",
  "Per incident average": "每起事故平均值",
  "P50 duration": "P50 耗时",
  "P95 duration": "P95 耗时",
  "Tool calls": "工具调用",
  Tokens: "Token 数",
  "Measured, scoped, reproducible": "可测量、有边界、可复现",
  "Preview the exact decision before an Agent intent enters the execution path.":
    "在 Agent 意图进入执行路径前预览准确决策。",
  "Tool intent input": "工具执行意图输入",
  "Normalized policy evaluation payload": "标准化策略求值载荷",
  Environment: "环境",
  Tool: "工具",
  Resource: "资源",
  "Verified rollback available": "已有验证过的回滚方案",
  "Intent includes a compensating action and before snapshot.":
    "执行意图包含补偿操作与变更前快照。",
  "Evaluate policy": "执行策略求值",
  Decision: "决策",
  "Deterministic policy output": "确定性策略输出",
  "Matched rule": "命中规则",
  "Classified risk": "风险等级",
  "Next gate": "下一道门禁",
  "Evaluation path": "求值路径",
  "MANUAL INTAKE": "人工接入",
  "Create incident": "创建事故",
  Title: "标题",
  Severity: "严重等级",
  Service: "服务",
  Description: "描述",
  Cancel: "取消",
  Create: "创建",
  "e.g. order-api latency elevated": "例如：order-api 延迟升高",
  "Describe the signal, duration and known impact…": "描述信号、持续时间和已知影响……",
  "Synchronizing incident state": "正在同步事故状态",
  "Loading events, policy decisions and trace spans…": "正在加载事件、策略决策与 Trace Span……",
  "LEARNING SYSTEM": "持续学习系统",
  "Turn evidence, decisions, execution, and verification into a structured record.":
    "将证据、决策、执行和验证整理为结构化事故记录。",
  "Regenerate": "重新生成",
  "Generate report": "生成报告",
  "Export Markdown": "导出 Markdown",
  "No closed incidents yet": "尚无已关闭事故",
  "Resolve or roll back an incident before generating a postmortem.":
    "请先完成事故恢复或回滚，再生成复盘报告。",
  "No report generated": "尚未生成报告",
  "Create a structured postmortem from the immutable incident record.":
    "根据不可变事故记录创建结构化复盘报告。",
  "Structured postmortem generated": "结构化事故复盘已生成",
  "Impact": "影响",
  "Contributing factors": "促成因素",
  "Incident timeline": "事故时间线",
  "Lessons learned": "经验总结",
  "Action items": "行动项",
  "Run": "运行",
  "Recovery actions": "恢复措施",
  "FINAL": "最终版",
  "Redis command duration above baseline.": "Redis 命令执行时间超过基线。",
  "Root cause remains unconfirmed; further human investigation is required.":
    "根因尚未确认，需要继续人工调查。",
  "No infrastructure mutation was executed.": "本次事故没有执行基础设施变更。",
  "Evidence-linked changes reduce time spent validating competing hypotheses.":
    "证据关联的变更可以减少验证竞争性假设所需的时间。",
  "Reversible, bounded remediation should be preferred over broad restarts.":
    "应优先采用可回滚、有边界的修复，而不是大范围重启。",
  "Add a regression alert for the verified failure signal": "为已验证的故障信号添加回归告警",
  "Add the remediation and rollback path to the service runbook":
    "将修复与回滚路径补充到服务 Runbook",
  "OPEN": "待处理",
  "incident.created": "事故已创建",
  "incident.status_changed": "事故状态已变更",
  "event-gateway": "事件网关",
  "commander-agent": "指挥 Agent",
  "source=seed": "来源=初始化数据",
  NEW: "新建",
  TRIAGING: "分诊中",
  INVESTIGATING: "调查中",
  PLAN_READY: "方案就绪",
  POLICY_CHECKING: "策略检查中",
  WAITING_APPROVAL: "等待审批",
  EXECUTING: "执行中",
  VERIFYING: "验证中",
  RESOLVED: "已解决",
  DENIED: "已拒绝",
  ROLLING_BACK: "正在回滚",
  ROLLED_BACK: "已回滚",
  HUMAN_HANDOFF: "人工接管",
  CANCELLED: "已取消",
  Pending: "待处理",
  Complete: "完成",
  Current: "当前",
  Queued: "排队中",
  "Safe repair plan": "安全修复方案",
  "Human approval": "人工审批",
  "Postmortem": "事故复盘",
  "Prompt version": "Prompt 版本",
  "just now": "刚刚",
  "order-api P95 latency exceeds 2s": "order-api P95 延迟超过 2 秒",
  "payment-api pods restarting": "payment-api Pod 持续重启",
  "Redis command latency elevated": "Redis 命令延迟升高",
  "frontend error budget burn": "前端错误预算消耗过快",
};

const originalText = new WeakMap<Text, string>();
const originalAttributes = new WeakMap<Element, Map<string, string>>();
const ATTRIBUTES = ["aria-label", "placeholder", "title"] as const;

function translateCore(value: string): string {
  if (ZH[value]) return ZH[value];
  const incidentTitles = [
    "order-api P95 latency exceeds 2s",
    "payment-api pods restarting",
    "Redis command latency elevated",
    "frontend error budget burn",
  ];
  for (const title of incidentTitles) {
    if (value.includes(title)) return value.replace(title, ZH[title]);
  }
  const resolvedSummary = value.match(
    /^(.+) entered RESOLVED after a (P[0-3]) incident in (.+)\.$/,
  );
  if (resolvedSummary) {
    return `${resolvedSummary[1]} 在 ${resolvedSummary[3]} 的 ${resolvedSummary[2]} 事故处理后进入已解决状态。`;
  }
  const month = value.match(/^Jul (.+)$/);
  if (month) return `7月 ${month[1]}`;
  const ago = value.match(/^(.+) ago$/);
  if (ago) return `${ago[1]}前`;
  const created = value.match(/^(.+) created$/);
  if (created) return `${created[1]} 已创建`;
  const cases = value.match(/^(.+) cases$/);
  if (cases) return `${cases[1]} 个案例`;
  const calls = value.match(/^(.+) tool calls$/);
  if (calls) return `${calls[1]} 次工具调用`;
  return value;
}

function translatedValue(value: string): string {
  const leading = value.match(/^\s*/)?.[0] ?? "";
  const trailing = value.match(/\s*$/)?.[0] ?? "";
  const core = value.trim();
  return core ? `${leading}${translateCore(core)}${trailing}` : value;
}

function processText(node: Text, language: Language) {
  const current = node.nodeValue ?? "";
  const saved = originalText.get(node);
  if (language === "en") {
    if (saved !== undefined && current !== saved) node.nodeValue = saved;
    return;
  }
  if (saved === undefined || (current !== saved && current !== translatedValue(saved))) {
    originalText.set(node, current);
  }
  const source = originalText.get(node) ?? current;
  const translated = translatedValue(source);
  if (translated !== current) node.nodeValue = translated;
}

function processElement(element: Element, language: Language) {
  if (element.hasAttribute("data-i18n-skip")) return;
  let saved = originalAttributes.get(element);
  if (!saved) {
    saved = new Map<string, string>();
    originalAttributes.set(element, saved);
  }
  for (const attribute of ATTRIBUTES) {
    const current = element.getAttribute(attribute);
    if (current === null) continue;
    if (!saved.has(attribute)) saved.set(attribute, current);
    const source = saved.get(attribute) ?? current;
    const next = language === "zh" ? translatedValue(source) : source;
    if (next !== current) element.setAttribute(attribute, next);
  }
}

function translateTree(root: Node, language: Language) {
  if (root instanceof Element && root.hasAttribute("data-i18n-skip")) return;
  if (root instanceof Text) {
    processText(root, language);
    return;
  }
  if (root instanceof Element) processElement(root, language);
  root.childNodes.forEach((child) => translateTree(child, language));
}

export function useDocumentLanguage(language: Language) {
  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
    localStorage.setItem("runguard-language", language);
    translateTree(document.body, language);
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === "characterData") {
          translateTree(mutation.target, language);
        } else {
          mutation.addedNodes.forEach((node) => translateTree(node, language));
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, [language]);
}

export function initialLanguage(): Language {
  const saved = localStorage.getItem("runguard-language");
  if (saved === "en" || saved === "zh") return saved;
  return navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
}
