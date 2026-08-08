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
