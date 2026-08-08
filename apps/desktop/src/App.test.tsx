import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import App, {
  type JarvisClient,
  type JarvisClientFactory,
} from "./App";
import type { JarvisCoreClientHandlers } from "./core/JarvisCoreClient";

describe("App", () => {
  it("expands the Orb and renders one correlated heartbeat", async () => {
    let handlers: JarvisCoreClientHandlers | undefined;
    const client: JarvisClient = {
      connect: vi.fn(async () => undefined),
      sendChat: vi.fn(() => "request-a"),
      disconnect: vi.fn(),
    };
    const createClient: JarvisClientFactory = (nextHandlers) => {
      handlers = nextHandlers;
      return client;
    };
    const resizeWindow = vi.fn(async () => undefined);

    render(<App createClient={createClient} resizeWindow={resizeWindow} />);
    await waitFor(() => expect(client.connect).toHaveBeenCalledOnce());

    act(() => {
      handlers?.onMessage({
        version: 1,
        type: "core.ready",
        payload: { state: "IDLE" },
      });
    });
    expect(screen.getByText("IDLE")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "打开 JARVIS" }));
    expect(resizeWindow).toHaveBeenCalledWith(true);

    const input = screen.getByRole("textbox", { name: "消息" });
    fireEvent.change(input, { target: { value: "你好" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(client.sendChat).toHaveBeenCalledWith("你好");
    expect(input).toBeDisabled();

    act(() => {
      handlers?.onMessage({
        version: 1,
        type: "state.changed",
        requestId: "request-a",
        payload: { state: "THINKING" },
      });
    });
    expect(screen.getByText("THINKING")).toBeInTheDocument();

    act(() => {
      handlers?.onMessage({
        version: 1,
        type: "state.changed",
        requestId: "request-a",
        payload: { state: "RESPONDING" },
      });
      handlers?.onMessage({
        version: 1,
        type: "chat.delta",
        requestId: "request-a",
        payload: { text: "晚上好。" },
      });
      handlers?.onMessage({
        version: 1,
        type: "chat.delta",
        requestId: "request-a",
        payload: { text: "JARVIS Core 已上线。" },
      });
    });
    expect(screen.getByText("RESPONDING")).toBeInTheDocument();
    expect(screen.getByText("晚上好。JARVIS Core 已上线。")).toBeInTheDocument();

    act(() => {
      handlers?.onMessage({
        version: 1,
        type: "chat.completed",
        requestId: "request-a",
        payload: { text: "晚上好。JARVIS Core 已上线。" },
      });
      handlers?.onMessage({
        version: 1,
        type: "state.changed",
        requestId: "request-a",
        payload: { state: "IDLE" },
      });
    });
    expect(screen.getByText("IDLE")).toBeInTheDocument();
    expect(input).not.toBeDisabled();
  });
});
