import type { RunStatus } from "@/lib/types";

const STYLES: Record<RunStatus, string> = {
  created: "bg-slate-700 text-slate-200",
  planning: "bg-indigo-700 text-indigo-100",
  running: "bg-blue-600 text-blue-50 animate-pulse",
  paused: "bg-amber-600 text-amber-50",
  done: "bg-emerald-600 text-emerald-50",
  failed: "bg-rose-700 text-rose-50",
  cancelled: "bg-slate-500 text-slate-50",
};

export function StatusBadge({ status }: { status: RunStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-wide ${STYLES[status]}`}
    >
      {status}
    </span>
  );
}
