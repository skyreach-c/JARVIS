# JARVIS 迭代与封版档案

本文档是 JARVIS 的单一版本总账。每个里程碑在人工验收并封版后，都必须在这里记录：

- 实现功能
- 验收或开发中发现的问题
- 最终改进与关键边界
- 自动检查与人工验收状态

详细架构理由继续记录在 [`decisions.md`](decisions.md)，本文件只维护稳定的版本演进事实。已封版条目只允许补充勘误，不应静默改写历史。

## 轻量封版流程

每个版本在用户明确完成人工验收后，由 Codex 自动完成以下记录，不需要用户再次提醒：

1. 在本文件追加或完善该版本的目标、实现功能、实际问题、根因、最终改进、验证结果、已知限制和状态。
2. 在项目根目录 [`CHANGELOG.md`](../CHANGELOG.md) 添加面向 GitHub 读者的简短变化摘要。
3. 仅在用户明确确认人工验收通过后标记 `Manual Acceptance: PASS · SEALED`。
4. 执行 `git diff --check`，检查仓库内不存在 `.env`、API Key、`memory.db`、用户私人 Memory 或未脱敏日志。
5. 将版本代码、测试和封档文档提交到 `main` 并推送至当前 GitHub `origin`，再核对远程提交与本地一致；推送失败时如实报告，不将版本标记为“已同步 GitHub”。
6. 封版以后只追加勘误；新的行为变化必须进入新版本，不能静默改写已封版语义。Tag、GitHub Release 和仓库可见性变更仍需用户单独确认。

历史版本是根据现有实现、测试记录和人工验收结果回填的；没有可靠记录的信息不补造日期、Commit 或测试数字。

## 全局架构不变量

```text
Side-effect success claims MUST originate
from verified executor results.
```

模型或 Router 只能理解、提出操作。Core 必须验证操作，executor 必须真实执行；只有经过验证的 executor 成功结果，才能生成副作用成功声明。未执行、执行失败、结果未验证或状态不确定时必须 fail closed。

## v0.1 — Minimum End-to-End Heartbeat

**状态：Manual Acceptance: PASS · SEALED**

- 实现功能：建立 React Orb → Tauri/Rust 生命周期 → Python Core WebSocket 的最小闭环；完成认证、动态端口、固定分片回复和请求级 `requestId` 传播。
- 发现问题：process readiness 与 WebSocket readiness 容易混淆；首个 delta 的状态时序和 stdout/stderr 职责需要锁定。
- 最终改进：明确 `process.ready` 只经 stdout 报告端口，认证成功后才发送 `core.ready`；正常顺序固定为 `THINKING → RESPONDING → chat.delta(s) → chat.completed → IDLE`。
- 边界：临时 token 只用于本机进程身份确认，不构成完整安全边界。

## v0.2A — DeepSeek LLM Integration

**状态：Manual Acceptance: PASS · SEALED**

- 实现功能：生产路径从固定回复切换为 `LLMConversation + DeepSeekClient`，使用 `AsyncOpenAI`、Chat Completions 和流式响应；`FakeConversation` 保留用于离线测试。
- 发现问题：API Key 缺失、网络/API 错误、空 chunk、空 `choices`、400/422 和启动 cwd 都可能破坏闭环或配置加载。
- 最终改进：增加 provider abstraction、根目录 `.env` 的绝对定位、完整错误映射和空分片防护；第一阶段显式关闭 Thinking Mode。
- 边界：每次只发送当前用户消息；不实现聊天历史、Memory、Tools 或其他 Provider。

## v0.2B — Personality

**状态：Manual Acceptance: PASS · SEALED**

- 实现功能：增加独立 Personality 与 Runtime Capability Constraints，并由 Conversation 组合为分区清晰的 system message。
- 发现问题：模型容易过度展开，并在完整回答后机械追加“还需要什么”一类邀请式追问。
- 最终改进：强化最短有效回答和自然终止规则；Personality 保持稳定人格，Runtime Capability 单独描述当前能力边界。
- 边界：Provider 不包含 JARVIS 人格；不加入 Session History 或长期 Memory。

## v0.3 — Session Context

**状态：Manual Acceptance: PASS · SEALED**

- 实现功能：在单个 Python Core 生命周期内保存最近 10 个完整 user/assistant turns，并按 `system → committed history → current user` 发送给 Provider。
- 发现问题：流式生成中途失败、取消或消费者提前关闭时，partial assistant 不能污染历史。
- 最终改进：仅在 Provider stream 正常完整结束后原子提交当前 turn；失败、取消、空响应均不提交。
- 边界：历史仅在 RAM 中；重启即清空。10 turns 只限制轮数，不限制真实 token 数量。

## v0.4A — Persistent Pinned Memory

**状态：Manual Acceptance: PASS · SEALED**

- 实现功能：使用本机 SQLite 保存用户显式指定的 Pinned Long-term Memory；提供 `/remember`、`/memories`、`/forget`。
- 发现问题：需要防止测试污染真实数据库、旧 schema 静默不兼容、删除后仍从当前 Session 看见旧信息等语义混淆。
- 最终改进：数据库固定到 `%LOCALAPPDATA%\JARVIS\memory.db`，支持绝对测试目录覆盖；启动前严格验证 schema；Memory 命令不调用 LLM、不进入 Session。
- 边界：最多 20 条，每条按 `len(content.strip())` 最多 500 个 Python 字符；`/forget` 不追溯清洗当前 Session。

## v0.4A.1 — Latency Telemetry

**状态：Manual Acceptance: PASS · SEALED**

- 实现功能：使用 `time.perf_counter()` 和 request-scoped `ContextVar`，为普通请求与 Memory 命令输出单条脱敏 PERF 汇总。
- 发现问题：需要区分 SQLite、prompt build、Provider 首 token、后续 streaming 与用户感知首 delta；telemetry 还不能泄漏到下一请求或反向影响业务。
- 最终改进：增加 `provider_first_token_ms`、`first_delta_ms` 等阶段耗时；成功、失败和取消路径都无条件 reset ContextVar；telemetry 始终 best-effort。
- 边界：不记录用户文本、Memory、Prompt、密钥或异常原文；`prompt_chars` 不是 token count。

## v0.4B — Natural Memory Interaction

**状态：Manual Acceptance: PASS · SEALED**

- 实现功能：在保留 slash command 的同时，以 deterministic local parser 支持少量明确的自然语言 Remember/List/Forget 表达。
- 发现问题：固定白名单之外的明显删除意图可能落入普通 Chat，模型会在未执行 Store 时声称“已删除”。
- 最终改进：统一 `ParsedMemoryCommand` 与 MemoryStore 执行路径；明确命令不调用 Provider、不进入 Session，并坚持保守识别。
- 边界：不使用 LLM 判断 intent，不实现语义删除或普通聊天自动记忆。

## v0.4B.1 — Side-effect Safety Patch

**状态：Manual Acceptance: PASS · SEALED**

- 实现功能：补充少量安全、唯一、常见的 deterministic Fast Path，并增加 Unsupported Explicit Memory Intent 本地拦截。
- 发现问题：`删除记忆1` 等常见表达可能漏过 parser；未执行的副作用请求一旦进入 Chat Provider，就可能产生假成功声明。
- 最终改进：明显 Memory 操作但无法安全执行时返回本地确定性提示，不调用 Provider、不修改 Memory；真实成功回复只由 MemoryStore 结果生成。
- 边界：停止无限扩展 regex 白名单，后续自然理解交给独立 Semantic Router。

## v0.4C — Natural Memory Router

**状态：Manual Acceptance: PASS · SEALED**

- 实现功能：建立 `Fast Path → potential-memory-intent gate → Semantic Router → Core validation → MemoryStore executor`；Router 只输出结构化意图。
- 发现问题：真实 DeepSeek 中出现 gate false negative、Router JSON/schema 脆弱、多候选删除和代词引用可靠性不足。
- 最终改进：引入 provider-neutral Router abstraction、严格结构校验、真实 Store ID 验证、多候选 pending 与极小的 RAM-only Memory interaction context。
- 边界：只有候选 Memory 请求调用 Router；普通聊天维持单次 Chat Provider，不实现自动记忆、Embedding 或通用 Supervisor。

## v0.4C.1 — Semantic Router Reliability

**状态：Manual Acceptance: PASS · SEALED**

- 实现功能：提高明确 Remember intent 的 gate 命中率，并增强 Router wire JSON 与 Core action-specific validation 的兼容性。
- 发现问题：缺失非 action 字段会被折叠为 `invalid_schema`；未知 action、evidence 和 ID 校验失败难以通过脱敏日志区分；孤立 ID follow-up 可能回落 Chat。
- 最终改进：先检查 action，再应用安全字段默认值并执行严格业务校验；增加脱敏 stage/category/reason；加入单次 RAM-only follow-up guard。
- 边界：安全默认值不代表业务字段可缺失；无真实 pending 时纯数字仍是普通输入，不能授权删除。

## v0.4C.2 — Memory Safety Domain and Confirmed Clear-All

**状态：Manual Acceptance: PASS · SEALED**

- 实现功能：候选 Memory 请求进入安全域后不再回退普通 Chat；增加基于真实 ID 快照的两阶段 `clear_all` 确认与事务执行。
- 发现问题：清空确认 continuation 曾泄漏到 Chat Provider，模型声称成功但 SQLite 未发生变化；多候选 ROS2 删除需要可靠澄清。
- 最终改进：`pending_clear_all` 成为最高优先级 terminal local state；Conversation 使用显式 `handled` 结果阻断 Router/Chat；只有真实 `ClearAllResult` 能生成清空成功回复。
- 自动验证：2026-08-10 运行 `scripts/check.ps1`；Python 376、React 7、Rust 6 项测试通过，Ruff、React production build 与 Rust fmt/check 通过。
- 人工验收：真实 DeepSeek Remember、Forget、多候选澄清、clear-all confirmation、跨重启持久化及普通聊天隔离均为 PASS。
- 边界：确认前 Store 快照变化、executor 失败或输入不明确时一律不删除并 fail closed；Core 重启自然清除 pending。

## v0.5A — Multi-Model Provider Foundation

**状态：Manual Acceptance: PASS · SEALED**

- 封版日期：2026-08-11
- 唯一目标：保留 DeepSeek 的同时接入 PackyCode Responses API，使普通 Chat 可以通过静态 Profile 在两个 Provider 间切换，Memory Router 继续固定使用 DeepSeek。
- 实现功能：增加 `chat_default`、`reasoning_strong` 和 `structured_router` Profile；增加 lazy `PackyCodeResponsesClient`，使用 `gpt-5.6-sol`、`reasoning.effort=low`、`store=false`、60 秒 timeout 和零 SDK retry；流式支持 output/refusal delta，并只接受 `response.completed` 正常终态。
- 发现问题：服务商页面早期示例 endpoint 与真实可用 endpoint 不一致；同一请求中的 Chat Provider 与 Memory Router Provider 容易在 telemetry 中被错误混标；Responses API 的 EOF、failed、incomplete 和空 completed 必须明确 fail closed。
- 根因：第三方 OpenAI-compatible 服务只兼容特定协议面，不能依据名称假设 endpoint、模型、终态和扩展行为与官方完全一致；Chat Profile 和系统 Router Profile若共享模糊身份字段会破坏可诊断性。
- 最终改进：生产默认 Base URL 固定为已实测的 `https://www.packyapi.com/v1`，同时保留环境变量 override；Chat 与 Memory Router 分别记录实际 Profile/Provider/Model；Provider Adapter 将 Responses event 转换为现有统一文本流，不把第三方结构泄漏到 Conversation。
- 自动验证：2026-08-11 运行 `scripts/check.ps1`；Python 407、React 7、Rust 6 项测试通过，Ruff、React production build 与 Rust fmt/check 通过。
- 人工验收：`chat_default → DeepSeek/deepseek-v4-flash`、`reasoning_strong → PackyCode/gpt-5.6-sol` 均完成真实流式调用；reasoning_strong 下 Semantic Memory Router 仍固定使用 DeepSeek；Memory Fast Path、Forget、clear-all confirmation 与 executor-only side-effect safety 回归均为 PASS。
- 安全与数据边界：API Key 只从根目录 `.env`/进程环境读取，不进入源码、测试、日志或 Git；选择 PackyCode 时，正常 Chat 的 Personality、Pinned Memory、Session Context 和当前用户消息会发送给 PackyCode，Memory Router 所需的有限上下文仍发送给 DeepSeek。
- 已知限制：第三方 Provider 的底层实际模型无法由 Core 加密验证；Profile 只能在启动时静态选择；没有自动 Model Router、fallback、并行回答、模型投票或 UI 切换。
- 边界：不修改 Memory、Session、WebSocket、React 或 Tauri；不实现 Agent Runtime、Supervisor、Codex/Windows Tools 或 provider-side conversation state。

## 后续封版记录模板

```markdown
## vX.Y — Milestone Name

**状态：Manual Acceptance: PASS · SEALED**

- 封版日期：YYYY-MM-DD
- 唯一目标：
- 实现功能：
- 发现问题：
- 根因：
- 最终改进：
- 自动验证：测试命令与结果摘要
- 人工验收：核心场景与 PASS 结论
- 安全与数据边界：
- 已知限制：
- 边界：
```
