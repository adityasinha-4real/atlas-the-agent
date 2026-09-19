"""A pure reducer that folds an event ledger into derived state (RFC-0004 §15).

``fold_events`` reconstructs a run's derived state — status, answer, error, and the
per-index task checklist — from nothing but its events. This makes "the UI/API can
rebuild everything from the ledger" a *checked* invariant (I-24, ADR-0023): the
replay tests fold a run's events and assert equivalence with the persisted views,
and the reconciler uses the same fold to decide whether an interrupted run already
reached a terminal event.

The reducer is deliberately tolerant of input shape — it accepts ``Event`` models
or plain ``{"type": ..., "payload": ...}`` dicts (so golden-ledger JSON fixtures
fold identically to live events).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from atlas.agent.schemas import RunStatus, TaskStatus

# Terminal run event type → the run status it implies.
_TERMINAL_EVENT_STATUS: dict[str, RunStatus] = {
    "run.completed": RunStatus.DONE,
    "run.failed": RunStatus.FAILED,
    "run.cancelled": RunStatus.CANCELLED,
}

# Task event type → the task status it implies.
_TASK_EVENT_STATUS: dict[str, TaskStatus] = {
    "task.started": TaskStatus.RUNNING,
    "task.completed": TaskStatus.DONE,
    "task.failed": TaskStatus.FAILED,
    "task.retrying": TaskStatus.RETRYING,
    "task.skipped": TaskStatus.SKIPPED,
    "task.cancelled": TaskStatus.CANCELLED,
}


@dataclass
class ReplayTask:
    """A task's derived state, folded from the ledger."""

    index: int
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    output: str | None = None


@dataclass
class ReplayState:
    """Derived state of a run, reconstructed purely from its events."""

    status: RunStatus = RunStatus.CREATED
    answer: str | None = None
    error: str | None = None
    tasks: dict[int, ReplayTask] = field(default_factory=dict)
    answer_tokens: list[str] = field(default_factory=list)
    terminal: bool = False

    @property
    def partial(self) -> bool:
        """A FAILED run that still carries an answer is a graceful partial."""
        return self.status is RunStatus.FAILED and self.answer is not None

    def task_status_by_index(self) -> dict[int, TaskStatus]:
        return {i: t.status for i, t in self.tasks.items()}


def _unpack(event: object) -> tuple[str, Mapping[str, object]]:
    """Return ``(type_value, payload)`` from an Event model or a plain dict."""
    if isinstance(event, Mapping):
        raw_type = event.get("type", "")
        payload = event.get("payload", {}) or {}
    else:
        raw_type = getattr(event, "type", "")
        payload = getattr(event, "payload", {}) or {}
    type_value = raw_type.value if hasattr(raw_type, "value") else str(raw_type)
    return type_value, payload


def _tasks_from_plan(payload: Mapping[str, object]) -> Iterable[ReplayTask]:
    for item in payload.get("tasks", []) or []:
        if not isinstance(item, Mapping):
            continue
        index = item.get("index")
        if index is None:
            continue
        yield ReplayTask(index=int(index), description=str(item.get("description", "")))


def sequence_is_intact(events: Iterable[object]) -> bool:
    """True iff the events' ``seq`` fields are the gapless run 1..n, in order.

    The ledger's per-run sequence must be gapless and monotonic (I-24); this
    detects a reordered or truncated stream. Events without a ``seq`` (e.g. golden
    fixtures) are ignored, so a fixture with no seqs trivially passes.
    """
    seqs: list[int] = []
    for event in events:
        if isinstance(event, Mapping):
            raw = event.get("seq")
        else:
            raw = getattr(event, "seq", None)
        if raw is not None:
            seqs.append(int(raw))
    return seqs == list(range(1, len(seqs) + 1))


def fold_events(events: Iterable[object]) -> ReplayState:
    """Fold an ordered event stream into a :class:`ReplayState` (pure).

    This is the project's **canonical reducer**: run status, answer, and the task
    checklist are defined as this fold of the ledger, and nothing else. The API
    projections, the frontend's event-sourced view, and the crash reconciler
    (ADR-0021) must agree with it; new features that derive run state should route
    through this function rather than re-interpreting events independently (I-24,
    ADR-0023). It trusts the given order — validate ordering with
    :func:`sequence_is_intact` first when the source is untrusted.
    """
    state = ReplayState()
    for event in events:
        type_value, payload = _unpack(event)

        if type_value == "run.created":
            state.status = RunStatus.CREATED
        elif type_value == "run.started":
            # run.started is emitted at PLANNING; treat any live run as RUNNING for
            # replay purposes (the exact terminal status is set below).
            state.status = RunStatus.RUNNING
        elif type_value in _TERMINAL_EVENT_STATUS:
            state.status = _TERMINAL_EVENT_STATUS[type_value]
            state.terminal = True
            if type_value == "run.failed":
                state.error = _str_or_none(payload.get("error"))

        elif type_value in ("plan.created", "plan.replanned"):
            for task in _tasks_from_plan(payload):
                state.tasks[task.index] = task

        elif type_value in _TASK_EVENT_STATUS:
            index = payload.get("index")
            if index is not None:
                task = state.tasks.setdefault(int(index), ReplayTask(index=int(index)))
                task.status = _TASK_EVENT_STATUS[type_value]
                if type_value == "task.completed":
                    task.output = _str_or_none(payload.get("output"))

        elif type_value == "answer.token":
            text = payload.get("text")
            if isinstance(text, str):
                state.answer_tokens.append(text)
        elif type_value == "answer.completed":
            text = payload.get("text")
            if isinstance(text, str):
                state.answer = text

    # If tokens streamed but no explicit completion carried the full text, the
    # concatenated tokens are the answer (matches the streaming contract).
    if state.answer is None and state.answer_tokens:
        state.answer = "".join(state.answer_tokens)
    return state


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
