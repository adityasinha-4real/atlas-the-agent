"use client";

// The Run page: goal input → plan checklist → live event feed → streaming
// answer. As of M3 the run plans a task list first; the checklist ticks each
// task pending → running → done as the events arrive.

import { FormEvent, useState } from "react";

import { cancelRun, createRun } from "@/lib/api";
import { TERMINAL_STATUSES } from "@/lib/types";
import { useRunStream } from "@/lib/useRunStream";
import { EventFeed } from "./EventFeed";
import { PlanChecklist } from "./PlanChecklist";
import { StatusBadge } from "./StatusBadge";

export function RunPage() {
  const [goal, setGoal] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const stream = useRunStream(runId);
  const isActive = runId !== null && !TERMINAL_STATUSES.includes(stream.status);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!goal.trim() || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const run = await createRun(goal.trim());
      setRunId(run.id);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold text-slate-100">ATLAS</h1>
          <p className="mt-1 text-sm text-slate-400">
            Agentic AI runtime — plan, act, reflect, recover.
          </p>
        </div>
        <nav className="mt-1 flex gap-2">
          <a
            href="/history"
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
          >
            History →
          </a>
          <a
            href="/memory"
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
          >
            Memory →
          </a>
        </nav>
      </header>

      <form onSubmit={onSubmit} className="mb-8">
        <label htmlFor="goal" className="mb-2 block text-sm font-medium text-slate-300">
          Goal
        </label>
        <div className="flex gap-2">
          <input
            id="goal"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="e.g. Compare the populations of France and Germany."
            className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-4 py-2 text-slate-100 outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-500/40"
          />
          <button
            type="submit"
            disabled={submitting || isActive}
            className="rounded-lg bg-sky-600 px-5 py-2 font-semibold text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? "Starting…" : "Run"}
          </button>
          {isActive && (
            <button
              type="button"
              onClick={() => runId && cancelRun(runId)}
              className="rounded-lg border border-slate-600 px-4 py-2 font-medium text-slate-200 hover:bg-slate-800"
            >
              Cancel
            </button>
          )}
        </div>
        {submitError && (
          <p role="alert" className="mt-2 text-sm text-rose-400">
            {submitError}
          </p>
        )}
      </form>

      {!runId && (
        <p className="text-sm text-slate-500">
          Enter a goal above and press Run to start an agent run.
        </p>
      )}

      {runId && (
        <section className="space-y-6">
          <div
            className="flex items-center justify-between"
            aria-live="polite"
          >
            <StatusBadge status={stream.status} />
            <span className="text-xs text-slate-500">
              {stream.connected ? "● live" : "○ disconnected"} · run {runId.slice(0, 8)}
            </span>
          </div>

          {stream.recalled && stream.recalled.count > 0 && (
            <div className="rounded-lg border border-teal-800 bg-teal-950/30 px-4 py-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-teal-300">
                ◆ Recalled {stream.recalled.count} lesson
                {stream.recalled.count === 1 ? "" : "s"} from past runs
              </p>
              <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-xs text-teal-100/90">
                {stream.recalled.rendered}
              </pre>
            </div>
          )}

          <div>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
              Plan
            </h2>
            <PlanChecklist tasks={stream.tasks} />
          </div>

          {stream.budgetExceeded && (
            <div className="rounded-lg border border-rose-800 bg-rose-950/40 px-4 py-2 text-sm text-rose-300">
              Budget exhausted: {stream.budgetExceeded.budget} (limit{" "}
              {stream.budgetExceeded.limit}, used {stream.budgetExceeded.used}). The
              run was stopped.
            </div>
          )}

          <div>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
              Answer
            </h2>
            {stream.partial && (
              <p className="mb-2 rounded border border-amber-800 bg-amber-950/40 px-3 py-1.5 text-xs text-amber-300">
                ⚠️ Partial answer — the run did not fully complete; this is based on
                the tasks that finished.
              </p>
            )}
            <div className="min-h-[3rem] whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-900/60 p-4 text-slate-100">
              {stream.answer || (
                <span className="text-slate-600">Waiting for the model…</span>
              )}
            </div>
            {stream.error && (
              <p role="alert" className="mt-2 text-sm text-rose-400">
                Error: {stream.error}
              </p>
            )}
            {stream.memoryWritten && (
              <p className="mt-2 text-xs text-teal-400">
                ✎ Saved to memory ({stream.memoryWritten.outcome},{" "}
                {stream.memoryWritten.source}) —{" "}
                <a href="/memory" className="underline hover:text-teal-300">
                  browse memory
                </a>
              </p>
            )}
          </div>

          <div>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
              Event feed
            </h2>
            <EventFeed events={stream.events} />
          </div>
        </section>
      )}
    </main>
  );
}
