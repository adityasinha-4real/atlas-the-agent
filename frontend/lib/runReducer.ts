// Pure event -> run-state reducer, shared by the live stream (useRunStream) and
// the static History replay (RunReplay). The backend event ledger is the single
// source of truth (ADR-0009); folding it here is deterministic, so replaying a
// finished run reconstructs exactly what the live page showed. Keeping one
// reducer means live and replay can never drift.

import {
  PlanTask,
  RunEvent,
  RunStatus,
  TaskStatus,
  TERMINAL_EVENT_TYPES,
} from "./types";

// Everything derivable from a run's event stream. The live hook layers a
// transport-only `connected` flag on top of this; replay uses it as-is.
export interface RunDerived {
  status: RunStatus;
  events: RunEvent[];
  tasks: PlanTask[];
  answer: string;
  error: string | null;
  // M4: the answer is a best-effort partial (graceful abort) rather than a full
  // result; and a hard budget was exhausted (surfaced as a banner).
  partial: boolean;
  budgetExceeded: { budget: string; limit: number; used: number } | null;
  // M5: lessons recalled from past runs at plan time, and whether this run was
  // itself distilled into a memory.
  recalled: { count: number; ids: string[]; rendered: string } | null;
  memoryWritten: { id: string; outcome: string; source: string } | null;
}

export const INITIAL_DERIVED: RunDerived = {
  status: "created",
  events: [],
  tasks: [],
  answer: "",
  error: null,
  partial: false,
  budgetExceeded: null,
  recalled: null,
  memoryWritten: null,
};

// Append a replan generation's tasks to the checklist without disturbing the
// completed ones; the dropped tasks are marked skipped by the same event.
function applyReplan(tasks: PlanTask[], payload: Record<string, unknown>): PlanTask[] {
  const dropped = new Set(
    (payload.dropped_task_ids as unknown[] | undefined)?.map(String) ?? [],
  );
  const generation = Number(payload.generation ?? 0);
  const added = ((payload.tasks as Record<string, unknown>[] | undefined) ?? []).map(
    (t) => ({
      id: String(t.id),
      index: Number(t.index),
      description: String(t.description ?? ""),
      status: "pending" as TaskStatus,
      generation,
    }),
  );
  const kept = tasks.map((t) =>
    dropped.has(t.id) ? { ...t, status: "skipped" as TaskStatus } : t,
  );
  return [...kept, ...added];
}

function patchTask(
  tasks: PlanTask[],
  taskId: unknown,
  patch: Partial<PlanTask>,
): PlanTask[] {
  const id = String(taskId ?? "");
  return tasks.map((t) => (t.id === id ? { ...t, ...patch } : t));
}

// Fold a single event into the derived state. Pure: (prev, event) -> next.
export function reduceEvent(prev: RunDerived, event: RunEvent): RunDerived {
  const next: RunDerived = { ...prev, events: [...prev.events, event] };
  switch (event.type) {
    case "run.started":
      // A run starts by planning; it moves to "running" once tasks begin.
      next.status = "planning";
      break;
    case "plan.created": {
      const planned = (event.payload.tasks as Record<string, unknown>[]) ?? [];
      next.tasks = planned.map((t) => ({
        id: String(t.id),
        index: Number(t.index),
        description: String(t.description ?? ""),
        status: "pending" as TaskStatus,
      }));
      break;
    }
    case "task.started":
      next.status = "running";
      next.tasks = patchTask(prev.tasks, event.payload.task_id, {
        status: "running",
        attempt: Number(event.payload.attempt ?? 1),
      });
      break;
    case "task.completed":
      next.tasks = patchTask(prev.tasks, event.payload.task_id, {
        status: "done",
        output: String(event.payload.output ?? ""),
        attempt: Number(event.payload.attempt ?? 1),
      });
      break;
    case "task.failed":
      next.tasks = patchTask(prev.tasks, event.payload.task_id, {
        status: "failed",
        error: String(event.payload.error ?? ""),
      });
      break;
    case "task.reflected":
      next.tasks = patchTask(prev.tasks, event.payload.task_id, {
        reflection: {
          decision: String(event.payload.decision ?? ""),
          reason: String(event.payload.reason ?? ""),
          attempt: Number(event.payload.attempt ?? 1),
        },
      });
      break;
    case "task.retrying":
      next.tasks = patchTask(prev.tasks, event.payload.task_id, {
        status: "retrying",
        attempt: Number(event.payload.attempt ?? 1),
      });
      break;
    case "task.skipped":
      next.tasks = patchTask(prev.tasks, event.payload.task_id, {
        status: "skipped",
      });
      break;
    case "task.cancelled":
      next.tasks = patchTask(prev.tasks, event.payload.task_id, {
        status: "cancelled",
      });
      break;
    case "plan.replanned":
      next.tasks = applyReplan(prev.tasks, event.payload);
      break;
    case "budget.exceeded":
      next.budgetExceeded = {
        budget: String(event.payload.budget ?? ""),
        limit: Number(event.payload.limit ?? 0),
        used: Number(event.payload.used ?? 0),
      };
      break;
    case "memory.recalled":
      next.recalled = {
        count: Number(event.payload.count ?? 0),
        ids:
          (event.payload.memory_ids as unknown[] | undefined)?.map(String) ?? [],
        rendered: String(event.payload.rendered ?? ""),
      };
      break;
    case "memory.written":
      next.memoryWritten = {
        id: String(event.payload.memory_id ?? ""),
        outcome: String(event.payload.outcome ?? ""),
        source: String(event.payload.source ?? ""),
      };
      break;
    case "answer.token":
      next.answer = prev.answer + String(event.payload.text ?? "");
      break;
    case "answer.completed":
      if (typeof event.payload.text === "string") {
        next.answer = event.payload.text;
      }
      break;
    case "run.completed":
      next.status = "done";
      break;
    case "run.failed":
      next.status = "failed";
      next.error = String(event.payload.error ?? "unknown error");
      // A FAILED run that already produced an answer is a graceful-abort partial.
      next.partial = next.answer.trim().length > 0;
      break;
    case "run.cancelled":
      next.status = "cancelled";
      break;
  }
  return next;
}

// Fold an ordered event slice into a single derived state — the replay path.
export function reduceEvents(events: RunEvent[]): RunDerived {
  return events.reduce(reduceEvent, INITIAL_DERIVED);
}

export { TERMINAL_EVENT_TYPES };
