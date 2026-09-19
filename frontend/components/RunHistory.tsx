"use client";

// History page: a reverse-chronological list of past runs (GET /runs). Each row
// links to its replay. Read-only — nothing here starts, cancels, or mutates a
// run; it is a window onto the ledger.

import { useEffect, useState } from "react";

import { listRuns } from "@/lib/api";
import type { RunSummary } from "@/lib/types";
import { StatusBadge } from "./StatusBadge";

function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function RunHistory() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listRuns(100)
      .then((rows) => {
        if (!cancelled) setRuns(rows);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold text-slate-100">History</h1>
          <p className="mt-1 text-sm text-slate-400">
            Past runs, newest first. Open one to replay it from the event ledger.
          </p>
        </div>
        <nav className="mt-1 flex gap-2">
          <a
            href="/"
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
          >
            ← Run
          </a>
          <a
            href="/memory"
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
          >
            Memory →
          </a>
        </nav>
      </header>

      {error && (
        <p
          role="alert"
          className="rounded-lg border border-rose-800 bg-rose-950/40 px-4 py-3 text-sm text-rose-300"
        >
          Could not load runs: {error}
        </p>
      )}

      {!error && runs === null && (
        <p className="text-sm text-slate-500">Loading…</p>
      )}

      {!error && runs?.length === 0 && (
        <p className="text-sm text-slate-500">
          No runs yet.{" "}
          <a href="/" className="text-sky-400 underline hover:text-sky-300">
            Start one
          </a>
          .
        </p>
      )}

      {runs && runs.length > 0 && (
        <ol className="space-y-2">
          {runs.map((run) => (
            <li key={run.id}>
              <a
                href={`/history/${run.id}`}
                className="flex items-center gap-4 rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3 transition hover:border-slate-700 hover:bg-slate-800/60"
              >
                <StatusBadge status={run.status} />
                <span className="min-w-0 flex-1 truncate text-sm text-slate-200">
                  {run.goal}
                </span>
                <span className="shrink-0 text-xs text-slate-500">
                  {formatTime(run.created_at)}
                </span>
                <span className="shrink-0 font-mono text-xs text-slate-600">
                  {run.id.slice(0, 8)}
                </span>
              </a>
            </li>
          ))}
        </ol>
      )}
    </main>
  );
}
