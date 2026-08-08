import { describe, expect, it } from "vitest";

import type { ServerMessage } from "../core/protocol";
import {
  initialJarvisState,
  jarvisReducer,
} from "./jarvisReducer";

function message(
  type: ServerMessage["type"],
  payload: Record<string, unknown>,
  requestId?: string,
): ServerMessage {
  return { version: 1, type, payload, requestId };
}

describe("jarvisReducer", () => {
  it("becomes READY only when core.ready arrives", () => {
    const ready = jarvisReducer(initialJarvisState, {
      type: "server.message",
      message: message("core.ready", { state: "IDLE" }),
    });

    expect(ready.coreStatus).toBe("READY");
    expect(ready.jarvisState).toBe("IDLE");
  });

  it("aggregates only the active request and follows the heartbeat", () => {
    let state = jarvisReducer(initialJarvisState, {
      type: "server.message",
      message: message("core.ready", { state: "IDLE" }),
    });
    state = jarvisReducer(state, {
      type: "request.started",
      requestId: "request-a",
    });

    state = jarvisReducer(state, {
      type: "server.message",
      message: message("chat.delta", { text: "wrong" }, "request-b"),
    });
    expect(state.assistantText).toBe("");

    state = jarvisReducer(state, {
      type: "server.message",
      message: message("state.changed", { state: "THINKING" }, "request-a"),
    });
    expect(state.jarvisState).toBe("THINKING");

    state = jarvisReducer(state, {
      type: "server.message",
      message: message("state.changed", { state: "RESPONDING" }, "request-a"),
    });
    state = jarvisReducer(state, {
      type: "server.message",
      message: message("chat.delta", { text: "JARVIS " }, "request-a"),
    });
    state = jarvisReducer(state, {
      type: "server.message",
      message: message("chat.delta", { text: "online" }, "request-a"),
    });
    expect(state.jarvisState).toBe("RESPONDING");
    expect(state.assistantText).toBe("JARVIS online");

    state = jarvisReducer(state, {
      type: "server.message",
      message: message("chat.completed", { text: "JARVIS online" }, "request-a"),
    });
    state = jarvisReducer(state, {
      type: "server.message",
      message: message("state.changed", { state: "IDLE" }, "request-a"),
    });
    expect(state.jarvisState).toBe("IDLE");
    expect(state.activeRequestId).toBeNull();
    expect(state.assistantText).toBe("JARVIS online");
  });

  it("correlates request errors and moves the UI back to IDLE", () => {
    const active = {
      ...initialJarvisState,
      coreStatus: "READY" as const,
      jarvisState: "THINKING" as const,
      activeRequestId: "request-a",
    };

    const wrongRequest = jarvisReducer(active, {
      type: "server.message",
      message: message("error", { message: "wrong" }, "request-b"),
    });
    expect(wrongRequest).toEqual(active);

    const failed = jarvisReducer(active, {
      type: "server.message",
      message: message("error", { message: "reply failed" }, "request-a"),
    });
    expect(failed.error).toBe("reply failed");
    expect(failed.jarvisState).toBe("IDLE");
    expect(failed.activeRequestId).toBeNull();
  });

  it("marks the Core OFFLINE and clears an in-flight request", () => {
    const offline = jarvisReducer(
      {
        ...initialJarvisState,
        coreStatus: "READY",
        activeRequestId: "request-a",
      },
      { type: "core.offline", reason: "connection closed" },
    );

    expect(offline.coreStatus).toBe("OFFLINE");
    expect(offline.jarvisState).toBe("IDLE");
    expect(offline.activeRequestId).toBeNull();
  });
});
