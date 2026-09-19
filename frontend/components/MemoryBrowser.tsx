"use client";

// Memory page (M5): browse, search, inspect, pin, and forget the episodic
// memories distilled from past runs. Read/manage only — recall/write happen in
// the runtime. No run context here; it talks to the /memories API directly.

import { FormEvent, useCallback, useEffect, useState } from "react";

import { deleteMemory, listMemories, setMemoryPinned } from "@/lib/api";
import type { MemoryOutcome, MemoryView } from "@/lib/types";

const PAGE_SIZE = 20;

const OUTCOME_STYLES: Record<MemoryOutcome, string> = {
  done: "bg-emerald-700 text-emerald-50",
  partial: "bg-amber-700 text-amber-50",
  failed: "bg-rose-800 text-rose-50",
  cancelled: "bg-slate-600 text-slate-50",
};

export function MemoryBrowser() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<MemoryView[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Search ignores paging (relevance-ranked); browsing paginates.
      const data = submitted
        ? await listMemories({ q: submitted, limit: PAGE_SIZE })
        : await listMemories({ limit: PAGE_SIZE, offset });
      setItems(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [submitted, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  function onSearch(e: FormEvent) {
    e.preventDefault();
    setOffset(0);
    setSubmitted(query.trim());
  }

  async function onPin(m: MemoryView) {
    const updated = await setMemoryPinned(m.id, !m.pinned);
    setItems((prev) => prev.map((it) => (it.id === m.id ? updated : it)));
  }

  async function onDelete(id: string) {
    await deleteMemory(id);
    setItems((prev) => prev.filter((it) => it.id !== id));
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold text-slate-100">Memory</h1>
          <p className="mt-1 text-sm text-slate-400">
            Lessons distilled from past runs and recalled at plan time.
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
            href="/history"
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
          >
            History →
          </a>
        </nav>
      </header>

      <form onSubmit={onSearch} className="mb-6 flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search memories…"
          aria-label="Search memories"
          className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-4 py-2 text-slate-100 outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-500/40"
        />
        <button
          type="submit"
          className="rounded-lg bg-teal-600 px-5 py-2 font-semibold text-white hover:bg-teal-500"
        >
          Search
        </button>
        {submitted && (
          <button
            type="button"
            onClick={() => {
              setQuery("");
              setSubmitted("");
              setOffset(0);
            }}
            className="rounded-lg border border-slate-600 px-4 py-2 text-slate-200 hover:bg-slate-800"
          >
            Clear
          </button>
        )}
      </form>

      {error && (
        <p role="alert" className="mb-4 text-sm text-rose-400">
          Error: {error}
        </p>
      )}
      {loading && <p className="text-sm text-slate-500">Loading…</p>}
      {!loading && items.length === 0 && (
        <p className="text-sm text-slate-500">
          No memories yet. Enable memory (<code>ATLAS_MEMORY_ENABLED=true</code>) and
          complete a run.
        </p>
      )}

      <ul className="space-y-3">
        {items.map((m) => (
          <MemoryCard
            key={m.id}
            memory={m}
            onPin={() => onPin(m)}
            onDelete={() => onDelete(m.id)}
          />
        ))}
      </ul>

      {!submitted && (
        <div className="mt-6 flex items-center justify-between text-sm">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            ← Newer
          </button>
          <span className="text-slate-500">
            {items.length > 0 ? `${offset + 1}–${offset + items.length}` : "—"}
          </span>
          <button
            type="button"
            disabled={items.length < PAGE_SIZE}
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            Older →
          </button>
        </div>
      )}
    </main>
  );
}

function MemoryCard({
  memory,
  onPin,
  onDelete,
}: {
  memory: MemoryView;
  onPin: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-1 flex items-center gap-2">
            <span
              className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${OUTCOME_STYLES[memory.outcome]}`}
            >
              {memory.outcome}
            </span>
            {memory.pinned && (
              <span title="Pinned" aria-label="Pinned">
                📌
              </span>
            )}
            <span className="text-[10px] uppercase text-slate-500">
              {memory.source}
            </span>
          </div>
          <p className="truncate text-sm font-medium text-slate-100" title={memory.goal}>
            {memory.goal}
          </p>
          {memory.lessons && (
            <p className="mt-1 text-sm text-teal-200/90">{memory.lessons}</p>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={onPin}
            aria-label={`${memory.pinned ? "Unpin" : "Pin"} memory: ${memory.goal}`}
            className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800"
          >
            {memory.pinned ? "Unpin" : "Pin"}
          </button>
          <button
            type="button"
            onClick={onDelete}
            aria-label={`Forget memory: ${memory.goal}`}
            className="rounded border border-rose-800 px-2 py-1 text-xs text-rose-300 hover:bg-rose-950/50"
          >
            Forget
          </button>
        </div>
      </div>

      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="mt-2 text-xs text-slate-500 hover:text-slate-300"
      >
        {open ? "▾ details" : "▸ details"}
      </button>
      {open && (
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-slate-400">
          {memory.summary && (
            <>
              <dt className="text-slate-500">Summary</dt>
              <dd className="text-slate-300">{memory.summary}</dd>
            </>
          )}
          <dt className="text-slate-500">Tools</dt>
          <dd className="text-slate-300">
            {memory.tools_used.length ? memory.tools_used.join(", ") : "none"}
          </dd>
          <dt className="text-slate-500">Recalled</dt>
          <dd className="text-slate-300">{memory.use_count}×</dd>
          <dt className="text-slate-500">Salience</dt>
          <dd className="text-slate-300">{memory.salience.toFixed(2)}</dd>
          <dt className="text-slate-500">Created</dt>
          <dd className="text-slate-300">
            {new Date(memory.created_at).toLocaleString()}
          </dd>
        </dl>
      )}
    </li>
  );
}
