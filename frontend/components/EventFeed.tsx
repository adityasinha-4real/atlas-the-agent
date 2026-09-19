import type { RunEvent } from "@/lib/types";

// Per-type presentation for the feed. The answer.token stream is rendered in the
// dedicated Answer panel, so it is filtered out here to keep the feed legible.
interface Rendered {
  label: string;
  accent: string;
  detail: string;
}

function render(event: RunEvent): Rendered | null {
  const p = event.payload as Record<string, unknown>;
  switch (event.type) {
    case "answer.token":
      return null;
    case "run.created":
      return { label: "created", accent: "text-slate-400", detail: str(p.goal) };
    case "run.started":
      return { label: "started", accent: "text-slate-400", detail: "" };
    case "thought":
      return {
        label: `thought${attemptTag(p)}`,
        accent: "text-violet-400",
        detail: str(p.text),
      };
    case "tool.call":
      return {
        label: `tool ▶${attemptTag(p)}`,
        accent: "text-amber-400",
        detail: `${str(p.tool)}(${compact(p.arguments)})`,
      };
    case "tool.result":
      return {
        label: `tool ${p.ok ? "✓" : "✗"}${attemptTag(p)}`,
        accent: p.ok ? "text-emerald-400" : "text-rose-400",
        detail: str(p.observation),
      };
    case "plan.created": {
      const tasks = Array.isArray(p.tasks) ? p.tasks : [];
      return {
        label: "plan",
        accent: "text-sky-400",
        detail: `${tasks.length} task${tasks.length === 1 ? "" : "s"}`,
      };
    }
    case "task.started":
      return {
        label: `task ${Number(p.index) + 1} ▶`,
        accent: "text-amber-400",
        detail: str(p.description),
      };
    case "task.completed":
      return {
        label: `task ${Number(p.index) + 1} ✓`,
        accent: "text-emerald-400",
        detail: str(p.output),
      };
    case "task.failed":
      return {
        label: `task ${Number(p.index) + 1} ✗`,
        accent: "text-rose-400",
        detail: str(p.error),
      };
    case "task.reflected":
      return {
        label: `reflect ${str(p.decision)}`,
        accent: "text-violet-400",
        detail: str(p.reason),
      };
    case "task.retrying":
      return {
        label: `task ${Number(p.index) + 1} ↻`,
        accent: "text-amber-400",
        detail: `retry (attempt ${Number(p.attempt)}) — ${str(p.reason)}`,
      };
    case "task.skipped":
      return {
        label: `task ${Number(p.index) + 1} –`,
        accent: "text-slate-500",
        detail: `skipped — ${str(p.reason)}`,
      };
    case "task.cancelled":
      return {
        label: `task ${Number(p.index) + 1} ⊘`,
        accent: "text-amber-500",
        detail: `cancelled (${str(p.phase)})`,
      };
    case "plan.replanned":
      return {
        label: `replan gen ${Number(p.generation)}`,
        accent: "text-sky-400",
        detail: `−${Number(p.old_task_count)} +${Number(p.new_task_count)} — ${str(
          p.reason,
        )}`,
      };
    case "budget.exceeded":
      return {
        label: "budget ✗",
        accent: "text-rose-400",
        detail: `${str(p.budget)} exhausted (limit ${Number(p.limit)}, used ${Number(
          p.used,
        )})`,
      };
    case "memory.recalled": {
      const count = Number(p.count ?? 0);
      return {
        label: "recall ◆",
        accent: "text-teal-400",
        detail: `recalled ${count} lesson${count === 1 ? "" : "s"} from past runs`,
      };
    }
    case "memory.written":
      return {
        label: "memory ✎",
        accent: "text-teal-400",
        detail: `saved this run (${str(p.outcome)}, ${str(p.source)})`,
      };
    case "answer.completed":
      return { label: "answer", accent: "text-sky-400", detail: str(p.text) };
    case "run.completed":
      return { label: "completed", accent: "text-emerald-400", detail: "" };
    case "run.failed":
      return { label: "failed", accent: "text-rose-400", detail: str(p.error) };
    case "run.cancelled":
      return { label: "cancelled", accent: "text-amber-400", detail: "" };
    default:
      return { label: event.type, accent: "text-slate-400", detail: compact(p) };
  }
}

// Executor events (thought/tool.*) carry their attempt from M4; surface it in the
// label so a task's retried events read as distinct attempts (grouping cue).
function attemptTag(p: Record<string, unknown>): string {
  const attempt = Number(p.attempt ?? 0);
  return attempt > 1 ? ` a${attempt}` : "";
}

function str(value: unknown): string {
  return value == null ? "" : String(value);
}

function compact(value: unknown): string {
  if (value == null) return "";
  const json = JSON.stringify(value);
  return json.length > 160 ? `${json.slice(0, 157)}…` : json;
}

export function EventFeed({ events }: { events: RunEvent[] }) {
  const rows = events
    .map((event) => ({ event, view: render(event) }))
    .filter((r): r is { event: RunEvent; view: Rendered } => r.view !== null);

  if (rows.length === 0) {
    return (
      <p className="text-sm text-slate-500">No events yet. Submit a goal to begin.</p>
    );
  }
  return (
    <ol className="space-y-1 font-mono text-xs">
      {rows.map(({ event, view }) => (
        <li
          key={event.seq}
          className="flex items-start gap-3 rounded border border-slate-800 bg-slate-900/60 px-3 py-2"
        >
          <span className="w-8 shrink-0 text-right text-slate-500">{event.seq}</span>
          <span className={`w-24 shrink-0 font-semibold ${view.accent}`}>
            {view.label}
          </span>
          <span className="whitespace-pre-wrap break-words text-slate-300">
            {view.detail}
          </span>
        </li>
      ))}
    </ol>
  );
}
