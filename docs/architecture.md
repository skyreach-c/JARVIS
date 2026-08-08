# JARVIS v0.1 第一轮架构

## 唯一目标

```text
点击 Orb
→ Python Core 收到 chat.send
→ THINKING
→ RESPONDING
→ 一个或多个 chat.delta
→ chat.completed
→ IDLE
```

第一轮只验证 React、Tauri/Rust、Python Core 和 WebSocket 的完整数据链。自动测试与完整闭环通过后停止，等待 Windows 人工验收。

## 三层边界

- React 负责 Orb、输入、状态和回复展示。
- Tauri/Rust 负责窗口及 Python Core 生命周期。
- Python Core 负责认证、协议、状态机和固定分片回复。

开发阶段只运行 `scripts/dev.ps1`。Tauri 生成临时 token，启动 Python，读取其生命周期消息，并在退出时结束子进程。React 从 Tauri IPC 获取连接信息后直接连接本机 WebSocket。

## 进程启动协议

Python 使用 `127.0.0.1:0`，由操作系统分配端口。绑定成功后，stdout 只输出一行机器可读生命周期消息：

```json
{"type":"process.ready","port":54321,"protocolVersion":1}
```

普通日志、诊断信息和 traceback 只能写入 stderr。`process.ready` 只表示 Server 已绑定端口，不表示 WebSocket 客户端已认证。

## WebSocket 协议

UI 建连后的第一条消息必须是 `auth`。认证成功后 Core 才发送 `core.ready`。

消息信封：

```json
{
  "version": 1,
  "type": "chat.send",
  "requestId": "uuid",
  "payload": {}
}
```

每个 `chat.send` 的所有请求级事件必须保留原始 `requestId`：

```text
state.changed(THINKING)
state.changed(RESPONDING)
chat.delta...
chat.completed
state.changed(IDLE)
```

请求级 `error` 也保留原始 `requestId`。没有对应 `chat.send` 的连接级错误不伪造 requestId。

临时 token 只是当前本机进程身份确认机制，不是未来完整安全边界。

## 明确延期

第一轮不实现托盘、自启动、单实例、Orb 拖动、真实 LLM、Memory、Voice、Tools、Codex Agent、Supervisor、ROS2、聊天历史、设置、安装包和 UI 精修。`SPEAKING` 只在真正加入 TTS 后增加。
