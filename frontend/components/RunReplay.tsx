"use client";

// Replay a finished run from its event ledger. It fetches the full event stream
// (GET /runs/{id}/events) once and folds it through the SAME reducer the live
// page uses, so the reconstruction is identical to what streamed originally
// (ADR-0009). A scrubber steps the fold cursor through the events, turning the
// static ledger back into the play-by-play — plan, tool calls, retries, answer.

import { useEffect, useMemo, useRef, useState } from "react";

import { getEvents, getRun } from "@/lib/api";
import { reduceEvents } from "@/lib/runReducer";
import type { RunEvent, RunView } from "@/lib/types";
import { EventFeed } from "./EventFeed";
import { PlanChecklist } from "./PlanChecklist";
import { StatusBadge } from "./StatusBadge";

export function RunReplay({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunView | null>(null);
  const [events, setEvents] = useState<RunEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0); // events applied so far
  const [playing, setPlaying] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getRun(runId), getEvents(runId)])
      .then(([r, evs]) => {
        if (cancelled) return;
        setRun(r);
        setEvents(evs);
        setCursor(evs.length); // start fully replayed
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const total = events?.length ?? 0;

  // Advance the cursor while playing; stop at the end.
  useEffect(() => {
    if (!playing || total === 0) return;
    timer.current = setInterval(() => {
      setCursor((c) => {
        if (c >= total) {
          setPlaying(false);
          return c;
        }
        return c + 1;
      });
    }, 350);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [playing, total]);

  const shown = useMemo(
    () => (events ? events.slice(0, cursor) : []),
    [events, cursor],
  );
  const derived = useMemo(() => reduceEvents(shown), [shown]);
  const atEnd = cursor >= total;

  function play() {
    if (atEnd) setCursor(0); // replaying from the top
    setPlaying(true);
  }

  if (error) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-10">
        <ReplayHeader />
        <p
          role="alert"
          className="rounded-lg border border-rose-800 bg-rose-950/40 px-4 py-3 text-sm text-rose-300"
        >
          Could not load run {runId.slice(0, 8)}: {error}
        </p>
      </main>
    );
  }

  if (!run || !events) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-10">
        <ReplayHeader />
        <p className="text-sm text-slate-500">Loading…</p>
      </main>
    );
  }

  // Authoritative status/answer come from the run row; the fold drives the
  // step-by-step reconstruction. While scrubbing, show the folded status.
  const status = atEnd ? run.status : derived.status;
  const answer = atEnd ? run.answer ?? derived.answer : derived.answer;

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <ReplayHeader goal={run.goal} />

      <section className="space-y-6">
        <div className="flex items-center justify-between" aria-live="polite">
          <StatusBadge status={status} />
          <span className="font-mono text-xs text-slate-500">
            run {run.id.slice(0, 8)}
          </span>
        </div>

        {/* Replay controls */}
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3">
          <div className="mb-2 flex items-center gap-2">
            <button
              type="button"
              onClick={() => (playing ? setPlaying(false) : play())}
              className="rounded-lg bg-sky-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-sky-500"
            >
              {playing ? "⏸ Pause" : atEnd ? "↻ Replay" : "▶ Play"}
            </button>
            <button
              type="button"
              onClick={() => {
                setPlaying(false);
                setCursor((c) => Math.max(0, c - 1));
              }}
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-800"
            >
              ◀ Step
            </button>
            <button
              type="button"
              onClick={() => {
                setPlaying(false);
                setCursor((c) => Math.min(total, c + 1));
              }}
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-800"
            >
              Step ▶
            </button>
            <span className="ml-auto font-mono text-xs text-slate-500">
              {cursor}/{total}
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={total}
            value={cursor}
            onChange={(e) => {
              setPlaying(false);
              setCursor(Number(e.target.value));
            }}
            className="w-full accent-sky-500"
            aria-label="Replay position"
          />
        </div>

        {derived.recalled && derived.recalled.count > 0 && (
          <div className="rounded-lg border border-teal-800 bg-teal-950/30 px-4 py-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-teal-300">
              ◆ Recalled {derived.recalled.count} lesson
              {derived.recalled.count === 1 ? "" : "s"} from past runs
            </p>
            <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-xs text-teal-100/90">
              {derived.recalled.rendered}
            </pre>
          </div>
        )}

        <div>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
            Plan
          </h2>
          <PlanChecklist tasks={derived.tasks} />
        </div>

        {derived.budgetExceeded && (
          <div className="rounded-lg border border-rose-800 bg-rose-950/40 px-4 py-2 text-sm text-rose-300">
            Budget exhausted: {derived.budgetExceeded.budget} (limit{" "}
            {derived.budgetExceeded.limit}, used {derived.budgetExceeded.used}). The
            run was stopped.
          </div>
        )}

        <div>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
            Answer
          </h2>
          {derived.partial && (
            <p className="mb-2 rounded border border-amber-800 bg-amber-950/40 px-3 py-1.5 text-xs text-amber-300">
              ⚠️ Partial answer — the run did not fully complete; this is based on
              the tasks that finished.
            </p>
          )}
          <div className="min-h-[3rem] whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-900/60 p-4 text-slate-100">
            {answer || <span className="text-slate-600">No answer.</span>}
          </div>
          {derived.error && (
            <p role="alert" className="mt-2 text-sm text-rose-400">
              Error: {derived.error}
            </p>
          )}
          {derived.memoryWritten && (
            <p className="mt-2 text-xs text-teal-400">
              ✎ Saved to memory ({derived.memoryWritten.outcome},{" "}
              {derived.memoryWritten.source}) —{" "}
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
          <EventFeed events={derived.events} />
        </div>
      </section>
    </main>
  );
}

function ReplayHeader({ goal }: { goal?: string }) {
  return (
    <header className="mb-8 flex items-start justify-between">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Replay</h1>
        {goal && <p className="mt-1 text-sm text-slate-300">{goal}</p>}
      </div>
      <nav className="mt-1 flex gap-2">
        <a
          href="/history"
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
        >
          ← History
        </a>
        <a
          href="/"
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
        >
          ← Run
        </a>
      </nav>
    </header>
  );
}
