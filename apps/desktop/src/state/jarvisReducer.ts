import type {
  CoreStatus,
  JarvisState,
  ServerMessage,
} from "../core/protocol";

export interface JarvisViewState {
  coreStatus: CoreStatus;
  jarvisState: JarvisState;
  activeRequestId: string | null;
  assistantText: string;
  error: string | null;
}

export const initialJarvisState: JarvisViewState = {
  coreStatus: "STARTING",
  jarvisState: "IDLE",
  activeRequestId: null,
  assistantText: "",
  error: null,
};

export type JarvisAction =
  | { type: "server.message"; message: ServerMessage }
  | { type: "request.started"; requestId: string }
  | { type: "core.offline"; reason: string };

function isJarvisState(value: unknown): value is JarvisState {
  return value === "IDLE" || value === "THINKING" || value === "RESPONDING";
}

export function jarvisReducer(
  state: JarvisViewState,
  action: JarvisAction,
): JarvisViewState {
  if (action.type === "core.offline") {
    return {
      ...state,
      coreStatus: "OFFLINE",
      jarvisState: "IDLE",
      activeRequestId: null,
      error: action.reason,
    };
  }

  if (action.type === "request.started") {
    if (
      state.coreStatus !== "READY" ||
      state.jarvisState !== "IDLE" ||
      state.activeRequestId !== null
    ) {
      return state;
    }
    return {
      ...state,
      activeRequestId: action.requestId,
      assistantText: "",
      error: null,
    };
  }

  const message = action.message;
  if (message.type === "core.ready") {
    return {
      ...state,
      coreStatus: "READY",
      jarvisState: "IDLE",
      error: null,
    };
  }

  if (
    message.requestId === undefined ||
    message.requestId !== state.activeRequestId
  ) {
    return state;
  }

  if (message.type === "state.changed") {
    const nextState = message.payload.state;
    if (!isJarvisState(nextState)) {
      return state;
    }
    return {
      ...state,
      jarvisState: nextState,
      activeRequestId:
        nextState === "IDLE" ? null : state.activeRequestId,
    };
  }

  if (message.type === "chat.delta") {
    const text = message.payload.text;
    return typeof text === "string"
      ? { ...state, assistantText: state.assistantText + text }
      : state;
  }

  if (message.type === "chat.completed") {
    const text = message.payload.text;
    return typeof text === "string" ? { ...state, assistantText: text } : state;
  }

  if (message.type === "error") {
    const text = message.payload.message;
    return {
      ...state,
      jarvisState: "IDLE",
      activeRequestId: null,
      error: typeof text === "string" ? text : "Core request failed",
    };
  }

  return state;
}
