# JARVIS

JARVIS v0.1 的第一轮只实现一个最小端到端心跳：Tauri 管理 Python Core，React 通过本机 WebSocket 完成一次带状态变化的固定分片回复。

## 开发命令

```powershell
.\scripts\bootstrap.ps1
.\scripts\dev.ps1
```

`dev.ps1` 是开发阶段的唯一启动入口。它只启动 Tauri；Python Core 由 Tauri 自动启动和停止。

运行全部自动检查：

```powershell
.\scripts\check.ps1
```

当前范围与协议见 `docs/architecture.md`，关键决策见 `docs/decisions.md`。

## Windows 人工验收

1. 运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1`。
2. 确认 Tauri 自动启动后，Orb 显示 `IDLE`。
3. 点击 Orb，在展开面板中输入任意消息并发送。
4. 依次确认 `THINKING`、`RESPONDING`、分片回复和 `IDLE`。
5. 修改 `core/src/jarvis_core/conversation.py` 中的固定回复，重新运行并确认 UI 内容随之变化。
6. 关闭 JARVIS 窗口，确认 Python Core 同时退出。
7. 在无网络、无 API Key 的条件下重复一次发送，确认闭环仍然成立。
