"use client";

// React hook that drives a single run: it opens the WebSocket stream, collects
// events, reconstructs the streaming answer, and tracks status. On disconnect it
// reconnects using an `after=<lastSeq>` cursor so no events are missed — the same
// backfill mechanism that powers replay (design doc §1.7). The event -> state
// transition lives in the shared `reduceEvent` reducer, so the live page and the
// History replay reconstruct a run identically.

import { useCallback, useEffect, useRef, useState } from "react";

import { streamUrl } from "./api";
import {
  INITIAL_DERIVED,
  reduceEvent,
  RunDerived,
  TERMINAL_EVENT_TYPES,
} from "./runReducer";
import { RunEvent } from "./types";

// The live state is the derived run state plus a transport-only connection flag.
type RunStreamState = RunDerived & { connected: boolean };

const INITIAL: RunStreamState = { ...INITIAL_DERIVED, connected: false };

export function useRunStream(runId: string | null): RunStreamState {
  const [state, setState] = useState<RunStreamState>(INITIAL);
  const lastSeqRef = useRef(0);
  const closedRef = useRef(false);

  const applyEvent = useCallback((event: RunEvent) => {
    lastSeqRef.current = Math.max(lastSeqRef.current, event.seq);
    setState((prev) => ({ ...reduceEvent(prev, event), connected: prev.connected }));
  }, []);

  useEffect(() => {
    if (!runId) return;

    setState(INITIAL);
    lastSeqRef.current = 0;
    closedRef.current = false;
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      ws = new WebSocket(streamUrl(runId, lastSeqRef.current));

      ws.onopen = () => setState((p) => ({ ...p, connected: true }));

      ws.onmessage = (msg) => {
        const event = JSON.parse(msg.data) as RunEvent;
        applyEvent(event);
        if (TERMINAL_EVENT_TYPES.includes(event.type)) {
          closedRef.current = true;
        }
      };

      ws.onclose = () => {
        setState((p) => ({ ...p, connected: false }));
        if (!closedRef.current) {
          // Unexpected drop: reconnect from the last seen seq.
          retry = setTimeout(connect, 750);
        }
      };

      ws.onerror = () => ws?.close();
    };

    connect();
    return () => {
      closedRef.current = true;
      if (retry) clearTimeout(retry);
      ws?.close();
    };
  }, [runId, applyEvent]);

  return state;
}
