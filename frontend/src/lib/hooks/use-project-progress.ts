"use client";

import { useEffect, useReducer } from "react";
import { streamPaths } from "@/lib/api/endpoints";
import { parseJSON, subscribeEventStream } from "@/lib/api/sse";
import type { JobOut, ProgressEvent } from "@/lib/api/types";
import { applyProgressEvent, initialProgress, type ConnectionState, type ProgressState } from "@/lib/progress";

type Action = { type: "event"; event: ProgressEvent } | { type: "connection"; connection: ConnectionState };

function reducer(state: ProgressState, action: Action): ProgressState {
  if (action.type === "event") return applyProgressEvent(state, action.event);
  if (state.outcome !== "running" && action.connection !== "live") return state;
  return { ...state, connection: action.connection };
}

/** Live progress over SSE (`GET /projects/{id}/events`), with Last-Event-ID resume and backoff reconnect. */
export function useProjectProgress(projectId: string, job: JobOut | null): ProgressState {
  const [state, dispatch] = useReducer(reducer, job, initialProgress);
  const terminal = job?.status === "succeeded" || job?.status === "failed";

  useEffect(() => {
    if (terminal) return;
    const sub = subscribeEventStream({
      path: streamPaths.projectEvents(projectId),
      onOpen: () => dispatch({ type: "connection", connection: "live" }),
      onError: (_error, willRetry) => dispatch({ type: "connection", connection: willRetry ? "reconnecting" : "lost" }),
      onMessage: (msg) => {
        const event = parseJSON<ProgressEvent>(msg.data);
        if (!event) return;
        const typed: ProgressEvent = { ...event, type: (event.type ?? msg.event) as ProgressEvent["type"] };
        dispatch({ type: "event", event: typed });
        return typed.type === "ready" || typed.type === "failed" ? "stop" : undefined;
      },
    });
    return () => sub.close();
  }, [projectId, terminal]);

  return state;
}
