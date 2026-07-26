# RunGuard

> Agentic SRE 事故响应与可信执行平台

[![Version](https://img.shields.io/badge/version-1.2.1-6ef0b5)](./VERSION)
[![Python](https://img.shields.io/badge/Python-3.11%2B-78aef7)](./pyproject.toml)
[![React](https://img.shields.io/badge/React-TypeScript-78aef7)](./apps/web/package.json)
[![License](https://img.shields.io/badge/license-Apache--2.0-ac92ff)](./LICENSE)

RunGuard 将告警、Agent 调查、风险策略、人工审批、受控执行、效果验证和复盘记录连接为一条可审计的事故响应链路。LLM 只生成结构化 Tool Intent，无法直接获得基础设施凭据；所有写操作必须经过参数校验、风险分级、策略判断、幂等控制与回滚检查。

当前版本：**1.2.1** · 发布日期：**2026-07-26**
作者：**OdeliaLan**

## 1.2 能力

- Prometheus Webhook 与人工 Incident 接入
- Commander、Investigator、Remediation、Reviewer、Reporter 五类 Agent 工作流
- LangGraph 状态图与真实结构化 LLM 输出；本地可切换确定性后端
- Prometheus、Loki、Kubernetes、GitHub 真实连接器与 Mock/Hybrid/Recorded 模式
- 官方 MCP Streamable HTTP 客户端，可连接四类独立远程 MCP Server
- 每条根因假设关联来源 URI 与 Evidence ID
- 独立 OPA 服务执行 Rego 策略，OPA 异常时 fail-closed
- 生产写操作人工审批，R3 操作默认拒绝
- PostgreSQL + pgvector 证据存储、语义检索与 Redis Streams 事件总线
- PostgreSQL LangGraph Checkpointer、外层工作流检查点与重启自动恢复
- 带租约续期的 Redis 分布式执行锁，防止多副本重复启动、执行或补偿
- PostgreSQL 事务型 Outbox 向 Redis Streams 提供至少一次事件投递
- 异步请求路径将同步 Store I/O 隔离到线程池，避免阻塞 Agent 与锁续租事件循环
- Kubernetes Job 沙箱、独立 ServiceAccount、最小 RBAC、RuntimeDefault seccomp
- Tool Intent 幂等键、持久化 before/after snapshot、失败验证与真实补偿回滚
- 生产验证同时检查 P95、错误率与 Deployment 就绪副本
- OpenTelemetry OTLP Trace，可接 Tempo/Jaeger 与 Grafana
- append-only Incident Event 事件记录
- Recorded MCP Transport 抽象；当前 Replay API 仅生成无副作用的审计摘要
- A2A 1.0 Reviewer Agent Card、JSON-RPC 审查服务与远程 Reviewer Client
- 结构化 Postmortem 页面、JSON/Markdown 导出与行动项
- 12 个固定故障案例及可复现评测报告
- Helm Chart、kind 三节点演示集群与可控故障注入/补偿验证脚本
- 中英文切换、提升字号后的响应式运维工作台
- API Key/OIDC 身份认证、viewer/operator/approver/service/admin RBAC
- Redis 多副本限流、Prometheus Webhook HMAC 验签与安全响应头
- 启动时生产配置 fail-fast，拒绝无鉴权、无 OPA、无沙箱或无持久化的伪生产配置

> 默认仍运行在 `simulation + mock + deterministic` 安全模式。只有显式配置生产连接器、
> OPA、数据库、模型和 `kubernetes_job` 执行模式后，系统才会访问真实基础设施。

## 快速开始

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/) 和 Node.js 20+。

```bash
git clone https://github.com/Odelialan/RunGuard.git
cd RunGuard
./scripts/dev.sh
```

启动后访问：

- Web：<http://127.0.0.1:5173>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

也可以分开启动：

```bash
uv sync --extra dev
uv run uvicorn runguard_api.main:app --reload --port 8000

npm --prefix apps/web install
npm --prefix apps/web run dev
```

完整 Docker Compose 栈（PostgreSQL/pgvector、Redis、OPA、Prometheus、Loki、
OpenTelemetry Collector、Tempo、Grafana、RunGuard 与故障注入器）：

```bash
docker compose up --build
```

启动后可访问 RunGuard `:5173`、Grafana `:3000`、Prometheus `:9090` 和 Fault
Injector `:8090`。

### kind 真实沙箱与失败补偿

需要 Docker、kind、kubectl 和 Helm：

```bash
./scripts/kind-up.sh
./scripts/kind-failure-demo.sh
```

失败演示会在集群内注入健康检查故障，通过受限 Kubernetes Job 修改 Deployment，在验证
失败后执行补偿 Job，并断言 Incident 进入 `ROLLED_BACK`。完成后运行：

```bash
./scripts/kind-down.sh
```

### 局域网访问

开发模式会同时监听所有网卡。启动后，使用启动日志显示的局域网地址访问：

```bash
./scripts/dev.sh
# Web: http://<本机局域网IP>:5173
# API: http://<本机局域网IP>:8000/docs
```

需要单端口运行时：

```bash
RUNGUARD_PORT=8000 ./scripts/serve.sh
# 前端与 API 共用 http://<本机局域网IP>:8000
```

查看本机局域网 IP：

```bash
hostname -I
```

访问设备需要与运行 RunGuard 的电脑处于同一局域网。若系统启用了防火墙，需要允许 TCP `5173` 和 `8000`；单端口模式只需允许所选端口。不要把模拟模式以外的执行服务直接暴露到不受信任网络。

## 演示流程

1. 打开 Incidents，进入一个新 Incident。
2. 点击 **Run response**，系统依次完成分级、证据收集、根因推断与修复计划。
3. staging 的可逆 R1 操作自动执行；production 的 R2 操作暂停在 Approval Center。
4. 审核证据、变更差异、策略原因、回滚参数和幂等键后批准或拒绝。
5. 批准后进入模拟沙箱执行并自动验证 P95、错误率和工作负载稳定性。
6. 在 Trace Explorer 查看 Agent、检索、工具、策略、审批、执行和验证 Span。
7. 在 Evaluations 运行 `baseline-12`，生成带范围声明的静态参考报告。

## 系统架构

```mermaid
flowchart LR
    A["Prometheus / Manual Incident"] --> B["FastAPI Event Gateway"]
    B --> C["Incident Orchestrator"]
    C --> D["Commander"]
    C --> E["Investigator"]
    C --> F["Remediation"]
    C --> G["Reviewer"]
    C --> V["Reporter"]
    E --> H["MCP Tool Gateway"]
    F --> I["Normalized Tool Intent"]
    I --> J["Risk Classifier"]
    J --> K["Policy Gateway"]
    K -->|"allow"| L["Idempotent Executor"]
    K -->|"approval"| M["Approval Center"]
    K -->|"deny"| N["Stop + Audit"]
    M --> L
    L --> O["Verification"]
    O -->|"pass"| P["Resolved"]
    O -->|"fail"| Q["Compensation / Rollback"]
    C -.-> R[("PostgreSQL + pgvector")]
    H -.-> S["Prometheus / Loki / K8s / GitHub"]
    C -.-> T["Redis Streams + Replay + Eval"]
    C -.-> U["OTLP → Tempo / Jaeger / Grafana"]
```

本地安全模式继续支持 SQLite 零依赖演示；生产模式使用 Psycopg 3 连接 PostgreSQL，
在 Evidence 表启用 pgvector，并通过事务型 Outbox 向 Redis Streams 输出可消费、
可重放的事故事件。每条 Stream 消息包含稳定 `event_id`，消费者应使用它去重。
连接器采用统一 Transport 接口隔离 Production、Hybrid、Mock 与 Recorded 实现。

## 可信执行路径

```text
Agent plan
  → normalized Tool Intent
  → schema validation
  → role boundary
  → R0–R3 classification
  → policy decision
  → human approval when required
  → idempotency check
  → restricted Kubernetes Job execution
  → SLO verification
  → resolve or compensate
```

Tool Intent 示例：

```json
{
  "tool": "kubernetes.patch_deployment",
  "actor": "remediation-agent",
  "incident_id": "INC-2026-00017",
  "environment": "production",
  "resource": {
    "namespace": "production",
    "kind": "Deployment",
    "name": "order-api"
  },
  "arguments": {
    "memory_limit": "1Gi"
  },
  "rollback": {
    "memory_limit": "256Mi"
  },
  "idempotency_key": "inc-2026-00017-action-01"
}
```

## Incident 状态机

```mermaid
stateDiagram-v2
    [*] --> NEW
    NEW --> TRIAGING
    TRIAGING --> INVESTIGATING
    INVESTIGATING --> PLAN_READY
    PLAN_READY --> POLICY_CHECKING
    POLICY_CHECKING --> DENIED
    POLICY_CHECKING --> WAITING_APPROVAL
    POLICY_CHECKING --> EXECUTING
    WAITING_APPROVAL --> EXECUTING: approved
    WAITING_APPROVAL --> HUMAN_HANDOFF: rejected
    EXECUTING --> VERIFYING
    VERIFYING --> RESOLVED
    VERIFYING --> ROLLING_BACK
    ROLLING_BACK --> ROLLED_BACK
    VERIFYING --> HUMAN_HANDOFF
```

状态变化写入 `incident_events`，已有事件不会被覆盖。当前状态是事件流的查询投影。

## 策略决策

| 风险 | 示例 | 1.1 默认策略 |
| --- | --- | --- |
| R0 | 查询指标、日志、Pod 状态 | 自动允许 |
| R1 | staging Deployment 可逆修改 | 自动允许 |
| R2 | production 写操作、无回滚写操作 | 强制审批 |
| R3 | 删除 Namespace、数据库删除、任意 Shell | 拒绝 |

策略源码位于 [`policies/runguard.rego`](./policies/runguard.rego)。本地安全模式可使用等价
Python 求值器；生产设置 `RUNGUARD_POLICY_BACKEND=opa` 后，独立 OPA Data API 是最终决策点，
不可用或返回未定义结果时默认拒绝执行。

## 生产配置

| 配置 | 生产值 | 作用 |
| --- | --- | --- |
| `RUNGUARD_DATABASE_URL` | PostgreSQL DSN | 启用 PostgreSQL/pgvector |
| `RUNGUARD_REDIS_URL` | Redis DSN | 启用 Streams 事件发布 |
| `RUNGUARD_CONNECTOR_MODE` | `production` | 使用真实四类连接器 |
| `RUNGUARD_CONNECTOR_MODE` | `mcp` | 使用远程 Streamable HTTP MCP Server |
| `RUNGUARD_AGENT_BACKEND` | `langgraph` | 使用 LangGraph 与结构化 LLM |
| `RUNGUARD_LANGGRAPH_CHECKPOINT_BACKEND` | `postgres` | 持久化 Graph 节点状态 |
| `RUNGUARD_POLICY_BACKEND` | `opa` | 使用独立 OPA |
| `RUNGUARD_EXECUTION_MODE` | `kubernetes_job` | 使用受限 Job 执行 |
| `RUNGUARD_TARGET_INVENTORY_JSON` | 服务到环境/Namespace/资源名的映射 | 服务端绑定真实执行目标，拒绝客户端或 Agent 改写环境 |
| `RUNGUARD_OTEL_EXPORTER_OTLP_ENDPOINT` | Collector HTTP 端点 | 输出 OTLP Trace |
| `RUNGUARD_A2A_REVIEWER_URL` | A2A JSON-RPC URL | 委托独立 Reviewer |
| `RUNGUARD_AUTH_MODE` | `oidc` 或 `api_key` | 启用身份认证与 RBAC |
| `RUNGUARD_PUBLIC_BASE_URL` | `https://runguard.example.com` | 固定 Agent Card 公网地址，避免信任请求 Host |
| `RUNGUARD_MAX_REQUEST_BODY_BYTES` | `1048576` | 限制写请求体，包含无 Content-Length 的流式请求 |
| `RUNGUARD_ENFORCE_PRODUCTION_GUARDS` | `true` | 启动时强制校验生产安全基线 |
| `RUNGUARD_AUTO_RECOVER` | `true` | 重启后恢复未完成工作流 |

生产目标清单示例：

```json
{
  "order-api": {
    "environment": "production",
    "namespace": "runguard-system",
    "name": "order-api"
  }
}
```

Helm 生产安装还要求 `image.digest` 与 `runnerImage.digest`，执行控制面和 Runner
均以不可变镜像摘要部署。事件中提交的 `environment` 必须与目标清单一致。
手工部署时，`RUNGUARD_KUBERNETES_RUNNER_IMAGE` 同样必须使用
`repository@sha256:<64位摘要>` 格式。

所有密钥仅通过 Secret/环境变量注入。Helm Chart 不包含真实密钥，安装时必须提供。
生产集群建议预先创建 Secret，并通过 `secrets.existingSecret` 引用，避免把密钥写进
Helm values 或发布记录。

Prometheus Webhook 请求必须携带 Unix 秒时间戳 `X-RunGuard-Timestamp` 与
`X-RunGuard-Signature`。签名内容为
`HMAC-SHA256(secret, "<timestamp>.<raw-body>")`，请求头格式为
`sha256=<hex-digest>`；超过五分钟的请求会被拒绝，以降低重放风险。
相同标签与 `startsAt` 的重复通知只创建一个 Incident，非 `firing` 通知不会创建 Incident。

## 评测集

`baseline-12` 包含：

1. Pod OOMKilled
2. CrashLoopBackOff
3. 镜像拉取失败
4. 副本数不足
5. CPU Limit 过低
6. 数据库连接池耗尽
7. Redis 延迟
8. 错误环境变量
9. Service Selector 错误
10. 最近发布导致异常
11. 日志平台不可用
12. 多个候选根因并存

Dashboard 展示 Top-1/Top-3 根因期望、策略期望、危险操作拦截、Trace 覆盖、重复副作用、工具调用次数与处理耗时。当前 `baseline-12` 是**静态确定性参考夹具**，不执行真实模型、故障注入、Worker 中断或 Kubernetes 变更，不能作为实测成果。

## 仓库结构

```text
RunGuard/
├── agents/prompts/          # 运行所需、版本化的 Agent Prompt
├── apps/api/runguard_api/   # FastAPI、状态机、策略、事件存储、执行与评测
├── apps/web/                # React + TypeScript 操作台
├── deploy/docker/           # API 与 Web 容器配置
├── deploy/helm/runguard/    # 生产 Helm Chart、RBAC 与安全上下文
├── deploy/kind/             # 本地三节点集群与演示工作负载
├── deploy/observability/    # Prometheus/Loki/Tempo/OTel/Grafana
├── policies/                # OPA Rego 策略
├── services/fault-injector/ # 有鉴权的延迟/错误/健康故障注入器
├── scripts/                 # 本地启动入口
├── .github/workflows/       # CI 类型检查、构建与 API smoke test
├── VERSION
└── README.md
```

## 配置与数据安全

复制 `.env.example` 后按需配置。真实 API Key、Token、kubeconfig、数据库文件、日志、虚拟环境、`node_modules`、构建产物、评测报告和大体积模型/媒体文件均已被 `.gitignore` 排除。

提交前可运行：

```bash
./scripts/check.sh
```

该脚本会检查 Python 静态规则、前端类型与生产构建、API smoke test、Git 追踪文件大小和常见凭据模式。

## 1.2.1 发布记录

**2026-07-26 · Adversarial reliability review**

- 将 Webhook HMAC 验签前置到限流之前，避免无效请求抢占合法告警预算。
- 增加事务型 Outbox、稳定 Event ID、执行锁租约续期和同步 Store I/O 线程隔离。
- 增加生产目标清单、不可变 Runner 镜像摘要、请求体限制与告警入口去重。
- Kubernetes Runner 将真实 before snapshot 与幂等标记一同持久化；Scale 变更改为单次原子 Deployment patch。
- 幂等重放恢复原始 before snapshot，避免进程崩溃后生成错误补偿参数。
- 生产验证由单一延迟信号扩展为 P95、错误率和 Deployment 就绪副本三方验证。
- 调查证据新增 Deployment 状态，补齐工作负载证据。
- 对齐 MCP 工具发现结果并删除执行器不再使用的 Kubernetes RBAC 权限。
- 明确 `baseline-12` 为静态参考夹具，移除“已实测”表述。
- 扩充本地缓存、覆盖率报告、私有草稿和大文件忽略规则；运行所需 Prompt 与自动化测试继续版本化。

## 1.2.0 发布记录

**2026-07-26 · Production hardening**

- 增加真实 MCP Streamable HTTP Client；远程 MCP 写操作仍强制经过本地受限 Job。
- 增加 API Key 与 OIDC JWT 鉴权、五级 RBAC 和可信审批人身份绑定。
- 增加 Redis 分布式执行锁与多副本固定窗口限流。
- 增加 Prometheus Webhook HMAC-SHA256 验签、安全响应头和请求追踪 ID。
- 增加 PostgreSQL LangGraph Checkpointer、加密状态和外层工作流检查点。
- 增加安全恢复端点、启动自动恢复、验证续跑和幂等补偿恢复。
- 增加带 advisory lock、checksum 防篡改的顺序数据库迁移。
- Helm 增加生产配置 fail-fast、滚动更新、拓扑分散、Startup Probe 与 ServiceMonitor。
- CI 在强制生产安全配置下验证 OIDC/API Key 边界、迁移、PostgreSQL、Redis 与 OPA。

## 1.1.0 发布记录

**2026-07-24 · Production foundations**

- 接入真实 Prometheus、Loki、Kubernetes、GitHub 数据源与 Hybrid 模式。
- 接入 LangGraph、结构化 LLM 和 A2A Reviewer。
- 启用 PostgreSQL/pgvector、Redis Streams、独立 OPA 与 OTLP。
- 实现受限 Kubernetes Job、ServiceAccount、RBAC、seccomp 和补偿回滚。
- 增加结构化 Postmortem 页面与 Markdown/JSON 导出。
- 增加 Compose、Helm、kind、故障注入服务和真实失败补偿脚本。
- 前端整体字号提升，并支持右上角中英文切换与偏好记忆。

## 1.0.0 发布记录

**2026-07-24 · Initial release**

- 建立从 Incident 接入到验证结案的完整可信执行闭环。
- 实现四 Agent 编排、MCP Transport 抽象、证据链和结构化根因。
- 实现 R0–R3 风险模型、Rego 策略、人工审批及拒绝后接管。
- 实现幂等执行、执行快照、补偿参数、Trace 与无副作用重放。
- 建立 12 场景确定性评测套件和可交互 Dashboard。
- 完成响应式 Web 工作台、容器化配置、CI 与安全提交检查。

## 运行边界

- 任意 Shell、Namespace 删除、数据库删除等 R3 能力不实现，也不会通过策略。
- 生产连接器需要提供对应端点、Token、kubeconfig/集群身份和网络连通性。
- 生产模式必须配置 OIDC 或 API Key；身份提供方中的角色需映射为
  `viewer`、`operator`、`approver`、`service` 或 `admin`。
- 远程 MCP 模式要求四类 MCP Server 使用 Streamable HTTP，并由网络策略或 OAuth
  保护；只有精确只读白名单会发送到远程 Server，基础设施写入始终留在本地受限 Job。
- Helm 默认采用单集群、Namespace 级最小权限；企业 SSO、多租户和多集群调度需要按组织
  IAM 与网络边界单独集成。
- 未配置生产依赖时，系统明确显示 Mock/Simulation，不会把模拟数据标记成生产结果。
- `baseline-12` 尚未驱动真实故障注入或模型推理；当前分数仅是参考夹具，不是实验数据。
- Replay API 尚未重新执行 Recorded 模型/工具轨迹，只返回已有 Run 的无副作用审计摘要。
- A2A Reviewer 可调用外部服务，但仓库未提供独立 Reviewer 部署单元。
- Shadow Mode、Canary Execution、每次 Incident 的 Token/工具/时间预算尚未实现。
- 语义 Evidence Search 已提供 API，但历史 Incident Memory 尚未自动进入调查决策。

## License

Copyright 2026 OdeliaLan.

Licensed under the [Apache License 2.0](./LICENSE).
