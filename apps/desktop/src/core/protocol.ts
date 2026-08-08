export const PROTOCOL_VERSION = 1 as const;

export type JarvisState = "IDLE" | "THINKING" | "RESPONDING";
export type CoreStatus = "STARTING" | "READY" | "OFFLINE";

export type ServerMessageType =
  | "core.ready"
  | "state.changed"
  | "chat.delta"
  | "chat.completed"
  | "error";

export interface ServerMessage {
  version: number;
  type: ServerMessageType;
  requestId?: string;
  payload: Record<string, unknown>;
}

export interface CoreConnectionInfo {
  status: "READY";
  port: number;
  token: string;
  protocolVersion: number;
}

export interface ClientMessage {
  version: typeof PROTOCOL_VERSION;
  type: "auth" | "chat.send";
  requestId?: string;
  payload: Record<string, unknown>;
}

export function authMessage(token: string): ClientMessage {
  return {
    version: PROTOCOL_VERSION,
    type: "auth",
    payload: { token },
  };
}

export function chatMessage(requestId: string, text: string): ClientMessage {
  return {
    version: PROTOCOL_VERSION,
    type: "chat.send",
    requestId,
    payload: { text },
  };
}

export function isServerMessage(value: unknown): value is ServerMessage {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Partial<ServerMessage>;
  return (
    candidate.version === PROTOCOL_VERSION &&
    typeof candidate.type === "string" &&
    typeof candidate.payload === "object" &&
    candidate.payload !== null
  );
}
