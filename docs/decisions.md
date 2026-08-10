# Architecture Decisions

## ADR-001：端到端薄切片

第一轮同时打通 Tauri Orb、Python Core、WebSocket 和状态机，不按前端或后端单独纵向开发。完成标准是可运行闭环，而不是模块数量。

## ADR-002：本机动态 WebSocket

Python 绑定 `127.0.0.1:0`。动态端口避免固定端口冲突；Tauri 从 stdout 的 `process.ready` 获取实际端口并交给 React。

## ADR-003：分离 stdout 与 stderr

stdout 是机器可读生命周期通道。普通日志和 traceback 使用 stderr，避免日志破坏 Tauri 对生命周期消息的解析。

## ADR-004：认证后再报告 Core Ready

`process.ready` 只代表端口已绑定。WebSocket 客户端必须首先发送 `auth`，认证成功后 Core 才发送 `core.ready`。

## ADR-005：请求事件关联

所有由 `chat.send` 引发的状态、增量、完成和错误事件保留原始 `requestId`。连接级错误不伪造请求标识。

## ADR-006：临时 token 的边界

临时 token 只做当前本机进程身份确认，不宣称提供完整本机安全隔离。完整安全模型延期设计。

## ADR-007：进程内 Session Context

v0.3 的 Session Context 只保存在 Python Core 内存中，并且只提交 provider 正常完整结束的 user/assistant turn。API 异常、流取消或消费者提前关闭时不保存当前 turn；关闭或重启 JARVIS 后历史自然清空。

默认仅保留最近 10 个完整 turns，超过上限时按完整 user/assistant pair 删除最老记录。该上限只限制轮数，不限制单轮或总上下文的实际 token 数量，较长输入或回复仍可能使请求上下文快速增长。这是 v0.3 的已知限制；token estimation、token budget、自动摘要和 compression 留待后续独立优化。

## ADR-008：显式 Pinned Long-term Memory

v0.4A 使用 Python 标准库 `sqlite3` 保存用户通过 `/remember` 明确提交的 Pinned Long-term Memory。生产数据库固定为 `%LOCALAPPDATA%\JARVIS\memory.db`；测试通过绝对路径的 `JARVIS_DATA_DIR` 或 `build_conversation(data_dir=...)` 使用独立临时目录，路径解析不依赖进程 cwd。数据库 schema 在 `process.ready` 前创建并严格验证；不兼容时启动失败，不迁移、不修复也不重建。

`/remember <content>`、`/memories` 和 `/forget <id>` 由 Conversation/Core 层确定性处理，不调用 LLM，也不进入 Session Context。Memory 命令的 SQLite 事务在固定回复产生前提交，因此客户端之后断开不会撤销已经完成的保存或删除。普通请求会把全部 Pinned Memory 作为独立的 `Long-term Pinned Memory` system 区段发送给当前 LLM Provider；Memory 是用户数据，不能覆盖 system 指令，也不属于 Personality。

`/forget <id>` 只删除持久化记录，不追溯清洗已经 committed 的当前 Session Context。如果相关信息此前已进入本次 Session，模型在当前 Core 生命周期内仍可能从 Session 中看到它；重启后 Session 清空，已删除的 Pinned Memory 不会恢复。v0.4A 不实现 Session redaction 或立即全局遗忘。

数据库是本机用户目录中的明文 SQLite，仅依赖 Windows 用户目录权限。SQLite 保存本身不消耗模型 token，但普通聊天注入的全部 Memory 会增加 DeepSeek input token 并发送给 DeepSeek。默认上限为 20 条，每条按 `len(content.strip())` 最多 500 个 Python 字符；这不是 token budget。本轮不实现 grapheme 计算、token estimation、选择性检索、Embedding、摘要或 compression，不应保存不愿发送给当前 Provider 的敏感信息。

## ADR-009: Request latency telemetry

v0.4A.1 records one best-effort `PERF` JSON summary to stderr for every
accepted `chat.send` request handled by `_run_chat`. A request-scoped
`ContextVar` carries a small `RequestTelemetry` object from Server into the
Conversation/Core layer. The Server always restores the exact previous
ContextVar value after success, error, or cancellation.

All durations use `time.perf_counter()`. LLM summaries distinguish SQLite
memory reads, prompt construction, Provider time to the first valid text
chunk, remaining streaming time, the complete Provider lifecycle, time to the
first successfully sent `chat.delta`, and the complete Core request lifecycle.
Provider streaming time is wall-clock time and can include downstream
generator/WebSocket backpressure. `prompt_chars` is only the sum of Python
string lengths for outgoing message content; it is not a token estimate.

Telemetry never records user text, pinned Memory content, complete prompts,
API keys, SQL parameters, or raw exception text. Memory commands record only a
safe command enum and operation timing. Telemetry failures are ignored and do
not change WebSocket events, state transitions, conversation history, Memory,
or Provider requests. No optimization or external metrics framework is part
of this milestone.

## ADR-010：确定性的自然语言 Memory 操作

v0.4B 只识别白名单内、结构完整且锚定整条输入的中文 Memory Intent。自然语言和 slash command 统一解析为 `ParsedMemoryCommand`，再复用同一套 MemoryStore 操作及固定回复；命令不调用 LLM、不消耗模型 token，也不进入 Session Context。原有 `/remember`、`/memories` 和 `/forget` 保留为精确及调试接口。

Remember 只接受明确前缀及有限结构分隔符；List 只接受少量完整短语；Forget 只接受能唯一解析出 ID 的确定性结构。结构完整但 ID 为 `0`、负数、带正号或非数字 token 时仍按 Forget Intent 本地处理，由现有正整数校验返回 usage，绝不删除记录或调用 Provider。少量安全、唯一且常用的短格式（例如 `删除记忆1`）作为 deterministic Fast Path 本地执行。

Fast Path 无法执行、但明显是在要求 Remember/List/Forget 操作的输入，会被归类为 Unsupported Explicit Memory Intent：Core 返回确定性格式提示，不调用 Provider、不修改 Memory，也不进入 Session Context。普通讨论（例如“你觉得长期记忆有什么用？”或“人为什么会忘记东西？”）仍进入普通 LLM 流程。该边界同时遵循“宁可少识别，也不错误持久化”和“未执行的副作用不得由模型宣称成功”两项原则。

Natural Memory command 继续使用现有 `memory_command` telemetry，只记录安全命令名和耗时，不记录 Memory 内容。v0.4B 不实现 LLM intent classifier、自动持久化、语义删除、Memory extraction、检索或 UI。

## ADR-011：副作用执行边界与 v0.4C Natural Memory Router 方向

v0.4B.1 是 Natural Memory Interaction 的副作用安全补丁，不是持续扩展固定中文句式的里程碑。现有 slash command 与极少量确定性 Fast Path 可以直接执行并保持零 LLM token；Fast Path 未命中但存在明确 Memory 操作意图时，本地拦截并提示受支持格式。任何具有副作用的成功声明都必须来源于 Python Core executor 的真实成功结果。LLM 可以理解或建议 action，但不能自行修改 SQLite，也不能在 executor 未成功时声称操作成功。该原则未来同样适用于文件、应用、邮件、Codex、Windows、ROS2 和机器人控制。

v0.4C 的目标方向为：

```text
user
→ deterministic memory fast path
→ potential-memory-intent gate
→ semantic Memory Intent Router
→ structured intent
→ Core validation / disambiguation
→ MemoryStore executor
→ execution-result-derived response
```

Semantic Router 只输出严格结构化意图：`action = chat | remember | list | forget | clarify`，以及可选的 `content` 和 `memory_ids`；不得直接执行副作用。Core 将 Router 输出视为不可信输入，验证 action、remember content、Memory ID 是否真实存在以及 forget target 是否唯一。出现多个合理候选时必须澄清，不得猜测或任意删除。

为了避免普通聊天产生 Router + Chat LLM 的双重请求和额外首响应延迟，v0.4C 应先经过轻量 potential-memory-intent gate，只有候选 Memory 请求才调用 semantic Router；普通聊天继续保持当前单次 Provider 路径。该设计应为未来通用 Supervisor / Tool Routing 留出扩展空间，但 v0.4C 仍只处理用户明确表达的 Memory 意图，不实现普通聊天自动 Memory extraction、自动判断长期价值、Session 总结、Embedding、Vector DB、自动项目 Resume、通用 Windows Tools 或完整 Supervisor。

该方向在 v0.4B.1 阶段只记录、不提前实现；实现属于后续独立里程碑。

## ADR-012：v0.4C Semantic Memory Router 与副作用信任边界

v0.4C 保留所有 slash command 和少量可唯一、安全解析的自然语言 Memory Fast Path。Fast Path 仍是纯本地路径：不调用模型、不消耗 Router token，也不把命令写入 Session Context。Fast Path 未命中时，只有轻量本地 potential-memory-intent gate 判定为候选的请求才进入 Semantic Router；普通聊天不额外调用 Router，继续沿用单次 Chat Provider 路径。

Semantic Router 通过 provider-neutral `MemoryIntentRouter` 和 `StructuredLLMClient` 边界接入。当前 `DeepSeekStructuredClient` 使用 Chat Completions JSON mode、非流式短响应、零 SDK retry 和短超时，并显式发送 `extra_body={"thinking":{"type":"disabled"}}`。Memory-specific prompt、JSON schema 和解析属于 Core Router 层，不进入 DeepSeek Provider。Provider 只返回原始 JSON 文本，不访问 SQLite，也不生成面向用户的成功回复。未来 Local Router 可以在 `MemoryIntentRouter` 或 `StructuredLLMClient` 边界替换当前云端实现，不需要改变 Conversation、MemoryStore 或 WebSocket。

Router 输出使用严格 Pydantic v2 schema：`action = chat | remember | list | forget | clarify`，所有字段必须存在，额外字段、未知 action、布尔/非正/重复 ID 和 action/参数不一致全部拒绝。JSON 为空、截断、无法解析或 schema 不合法时 fail closed：Core 不调用 MemoryStore 副作用，不回退到可能伪造执行结果的普通 Chat LLM，而是返回明确的本地“未执行”提示。Router 超时和 Provider 异常使用同样的无副作用恢复语义。

所有 Router 输出均视为不可信建议。Remember 的 evidence 必须是当前用户文本或最近 user Session 消息中的精确非空片段；提取后的 content 仍经过既有空值、500 Python 字符、重复和 20 条容量校验。该 evidence 检查只能证明引用来源，不能形式化证明语义蕴含，因此语义不确定时必须澄清而不是猜测。Forget ID 在 Router 返回后重新对当前 Store 快照验证；只有一个真实存在的目标才允许执行。零个、虚构或多个合理目标都不删除，多候选进入 RAM-only ID 澄清。任何“已保存”或“已删除”回复只能由 `MemoryExecutionResult` 中的真实 Store 执行结果产生。

为解析“刚保存那个”“刚列出的记录”和多候选后续 ID，Coordinator 仅在当前 Core 生命周期内保存 `last_created_memory_id`、`last_listed_memory_ids` 和 pending forget candidate IDs。这些元数据来自真实 Store 结果，不保存完整命令文本、不写入普通 Session、也不持久化；重启后自然清空。Router 失败保留 pending 候选，明确取消、成功处理或切换到其他已验证操作会清除 pending。

### Semantic Router 数据边界

Deterministic Memory Fast Path 完全在本机执行并保持零 token。只有 gate 命中的候选 Memory 请求会把完成意图理解所必需的数据发送给当前云端 Provider：当前用户文本、当前全部 Pinned Memories（仍受 20 条上限约束）、最近两条已提交的 user Session 文本，以及最近 Memory 交互的 ID 元数据和 pending candidate IDs。不会发送 Personality、完整 Session、无关 assistant 回复、完整普通聊天 prompt、API Key 或其他无关数据。Router prompt、原始 JSON 输出、用户文本与 Memory 内容均不得写入日志。使用云端 Semantic Router 会为候选请求增加一次请求延迟和 input token；本轮不做缓存、token estimation、Embedding、检索或 Local Model。

Router telemetry 仅增加 `memory_router_ms` 和有限枚举 `memory_router_action`，不记录输入、Memory、prompt、原始响应或密钥。该设计继续遵守通用副作用原则：模型只能理解并提出 action，Core 负责验证，executor 负责执行，真实 execution result 是成功声明的唯一来源。未来文件、Windows、Codex 或 ROS2 Tool Routing 可以复用这一 propose → validate → execute → result 边界，但 v0.4C 不实现通用 Supervisor 或任何非 Memory Tool。

## ADR-013：v0.4C.1 Semantic Router 可靠性与安全跟进

真实 DeepSeek 验收暴露了两类可靠性问题：明确的“帮我长期记下来”可能被本地 gate 漏掉；Router JSON 即使语义合理，也可能因省略不适用字段而被统一折叠为 `invalid_schema`。v0.4C.1 只修复这两个边界，不继续扩大 deterministic Natural Memory Fast Path。Gate 仅补充少量明确的持久化动作短语，并继续优先排除“为什么”“怎么设计”“有什么风险”等 Memory 概念讨论；普通聊天仍不额外调用 Router。

Router prompt 继续要求输出 `action`、`content`、`memory_ids`、`evidence` 四个字段，并为 `chat/list/remember/forget/clarify` 给出完整 JSON 示例。Core 在 wire 边界为缺失的非 action 字段提供安全默认值 `null` 或 `[]`，这只是降低云端 JSON 形状脆弱性，不代表业务字段可选。处理顺序固定为：先从原始 JSON 检查 action，再完成严格类型解析，最后由 Coordinator 执行 action-specific Core validation。未知或非字符串 action 归类为 `invalid_action`；结构错误归类为 `invalid_schema`；非法、缺失、重复或不存在的 Memory ID 归类为 `id_validation`；无法从允许的用户消息精确验证 remember 内容来源时归类为 `evidence_validation`。`forget + memory_ids=[]` 永远不能执行。`remember + evidence=[]` 仅在 content 本身是当前用户文本或最近两条允许 user Session 消息中的精确非空子串时才可继续。

Router 失败诊断只在 stderr 写入安全字段：`request_id`、`stage=provider|router_parse|core_validation`、有限失败 code 和异常/校验类型；不写 Router 原始响应、Pydantic input、用户文本、Memory 内容、Prompt、Provider 响应体或 API Key。该诊断用于第二轮真实 API 验收区分 `invalid_json / invalid_schema / invalid_action / evidence_validation / id_validation / provider_error`，不改变 WebSocket 或 PERF telemetry schema。

多候选删除的 pending 仍只能由 Core 对真实 Store ID 验证后建立。pending 中的合法候选 ID 直接交给 executor；非候选 ID 由本地确定性回复拒绝并保留 pending，不调用 Router 或普通 Chat LLM。无 pending 时，纯数字（例如 `17`）以及普通 ID 形式仍属于普通输入，不能直接触发删除。为避免一次 Router fail-closed 后紧接的 `#17` 或 `第17条` 落入 Chat LLM 并产生“已删除”幻觉，Coordinator 只保留一个 RAM-only、单次消费的 follow-up guard：它仅返回“没有已确认候选”的本地无副作用提示，不把 ID 当作授权、不执行 Store，也不发展为 Memory 历史。真正的删除成功回复仍只能来自 `MemoryStore.forget()` 的真实结果。

## ADR-014：v0.4C.2 Memory 安全域与两阶段清空

v0.4C.2 将 potential-memory-intent gate 命中的请求视为已经进入 Memory 安全域。进入该域后，Router 返回 `chat`、Provider 失败、JSON/schema 非法或 Core validation 失败都只能产生本地确定性无副作用回复，不得回退到普通 Chat Provider。这样普通模型无法在 executor 未运行时生成“已保存”“已删除”或“已清空”等虚假副作用声明。普通 Memory 概念讨论仍由本地 gate 排除并沿用原 Chat 路径。

`clear_all` 是受控的 structured Memory action。Router 只能提出该 action；Core 首次收到合法意图时读取真实 Pinned Memory ID 快照并建立 RAM-only `pending_clear_all`，只回复待确认数量，不立即删除。pending 存在期间仅接受有限的确认或取消表达，其他输入全部由 Core 本地提示，不进入 Router 或 Chat。Core 重启会自然丢弃 pending，孤立的明确清空确认不会执行任何副作用。

确认后，`MemoryStore.clear_all(expected_ids)` 在单个 `BEGIN IMMEDIATE` 事务中重新读取当前 ID 快照。只有快照与确认前完全一致才执行删除；快照变化、SQL 失败或受影响行数异常均 fail closed，不产生成功声明。`ClearAllResult.cleared_count` 来自真实 executor 结果，且只有 `status=cleared` 才能生成“已清空 N 条长期记忆”。成功清空会移除 Coordinator 的 recent Memory ID 元数据，但与单条 `/forget` 相同，不追溯改写当前 committed Session Context。

Router 失败诊断固定为脱敏的 `stage/category/reason/fields/error_type`。字段名只允许 `action/content/memory_ids/evidence/root`，未知字段统一记录为 `root`；日志不包含 Router raw response、用户文本、Memory 内容、Prompt、Provider 响应体或密钥。`删除 ROS2 那条` 的多候选 Core 路径要求 Router 提供经过 Store 验证的真实候选 ID，再建立 pending 并由用户本地选择；在没有真实失败分类证据时，不通过盲目扩写 Prompt 掩盖 wire/schema 问题。

## v0.4C 系列封版状态

```text
v0.4C   Manual Acceptance: PASS   SEALED
v0.4C.1 Manual Acceptance: PASS   SEALED
v0.4C.2 Manual Acceptance: PASS   SEALED
```

上述三个里程碑已完成人工验收并正式封存。后续开发不得在没有独立里程碑、明确范围和回归验证的情况下改变其既有路由、安全域、校验或执行语义。

完整迭代、问题、改进与封版状态统一归档在 [`milestones.md`](milestones.md)。

### 全局副作用成功声明不变量

```text
Side-effect success claims MUST originate
from verified executor results.
```

任何模型、Router、Conversation 或展示层都只能理解、提出或呈现操作；只有经过 Core 校验并由真实 executor 成功执行后返回的结果，才能成为“已保存”“已删除”“已清空”以及未来文件、应用、邮件、Windows、Codex、ROS2 或机器人控制等副作用成功声明的来源。未执行、执行失败、结果未验证或状态不确定时，必须 fail closed，不得声称成功。

## ADR-015：v0.5A 多模型 Provider 基础

JARVIS Core 保留 provider-neutral `LLMClient.stream_chat(messages)` 和
`StructuredLLMClient.complete_json(messages)` 边界。普通聊天新增 PackyCode
Responses API Adapter，同时保留现有 DeepSeek Provider。Provider 表示服务商和线协议，
Model 表示实际请求的模型 ID，Model Profile 表示某项 Core 工作负载使用的 Provider、
Model 及推理参数组合；PackyCode 的 `codex` 分组属于服务商 token/计费路由信息，
不是模型 ID，也不在没有文档依据时作为额外请求字段发送。

v0.5A 使用三个静态 Profile：`chat_default` 指向 DeepSeek，
`reasoning_strong` 指向 PackyCode `gpt-5.6-sol` 且默认 reasoning effort 为 `low`，
`structured_router` 继续指向 DeepSeek。只有前两个 Profile 可以通过
`JARVIS_CHAT_PROFILE` 选择；本轮不实现自动模型 Router、fallback、并行回答、投票或
运行时 UI 切换。PackyCode Key 缺失不阻止 Core readiness，SDK client 仅在实际请求时
延迟创建。

PackyCode Adapter 使用 `/v1/responses`、`store=false` 和显式的 60 秒 timeout、零 SDK
retry。每轮仍由 JARVIS 从 Personality、Pinned Memory、Session Context 和当前用户消息
重新构造完整输入；不使用 `previous_response_id`、provider-side conversation 或 persisted
reasoning state。只有非空 `response.output_text.delta` 和 `response.refusal.delta` 会成为
可见文本，且 `response.completed` 是唯一正常完成终态。failed、incomplete、缺少
completed 或 completed 无可见文本均 fail closed，不能提交不完整 Session turn。

选择 PackyCode 普通聊天时，正常 Chat prompt 中的 Pinned Memory 和 Session Context 会
发送给 PackyCode；Semantic Memory Router 所需的有限上下文仍发送给 DeepSeek。PERF
日志分别记录实际 Chat client 的 `profile/provider/model` 和实际 Router client 的
`memory_router_profile/memory_router_provider/memory_router_model`，不得把启动时的 Chat
Profile 错标为 Router Provider。日志不记录 API Key、Authorization、Base URL、Prompt、
用户文本、Memory 内容或 Provider 原始响应。

第三方 Provider 的底层实际模型无法由 JARVIS 加密验证；Core 只能保证请求中的模型 ID
为配置值。人工验收需要在 PackyCode 消费日志中核对真实模型和费用。v0.5A 已完成
DeepSeek 与 PackyCode 真实 API、Profile 隔离、Memory Router 隔离及副作用安全回归，并于
2026-08-11 标记为 `Manual Acceptance: PASS · SEALED`。

## ADR-016：v0.5B Agent Runtime Foundation 方向（暂定）

v0.5A 完成后的下一阶段暂定为 v0.5B Agent Runtime Foundation。JARVIS 最终不由
Python Core 硬编码具体任务 workflow；目标运行循环为：

```text
Model → Tool Call → Core Validation → Executor → Tool Result → Model
```

在该方向中，Supervisor/Agent Model 根据当前上下文和真实 Tool Result 动态决定下一步。
Python Core 只提供通用 Tool Registry、schema validation、permission boundary、execution
与 observation feedback，不预先写死“调用 Codex 后做什么”“打开 VS Code 后做什么”等
任务流程。任何 Tool 输出和副作用仍必须通过 Core 校验与真实 executor 执行，副作用成功
声明继续遵守全局 invariant：只能来源于已验证的 executor 结果。

该条目仅记录后续架构方向，不代表 v0.5B 已获最终 Scope 批准。v0.5A 不实现 Tool
Registry、Supervisor、Agent loop、Codex/Windows/ROS2 Tool 或固定 workflow。

## ADR-017：采用项目级 Engineering Reliability Rules

JARVIS 将 [`engineering-reliability.md`](engineering-reliability.md) 作为所有重要功能开发的
项目级工程约束。后续设计、实现、测试、外部 smoke test、验收与封版都必须遵守其中对事实
验证、Source of Truth、默认配置、状态 ownership、模块隔离、边界校验、fail-closed 副作用、
诊断脱敏、Scope 控制和 Reality Check 的 Hard Rules。

该决策适用于 Provider、API、Python Core、Tauri、React、WebSocket、Memory、Agent Runtime、
Tools、Codex、Browser、Computer Use、Vision、Windows、文件系统、数据库、ROS2、外部程序和
硬件边界，不绑定某个版本或某一家 Provider。规则只约束工程开发与验收流程；本次决策不修改
任何 runtime 行为。
