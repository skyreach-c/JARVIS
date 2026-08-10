# Changelog

JARVIS 的重要版本变化记录在这里。详细问题、改进和验收档案见 [`docs/milestones.md`](docs/milestones.md)。历史条目的准确日期未被可靠保存，因此不补造日期；后续版本统一记录 `YYYY-MM-DD` 封版日期。

## [Unreleased]

- 当前没有已批准但尚未封版的功能。

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
