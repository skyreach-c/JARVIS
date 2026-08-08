import { invoke } from "@tauri-apps/api/core";
import { useEffect, useMemo, useReducer, useState, type FormEvent } from "react";

import JarvisOrb from "./components/JarvisOrb";
import {
  JarvisCoreClient,
  type JarvisCoreClientHandlers,
} from "./core/JarvisCoreClient";
import { initialJarvisState, jarvisReducer } from "./state/jarvisReducer";
import "./styles/app.css";

export interface JarvisClient {
  connect(): Promise<void>;
  sendChat(text: string): string;
  disconnect(): void;
}

export type JarvisClientFactory = (
  handlers: JarvisCoreClientHandlers,
) => JarvisClient;

interface AppProps {
  createClient?: JarvisClientFactory;
  resizeWindow?: (expanded: boolean) => Promise<void>;
}

const defaultClientFactory: JarvisClientFactory = (handlers) =>
  new JarvisCoreClient(undefined, undefined, handlers);

const defaultResizeWindow = (expanded: boolean) =>
  invoke<void>("set_expanded", { expanded });

export default function App({
  createClient = defaultClientFactory,
  resizeWindow = defaultResizeWindow,
}: AppProps) {
  const [view, dispatch] = useReducer(jarvisReducer, initialJarvisState);
  const [expanded, setExpanded] = useState(false);
  const [input, setInput] = useState("");

  const client = useMemo(
    () =>
      createClient({
        onMessage: (message) =>
          dispatch({ type: "server.message", message }),
        onOffline: (reason) => dispatch({ type: "core.offline", reason }),
      }),
    [createClient],
  );

  useEffect(() => {
    let mounted = true;
    void client.connect().catch((error: unknown) => {
      if (mounted) {
        dispatch({
          type: "core.offline",
          reason: error instanceof Error ? error.message : "Core connection failed",
        });
      }
    });

    return () => {
      mounted = false;
      client.disconnect();
    };
  }, [client]);

  const busy =
    view.coreStatus !== "READY" ||
    view.jarvisState !== "IDLE" ||
    view.activeRequestId !== null;
  const canSend = !busy && input.trim().length > 0;
  const visibleStatus =
    view.coreStatus === "READY" ? view.jarvisState : view.coreStatus;

  const openPanel = () => {
    setExpanded(true);
    void resizeWindow(true).catch((error: unknown) => {
      console.error("failed to expand JARVIS window", error);
    });
  };

  const sendMessage = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSend) {
      return;
    }

    try {
      const requestId = client.sendChat(input.trim());
      dispatch({ type: "request.started", requestId });
      setInput("");
    } catch (error) {
      dispatch({
        type: "core.offline",
        reason: error instanceof Error ? error.message : "Unable to send request",
      });
    }
  };

  if (!expanded) {
    return (
      <main className="compact-shell">
        <JarvisOrb
          compact
          coreStatus={view.coreStatus}
          state={view.jarvisState}
          onOpen={openPanel}
        />
      </main>
    );
  }

  return (
    <main className="panel-shell">
      <header className="panel-header">
        <JarvisOrb
          compact={false}
          coreStatus={view.coreStatus}
          state={view.jarvisState}
          onOpen={openPanel}
        />
        <div>
          <p className="eyebrow">JARVIS / CORE 01</p>
          <h1>Local Intelligence</h1>
        </div>
        <span className="status-chip" data-state={visibleStatus}>
          {visibleStatus}
        </span>
      </header>

      <section className="response-panel" aria-live="polite">
        <span className="response-label">CORE RESPONSE</span>
        <p>{view.assistantText || "等待指令。"}</p>
        {view.error ? <small className="error-text">{view.error}</small> : null}
      </section>

      <form className="command-form" onSubmit={sendMessage}>
        <label htmlFor="jarvis-command">消息</label>
        <input
          id="jarvis-command"
          aria-label="消息"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="输入指令"
          disabled={busy}
          autoComplete="off"
        />
        <button type="submit" disabled={!canSend}>
          发送
        </button>
      </form>
    </main>
  );
}
