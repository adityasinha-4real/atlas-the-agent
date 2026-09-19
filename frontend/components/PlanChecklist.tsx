import type { PlanTask, TaskStatus } from "@/lib/types";

// The plan checklist (design doc §1.7). It is reconstructed purely from the
// event stream — plan.created seeds the tasks, task.* events transition them —
// so it replays for free from the ledger (ADR-0009). No polling, no separate
// source of truth.

interface Glyph {
  icon: string;
  accent: string;
  label: string;
}

const GLYPHS: Record<TaskStatus, Glyph> = {
  pending: { icon: "○", accent: "text-slate-500", label: "pending" },
  running: { icon: "◐", accent: "text-sky-400 animate-pulse", label: "running" },
  retrying: { icon: "↻", accent: "text-amber-400 animate-pulse", label: "retrying" },
  done: { icon: "✓", accent: "text-emerald-400", label: "done" },
  failed: { icon: "✗", accent: "text-rose-400", label: "failed" },
  skipped: { icon: "–", accent: "text-slate-600", label: "skipped" },
  cancelled: { icon: "⊘", accent: "text-amber-500", label: "cancelled" },
};

// Small colored tag for a reflection verdict (M4).
const VERDICT_ACCENT: Record<string, string> = {
  accept: "text-emerald-400",
  retry: "text-amber-400",
  replan: "text-sky-400",
  abort: "text-rose-400",
};

export function PlanChecklist({ tasks }: { tasks: PlanTask[] }) {
  if (tasks.length === 0) {
    return (
      <p className="text-sm text-slate-500">
        Planning… the task list will appear here.
      </p>
    );
  }

  return (
    <ol className="space-y-2">
      {tasks.map((task) => {
        const glyph = GLYPHS[task.status] ?? GLYPHS.pending;
        return (
          <li
            key={task.id}
            className="flex items-start gap-3 rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3"
          >
            <span
              className={`mt-0.5 w-4 shrink-0 text-center font-semibold ${glyph.accent}`}
              title={glyph.label}
              aria-label={glyph.label}
            >
              {glyph.icon}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-slate-200">
                <span className="mr-2 text-slate-500">{task.index + 1}.</span>
                {task.description}
                {task.attempt && task.attempt > 1 && (
                  <span className="ml-2 text-xs text-amber-400">
                    attempt {task.attempt}
                  </span>
                )}
                {task.generation != null && task.generation > 0 && (
                  <span className="ml-2 text-xs text-sky-400">
                    gen {task.generation}
                  </span>
                )}
              </p>
              {task.reflection && task.reflection.decision !== "accept" && (
                <p className="mt-1 text-xs">
                  <span
                    className={
                      VERDICT_ACCENT[task.reflection.decision] ?? "text-slate-400"
                    }
                  >
                    {task.reflection.decision}
                  </span>
                  {task.reflection.reason && (
                    <span className="text-slate-500"> — {task.reflection.reason}</span>
                  )}
                </p>
              )}
              {task.status === "failed" && task.error && (
                <p className="mt-1 text-xs text-rose-400">{task.error}</p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
