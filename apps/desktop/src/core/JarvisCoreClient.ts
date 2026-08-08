import { invoke } from "@tauri-apps/api/core";

import {
  PROTOCOL_VERSION,
  authMessage,
  chatMessage,
  isServerMessage,
  type CoreConnectionInfo,
  type ServerMessage,
} from "./protocol";

export interface CoreSocket {
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  send(data: string): void;
  close(): void;
}

export interface JarvisCoreClientHandlers {
  onMessage(message: ServerMessage): void;
  onOffline(reason: string): void;
}

export type ConnectionLoader = () => Promise<CoreConnectionInfo>;
export type SocketFactory = (url: string) => CoreSocket;

const loadConnection: ConnectionLoader = () =>
  invoke<CoreConnectionInfo>("get_core_connection");

const createSocket: SocketFactory = (url) =>
  new WebSocket(url) as unknown as CoreSocket;

export class JarvisCoreClient {
  private socket: CoreSocket | null = null;
  private ready = false;
  private activeRequestId: string | null = null;
  private resolveConnection: (() => void) | null = null;
  private rejectConnection: ((reason: Error) => void) | null = null;
  private offlineNotified = false;

  constructor(
    private readonly getConnection: ConnectionLoader = loadConnection,
    private readonly socketFactory: SocketFactory = createSocket,
    private readonly handlers: JarvisCoreClientHandlers = {
      onMessage: () => undefined,
      onOffline: () => undefined,
    },
  ) {}

  async connect(): Promise<void> {
    const connection = await this.getConnection();
    if (
      connection.status !== "READY" ||
      connection.protocolVersion !== PROTOCOL_VERSION
    ) {
      throw new Error("incompatible Python Core lifecycle protocol");
    }

    const socket = this.socketFactory(`ws://127.0.0.1:${connection.port}`);
    this.socket = socket;
    this.offlineNotified = false;

    return new Promise<void>((resolve, reject) => {
      this.resolveConnection = resolve;
      this.rejectConnection = reject;

      socket.onopen = () => {
        socket.send(JSON.stringify(authMessage(connection.token)));
      };
      socket.onmessage = (event) => this.handleMessage(event.data);
      socket.onerror = () => this.markOffline("Core WebSocket error");
      socket.onclose = () => this.markOffline("Core connection closed");
    });
  }

  sendChat(text: string): string {
    if (!this.ready || this.socket === null) {
      throw new Error("Python Core is not ready");
    }
    if (this.activeRequestId !== null) {
      throw new Error("a JARVIS request is already in progress");
    }
    if (!text.trim()) {
      throw new Error("message text is required");
    }

    const requestId = crypto.randomUUID();
    this.activeRequestId = requestId;
    this.socket.send(JSON.stringify(chatMessage(requestId, text)));
    return requestId;
  }

  disconnect(): void {
    const socket = this.socket;
    this.socket = null;
    this.ready = false;
    this.activeRequestId = null;
    socket?.close();
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== "string") {
      this.markOffline("Core sent a non-text message");
      return;
    }

    let decoded: unknown;
    try {
      decoded = JSON.parse(raw);
    } catch {
      this.markOffline("Core sent invalid JSON");
      return;
    }

    if (!isServerMessage(decoded)) {
      this.markOffline("Core sent an incompatible message");
      return;
    }

    if (decoded.type === "core.ready") {
      if (decoded.payload.state !== "IDLE" || decoded.requestId !== undefined) {
        this.markOffline("Core sent an invalid core.ready message");
        return;
      }
      this.ready = true;
      this.handlers.onMessage(decoded);
      this.resolveConnection?.();
      this.resolveConnection = null;
      this.rejectConnection = null;
      return;
    }

    if (!this.ready) {
      this.markOffline("Core event arrived before core.ready");
      return;
    }

    if (
      decoded.requestId === this.activeRequestId &&
      (decoded.type === "error" ||
        (decoded.type === "state.changed" && decoded.payload.state === "IDLE"))
    ) {
      this.activeRequestId = null;
    }
    this.handlers.onMessage(decoded);
  }

  private markOffline(reason: string): void {
    this.ready = false;
    this.activeRequestId = null;
    this.rejectConnection?.(new Error(reason));
    this.resolveConnection = null;
    this.rejectConnection = null;
    if (!this.offlineNotified) {
      this.offlineNotified = true;
      this.handlers.onOffline(reason);
    }
  }
}
