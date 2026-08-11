# JARVIS Engineering Reliability Rules

状态：**ACTIVE — PROJECT-WIDE**

本文档是 JARVIS 所有重要开发工作的长期工程约束，适用于 Python Core、React、
Tauri、WebSocket、LLM Provider、Memory、数据库、Agent Runtime、Tools、Codex、
Browser、Computer Use、Vision、Windows、文件系统、ROS2、外部程序和硬件边界。

文中的 **MUST / MUST NOT** 是硬性要求，**SHOULD** 只允许在有明确理由并记录取舍时
偏离。可靠性不要求大型框架；优先选择能守住不变量的最小、清晰实现。

## Hard Rules

### 1. 可验证事实不得猜测

- API endpoint、协议、model ID、认证格式、SDK/CLI 参数、库 API、操作系统行为、路径规则、
  Tauri/ROS2/浏览器接口和版本支持情况，必须先从当前 authoritative source 验证。
- 模型记忆、旧代码、博客、论坛和“通常如此”都不是外部事实的最终来源。
- 必须明确区分 **KNOWN FACT** 与 **ASSUMPTION**。无法验证时，必须标记：
  `UNVERIFIED ASSUMPTION` 或 `NEEDS VERIFICATION`；不得把它悄悄写成 production default。
- 真实 smoke test 用于验证已确定的契约，不用于试错猜参数。

### 2. Source of Truth 与状态 owner 必须唯一且明确

每项重要事实或状态必须有一个 authoritative owner：

| 事实或状态 | Source of Truth / Owner |
| --- | --- |
| 外部 API 契约 | 当前官方文档与实际协议响应 |
| Core 对话与任务状态 | Python Core / Agent Runtime |
| Persistent Memory | MemoryStore / Database |
| Session Context | 当前 Conversation 实例 |
| 文件内容与存在性 | Filesystem 的实际结果 |
| Windows 程序、窗口和进程 | OS / Executor 的实际观察 |
| Tool 是否执行成功 | 经过 Core 验证的 Tool Result |
| Native shell 与子进程生命周期 | Tauri |
| 展示状态 | React；不得反向成为 Core 的权威状态 |
| 权限 | Permission Manager（引入后） |

LLM 只能 propose、reason 或 request，不能成为现实状态的事实来源。两个模块不得同时
声称拥有同一状态；Session 不得冒充 Persistent Memory，UI 不得冒充 Executor。

### 3. Production default 必须真实可用，重要事实保持单一定义

- Production default 必须合理且经过验证；环境变量是 optional override，不是修复坏默认值的
  拐杖。
- 配置必须分别测试 **no override path** 与 **override path**。本机 `.env` 能运行，不证明默认值
  正确。
- 默认 URL、model、protocol version、timeout、schema version、状态名、端口规则和命令名应有
  一个 canonical definition。代码、示例、测试和文档应引用或验证它，而不是各自维护事实副本。
- 避免跨领域的 `BASE_URL`、`MODEL`、`CLIENT`、隐式 singleton 或共享 mutable global。
- 不为消除少量清晰重复而引入复杂依赖容器或无必要抽象。

### 4. 模块必须隔离，变化不得隐式跨域传播

- Provider、Profile、Router、Memory、Session、Tool 和 UI 的配置与状态必须按职责隔离。
- 一个 Chat Profile 的变化不得改变 Memory Router；一个 Provider 的配置不得污染另一个
  Provider；Tool A 失败不得破坏 Tool B；Session 不得自动持久化。
- 共享状态必须有明确 owner、生命周期和同步语义，并有关键 isolation regression tests。
- 隐式全局耦合如果无法说明必要性，必须移除。

### 5. 所有跨边界数据必须在边界处验证

以下输入一律视为不可信：LLM → Core、Frontend → Core、Core ↔ Tool、API → Client、
Environment → Config、Database → Domain Object、WebSocket → State Machine，以及 User →
Side Effect。

验证应按风险覆盖：schema、type、required fields、allowed values、nullability、range、ID 是否
真实存在、当前 state、permission，以及必要时拒绝 unexpected extra values。模型通常返回正确
JSON、API 通常有字段、路径通常存在，都不能作为安全假设。

### 6. 副作用必须 fail closed，成功声明只能来自真实 Executor

全局不变量：

```text
SIDE-EFFECT SUCCESS CLAIMS MUST ORIGINATE
FROM VERIFIED EXECUTOR RESULTS.
```

删除、覆盖、发送、执行、启动、关闭、移动、点击、键盘输入、文件/Git/系统修改、ROS2
actuator 和外部设备控制，只要目标、参数、权限、状态或结果不确定，就必须 **DO NOTHING**。

- 不得猜参数、虚构 ID、尝试相近操作、假设成功或 fallback 到 Chat 生成成功声明。
- 具有副作用的成功回复必须由 Core 验证真实 Executor/Store/Tool Result 后生成。
- 失败、取消、超时、部分执行或结果不确定时，不得声明成功。
- 读操作可以按产品定义 fail soft；写操作默认 fail closed。
- Fallback 必须 explicit、observable、tested。A 失败后不得静默执行 B 或伪装仍是 A。

### 7. 测试围绕不变量，并覆盖失败、取消、隔离和环境差异

- 非简单功能实现前必须列出 3～10 条关键 invariant；实现与测试围绕行为不变量，而非当前代码
  形状。
- 除 happy path 外，按边界风险覆盖 timeout、认证/权限失败、限流、5xx、malformed/empty
  response、partial stream、连接关闭、进程 crash、duplicate request、stale state、invalid ID、
  missing config/file、permission denied、executor failure 和 cancellation。
- 副作用失败测试必须断言：现实未改变、没有 success claim、没有语义变化的 silent fallback。
- Windows CMD、PowerShell、Python、Node、Rust、Tauri 和外部 CLI 的 cwd、绝对/相对路径、
  quoting、slash、UTF-8、PATH、shell expansion、环境继承和进程清理必须视为一级问题。
- 脚本应尽量 cwd-independent、explicit、repeatable；仅适用于某个 shell 时必须明确标注。

### 8. Mock PASS 不等于真实世界 PASS

自动测试证明代码符合我们写下的契约，不能证明外部系统仍符合契约。外部边界采用两层验证：

1. mock/unit/integration tests；
2. 基于已核实配置的 manual real smoke test。

新 Provider/API、认证、Windows/Browser/Computer Use、Codex、ROS2 bridge、硬件通信和 destructive
Tool 在封版前必须有真实 smoke test。纯内部逻辑可以不做外部 smoke。一次成功运行不证明
restart、no-config、failure、cancellation、concurrency、persistence 或 isolation 正确。

### 9. Runtime Reality 必须可诊断，但不得泄密

诊断应能安全回答实际使用的 request_id、profile、provider、model、tool、state、status、failure
phase 和 latency category。日志和 telemetry 不得记录 API Key、Authorization、完整 Prompt、
Pinned Memory、私人文件、用户敏感正文或原始敏感 Provider 响应。

观测必须 best-effort；telemetry 故障不得改变请求结果、状态机、Session、Memory 或副作用。

### 10. 修根因、控制范围、保持最小正确抽象

Bug 修复优先检查：incorrect fact → broken invariant → missing validation → wrong ownership → wrong
state transition → duplicated source of truth → inadequate test，最后才考虑局部条件补丁。

- 禁止为单个验收句或测试增加非领域规则的 special-case hack。
- 每个任务开始要明确 `IN SCOPE`、`OUT OF SCOPE` 和 `INVARIANTS THAT MUST NOT CHANGE`。
- 修 A 默认不顺手重构 B/C/D；选择 smallest correct abstraction、minimal diff 和 clear ownership。
- 完成前必须审阅 Git diff，确认没有范围外行为、秘密、私人数据或临时调试产物。

## 风险分级验证

从 v0.5D 之后的变更开始，验证与独立审查强度必须和风险相称：

| 等级 | 典型范围 | 默认验证基线 |
| --- | --- | --- |
| 低风险 | 文档、文案、局部确定性修改，且不改变安全、状态、协议或外部边界 | 聚焦测试（如适用）、lint/format、diff 与敏感信息检查；默认不做多轮独立审查 |
| 中风险 | 有限业务行为或模块集成，但不引入高风险现实副作用 | 定向 TDD、受影响回归、diff/安全检查和一次独立审查 |
| 高风险 | 认证/Secret、持久化/schema、副作用、权限、文件/系统执行、Provider/协议、并发/取消或关键跨边界数据流 | 完整相关测试矩阵、跨栈回归、专项安全/规格审查，以及必要的真实人工验收 |

测试或审查发现新的 trust boundary、失败模式或现实副作用时必须升级等级。显式用户计划、CI、项目硬规则和 sealed ADR 规定的检查优先于本表；风险分级不能用来跳过必做验证。反过来，也不得让低风险功能默认复制 v0.5D 的最高强度多轮对抗审查。

## 标准执行顺序

非简单功能按以下顺序推进：

1. 理解需求与 Scope。
2. 建立事实：区分 known facts、待验证事实和 assumptions。
3. 定义 source of truth、状态 owner 与副作用 Executor。
4. 写出关键 invariants。
5. 确定 schema、permission、state 与 validation boundaries。
6. 实现最小改动。
7. 测试成功路径。
8. 测试失败、取消和部分完成路径。
9. 测试 isolation、restart 和旧能力 regression。
10. 审阅 diff、日志、配置与敏感文件。
11. 涉及外部现实边界时执行 manual smoke test。
12. Automated Verification、Manual Acceptance 与必要的 Reality Check 全部通过后，才允许建议
    `SEALED`。

## 交付前 Reliability Check

结束任务前必须内部确认：

- **Facts**：是否猜了本可验证的事实？未验证 assumption 是否明确暴露？
- **Defaults**：无 override 是否正确？override 是否独立生效？
- **Ownership**：source of truth 与 state owner 是否唯一？
- **Isolation**：本次变化是否影响无关 Provider、Router、Tool、Memory、Session 或 UI？
- **Boundaries**：所有不可信输入是否按风险验证？
- **Failure**：失败、取消、partial 与 restart 是否安全？
- **Side Effects**：成功声明是否来自已验证 Executor 结果？
- **Environment**：是否依赖未声明的 cwd、shell、PATH、编码或 OS 行为？
- **Tests/Reality**：是否只有 happy path？是否把 mock PASS 错当真实系统 PASS？
- **Security/Scope**：日志和 diff 是否泄密或超出任务范围？

若存在尚未验证但会影响 production correctness 的事实，任务不能以“已完成”或“可封版”交付；
必须先验证，或明确报告 `NEEDS VERIFICATION` 并停止在相应验收点。
