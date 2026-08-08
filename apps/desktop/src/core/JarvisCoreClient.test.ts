import { describe, expect, it, vi } from "vitest";

import {
  JarvisCoreClient,
  type CoreSocket,
} from "./JarvisCoreClient";
import type { ServerMessage } from "./protocol";

class FakeSocket implements CoreSocket {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readonly sent: string[] = [];

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.onclose?.();
  }

  open(): void {
    this.onopen?.();
  }

  emit(message: ServerMessage): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

describe("JarvisCoreClient", () => {
  it("uses auth as the first WebSocket message and waits for core.ready", async () => {
    const socket = new FakeSocket();
    const onMessage = vi.fn();
    const client = new JarvisCoreClient(
      async () => ({
        status: "READY",
        port: 54321,
        token: "launch-token",
        protocolVersion: 1,
      }),
      () => socket,
      { onMessage, onOffline: vi.fn() },
    );

    const connected = client.connect();
    await vi.waitFor(() => expect(socket.onopen).not.toBeNull());
    socket.open();

    expect(socket.sent).toHaveLength(1);
    expect(JSON.parse(socket.sent[0])).toEqual({
      version: 1,
      type: "auth",
      payload: { token: "launch-token" },
    });

    let settled = false;
    void connected.then(() => {
      settled = true;
    });
    await Promise.resolve();
    expect(settled).toBe(false);

    socket.emit({
      version: 1,
      type: "core.ready",
      payload: { state: "IDLE" },
    });
    await connected;
    expect(onMessage).toHaveBeenCalledOnce();
  });

  it("preserves its generated requestId and blocks a second send", async () => {
    const socket = new FakeSocket();
    const client = new JarvisCoreClient(
      async () => ({
        status: "READY",
        port: 54321,
        token: "launch-token",
        protocolVersion: 1,
      }),
      () => socket,
      { onMessage: vi.fn(), onOffline: vi.fn() },
    );

    const connected = client.connect();
    await vi.waitFor(() => expect(socket.onopen).not.toBeNull());
    socket.open();
    socket.emit({
      version: 1,
      type: "core.ready",
      payload: { state: "IDLE" },
    });
    await connected;

    const requestId = client.sendChat("hello");
    const outbound = JSON.parse(socket.sent[1]);
    expect(outbound.requestId).toBe(requestId);
    expect(outbound.payload).toEqual({ text: "hello" });
    expect(() => client.sendChat("again")).toThrow(/in progress/i);

    socket.emit({
      version: 1,
      type: "state.changed",
      requestId,
      payload: { state: "IDLE" },
    });
    expect(() => client.sendChat("next")).not.toThrow();
  });
});
