// Typed client for the ATLAS backend. REST calls go through an openapi-fetch
// client bound to the generated schema (lib/generated/schema.ts), so paths,
// query params, and response shapes are checked against the real backend
// contract — no hand-maintained URL strings or response casts. The WebSocket
// streaming helpers below stay hand-written: OpenAPI does not model WebSockets,
// and the bespoke reconnect/backfill logic in useRunStream is clearer than any
// generated substitute.

import createClient from "openapi-fetch";

import type { paths } from "./generated/schema";
import type { MemoryView, RunEvent, RunSummary, RunView } from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ?? "http://localhost:8000";

const client = createClient<paths>({ baseUrl: API_BASE });

export function wsBase(): string {
  return API_BASE.replace(/^http/, "ws");
}

// Collapse an openapi-fetch result to its data, throwing on any non-2xx exactly
// as the previous hand-written client did (components catch and display these).
function unwrap<T>(res: { data?: T; error?: unknown; response: Response }): T {
  if (!res.response.ok) {
    const detail =
      res.error == null
        ? ""
        : typeof res.error === "string"
          ? res.error
          : JSON.stringify(res.error);
    throw new Error(`HTTP ${res.response.status}${detail ? `: ${detail}` : ""}`);
  }
  return res.data as T;
}

export async function createRun(goal: string): Promise<RunView> {
  return unwrap(await client.POST("/runs", { body: { goal } }));
}

export async function getRun(runId: string): Promise<RunView> {
  return unwrap(
    await client.GET("/runs/{run_id}", { params: { path: { run_id: runId } } }),
  );
}

export async function listRuns(limit = 50): Promise<RunSummary[]> {
  return unwrap(await client.GET("/runs", { params: { query: { limit } } }));
}

export async function getEvents(runId: string, after = 0): Promise<RunEvent[]> {
  const events = unwrap(
    await client.GET("/runs/{run_id}/events", {
      params: { path: { run_id: runId }, query: { after } },
    }),
  );
  // The wire `Event` marks payload/ts optional (server defaults); RunEvent pins
  // them present. The server always sends them, so the narrowing is safe.
  return events as RunEvent[];
}

export async function cancelRun(runId: string): Promise<void> {
  await client.POST("/runs/{run_id}/cancel", {
    params: { path: { run_id: runId } },
  });
}

export function streamUrl(runId: string, after = 0): string {
  return `${wsBase()}/runs/${runId}/stream?after=${after}`;
}

// --- Episodic memory (M5) --------------------------------------------------

export async function listMemories(
  opts: { q?: string; limit?: number; offset?: number } = {},
): Promise<MemoryView[]> {
  const query: { q?: string; limit?: number; offset?: number } = {};
  if (opts.q) query.q = opts.q;
  if (opts.limit != null) query.limit = opts.limit;
  if (opts.offset != null) query.offset = opts.offset;
  return unwrap(await client.GET("/memories", { params: { query } })) as MemoryView[];
}

export async function deleteMemory(id: string): Promise<void> {
  const { response } = await client.DELETE("/memories/{memory_id}", {
    params: { path: { memory_id: id } },
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
}

export async function setMemoryPinned(
  id: string,
  pinned: boolean,
): Promise<MemoryView> {
  const path = { memory_id: id };
  const res = pinned
    ? await client.POST("/memories/{memory_id}/pin", { params: { path } })
    : await client.POST("/memories/{memory_id}/unpin", { params: { path } });
  return unwrap(res) as MemoryView;
}
