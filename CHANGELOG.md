# Changelog

JARVIS 的重要版本变化记录在这里。详细问题、改进和验收档案见 [`docs/milestones.md`](docs/milestones.md)。历史条目的准确日期未被可靠保存，因此不补造日期；后续版本统一记录 `YYYY-MM-DD` 封版日期。

## [Unreleased]

### Status

- 当前没有已批准但尚未封版的功能。

## [v0.5D] — Workspace Knowledge · 2026-08-11 · SEALED

Automated Verification: PASS · Manual Acceptance: PASS

### Added

- 新增 project-root-only 只读 `filesystem.read_text`，支持显式单文件路径、严格 UTF-8/BOM、完整行分页和固定 source/line/character/UTF-8 byte 预算。
- PERF 新增仅含数值的可选 `tool_observation_chars` 与 `tool_observation_utf8_bytes`，描述实际追加给 Chat Provider 的内部 Tool observation 规模。

### Security

- 路径保护、raw binary/NUL、encoding/control、竞态复核与全文件高置信 Secret 扫描均 fail closed；命中时不返回 partial 或 redacted content。
- 文件正文固定为 `untrusted_data`、`instruction_authority=none`，只发送给实际 Chat Provider，不进入 Agent Brain、Memory、Session 或 telemetry。

### Process

- 从后续版本开始正式采用 risk-proportional verification：低风险变更使用聚焦验证，中风险增加受影响回归和一次独立审查，只有安全、持久化、副作用或关键跨边界等高风险变更默认采用完整跨栈与专项审查；明确的用户、CI 和项目硬性要求始终优先。

### Known limitations

- Secret 扫描是 best-effort 而非完整 DLP；恶意本机 ABA race 与已经启动的 `asyncio.to_thread` 系统调用无法被完全消除或强制终止。
- 仍无搜索、RAG、索引、多 Tool loop、写入/删除、命令执行、Codex、Windows Action、Browser、ROS2、Task ID 或长任务。

## [v0.5C] — Capability Runtime Foundation · 2026-08-11 · SEALED

Automated Verification: PASS · Manual Acceptance: PASS

### Added

- v0.5C 在唯一生产 `ToolRegistry` 中按固定顺序注册只读 `system.get_runtime_info`、`system.get_os_info`、`filesystem.list_directory` 与 `filesystem.get_metadata`。
- 新增 project-root-only `ProjectPathPolicy`，为目录列表和单路径 metadata 提供统一范围、敏感目标与 reparse 检查。

### Fixed

- 将 OS 信息探测移入 worker thread，使 Registry timeout 可以及时 fail closed；已开始的底层调用仍不会被强制终止。

### Security

- 文件系统观察仅限 JARVIS project root 的安全目录名与元数据，不读取文件正文，也不提供写入、删除、命令或应用控制；这些 observation 会发送给实际 Chat Provider，并始终按不可信数据处理。

### Known limitations

- 当前仍没有 Codex、Windows control、Browser、ROS2、通用 PermissionPolicy 或任何副作用能力；路径前后复核不能消除恶意 ABA，`asyncio.to_thread` 超时也不能强制终止已经开始的系统调用。

## [v0.5B] — SEALED

### Added

- v0.5B Agent Runtime Foundation：新增 provider-neutral Agent Brain、最小 Context Builder、Tool Registry、严格 schema/risk/timeout 校验及单次 Tool 调度。
- 新增唯一生产 Tool `system.get_runtime_info`，仅返回 JARVIS 版本、运行状态及实际 Chat Profile/Provider/Model。
- 新增独立 `agent_brain_*`、`chat_*`、`memory_router_*` 与 `tool_*` 脱敏 telemetry。

### Changed

- 普通 Conversation 在 Memory terminal routing 和 Prompt 构建后统一委托 AgentRuntime；Session 仍只提交原始 user 与最终 assistant。
- Agent Brain 默认使用独立的 DeepSeek `agent_brain` 系统 Profile；Chat Profile 与 Memory Router Profile 保持隔离。
- Brain 决策失败会 fail closed 为 `agent_runtime_unavailable`，不会回退 Chat 或产生未经 Executor 验证的成功声明。

### Fixed

- Structured Brain 对非法 JSON、未知 action、schema 错误和非有限数值严格 fail closed；成功 observation 只能来自真实 Executor 结果。

## [v0.5A] — SEALED

### Added

- 保留 DeepSeek Chat，并增加 PackyCode Responses API Adapter 与 `gpt-5.6-sol` 强模型 Profile。
- 增加 `chat_default`、`reasoning_strong`、`structured_router` 的静态 Profile 装配和隔离 telemetry。

### Fixed

- 将 PackyCode production 默认 Base URL 固定为真实 API 验收通过的 `https://www.packyapi.com/v1`。
- Responses streaming 仅以 `response.completed` 为成功终态，并安全处理 refusal、failed、incomplete、truncated 与空响应。

## [v0.4C.2] — SEALED

### Added

- 增加基于真实 Memory ID 快照和事务验证的两阶段 `clear_all`。

### Fixed

- 修复清空确认 continuation 泄漏到 Chat Provider 并产生虚假成功声明的问题。
- 将 `pending_clear_all` 固定为 terminal local state，成功回复只来源于真实 `ClearAllResult`。

## [v0.4C.1] — SEALED

### Fixed

- 改进明确 Remember intent 的 gate 命中率和 Router JSON 兼容性。
- 增加 action-first 校验、脱敏失败分类和 RAM-only follow-up guard。

## [v0.4C] — SEALED

### Added

- 增加 Natural Memory Semantic Router、Core validation、多候选澄清和 Memory executor 安全边界。
- 普通聊天继续绕过 Router，候选 Memory 请求才产生额外 Router 调用。

## [v0.4B.1] — SEALED

### Fixed

- 增加 Unsupported Explicit Memory Intent 本地拦截，防止未执行操作由 Chat LLM 声称成功。
- 补充 `删除记忆1` 等少量安全、唯一、常见的本地 Fast Path。

## [v0.4B] — SEALED

### Added

- 增加确定性的自然语言 Remember、List 和 Forget 交互，同时保留 slash command。

## [v0.4A.1] — SEALED

### Added

- 增加 request-scoped、脱敏、best-effort 的 Python Core 延迟 telemetry。

## [v0.4A] — SEALED

### Added

- 增加基于 SQLite 的显式 Pinned Long-term Memory。
- 增加 `/remember`、`/memories` 和 `/forget` 本地命令。

## [v0.3] — SEALED

### Added

- 增加当前 Python Core 生命周期内最近 10 个完整 turns 的 Session Context。

## [v0.2B] — SEALED

### Added

- 增加独立 JARVIS Personality 和 Runtime Capability Constraints。

## [v0.2A] — SEALED

### Added

- 接入 DeepSeek 流式 Chat Completions，并保留 provider-neutral LLM 边界和离线 FakeConversation。

## [v0.1] — SEALED

### Added

- 建立 React Orb、Tauri 生命周期与 Python Core WebSocket 的最小端到端心跳。
