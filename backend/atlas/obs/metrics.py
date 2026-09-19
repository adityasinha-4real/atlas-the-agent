"""A tiny, dependency-free, bounded metrics registry (RFC-0004 §29, ADR-0022).

Three primitives — ``Counter``, ``Gauge``, ``Histogram`` — and a ``MetricsRegistry``
that renders them in the Prometheus text exposition format. No client library, no
network, no background threads. Cardinality is intentionally **fixed**: labels are
only ever bounded enums (event type, version), never per-run ids, so memory is
bounded (invariant I-28).

The registry is *generic*; the domain wiring lives in :class:`RuntimeMetrics`,
which the emitter calls once per event. Recording is best-effort and a no-op fast
path when disabled, so observability can never alter or fail a run (I-25/I-23).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence

logger = logging.getLogger(__name__)

# Latency buckets (seconds). Fixed set → bounded series per histogram.
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)


def _esc_label(value: str) -> str:
    """Escape a label value for the exposition format."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _fmt(value: float) -> str:
    """Render a numeric sample without a needless trailing ``.0``."""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


class _Base:
    """Shared label handling for the three metric types."""

    kind = "untyped"

    def __init__(
        self, name: str, documentation: str, labelnames: Sequence[str] = ()
    ) -> None:
        self.name = name
        self.documentation = documentation
        self.labelnames: tuple[str, ...] = tuple(labelnames)
        self._lock = threading.Lock()

    def _key(self, labels: Mapping[str, object]) -> tuple[str, ...]:
        if set(labels) != set(self.labelnames):
            raise KeyError(
                f"{self.name} expects labels {self.labelnames}, got {tuple(labels)}"
            )
        return tuple(str(labels[name]) for name in self.labelnames)

    def _labelstr(self, key: tuple[str, ...]) -> str:
        if not self.labelnames:
            return ""
        pairs = ", ".join(
            f'{name}="{_esc_label(value)}"'
            for name, value in zip(self.labelnames, key, strict=True)
        )
        return "{" + pairs + "}"

    def collect_lines(self) -> Iterator[str]:  # pragma: no cover - overridden
        raise NotImplementedError


class Counter(_Base):
    """A monotonically increasing value."""

    kind = "counter"

    def __init__(
        self, name: str, documentation: str, labelnames: Sequence[str] = ()
    ) -> None:
        super().__init__(name, documentation, labelnames)
        self._values: dict[tuple[str, ...], float] = {}
        if not self.labelnames:
            self._values[()] = 0.0

    def inc(self, amount: float = 1.0, **labels: object) -> None:
        if amount < 0:
            raise ValueError("counters cannot decrease")
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels: object) -> float:
        return self._values.get(self._key(labels), 0.0)

    def collect_lines(self) -> Iterator[str]:
        for key, val in sorted(self._values.items()):
            yield f"{self.name}{self._labelstr(key)} {_fmt(val)}"


class Gauge(_Base):
    """A value that can go up or down."""

    kind = "gauge"

    def __init__(
        self, name: str, documentation: str, labelnames: Sequence[str] = ()
    ) -> None:
        super().__init__(name, documentation, labelnames)
        self._values: dict[tuple[str, ...], float] = {}
        if not self.labelnames:
            self._values[()] = 0.0

    def set(self, value: float, **labels: object) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, **labels: object) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels: object) -> None:
        self.inc(-amount, **labels)

    def value(self, **labels: object) -> float:
        return self._values.get(self._key(labels), 0.0)

    def collect_lines(self) -> Iterator[str]:
        for key, val in sorted(self._values.items()):
            yield f"{self.name}{self._labelstr(key)} {_fmt(val)}"


class Histogram(_Base):
    """Cumulative bucketed observations plus ``_sum`` and ``_count`` series."""

    kind = "histogram"

    def __init__(
        self,
        name: str,
        documentation: str,
        buckets: Iterable[float] = DEFAULT_BUCKETS,
        labelnames: Sequence[str] = (),
    ) -> None:
        super().__init__(name, documentation, labelnames)
        self.buckets: tuple[float, ...] = tuple(sorted(buckets))
        # counts[key] has len(buckets)+1 slots; the last is the +Inf overflow.
        self._counts: dict[tuple[str, ...], list[int]] = {}
        self._sums: dict[tuple[str, ...], float] = {}
        if not self.labelnames:
            self._counts[()] = [0] * (len(self.buckets) + 1)
            self._sums[()] = 0.0

    def observe(self, value: float, **labels: object) -> None:
        key = self._key(labels)
        with self._lock:
            counts = self._counts.setdefault(key, [0] * (len(self.buckets) + 1))
            placed = False
            for i, upper in enumerate(self.buckets):
                if value <= upper:
                    counts[i] += 1
                    placed = True
                    break
            if not placed:
                counts[-1] += 1
            self._sums[key] = self._sums.get(key, 0.0) + value

    def collect_lines(self) -> Iterator[str]:
        for key in sorted(self._counts):
            counts = self._counts[key]
            cumulative = 0
            for i, upper in enumerate(self.buckets):
                cumulative += counts[i]
                labels = self._bucket_labelstr(key, _fmt(upper))
                yield f"{self.name}_bucket{labels} {cumulative}"
            cumulative += counts[-1]
            yield f'{self.name}_bucket{self._bucket_labelstr(key, "+Inf")} {cumulative}'
            yield f"{self.name}_sum{self._labelstr(key)} {_fmt(self._sums.get(key, 0.0))}"
            yield f"{self.name}_count{self._labelstr(key)} {cumulative}"

    def _bucket_labelstr(self, key: tuple[str, ...], le: str) -> str:
        pairs = [
            f'{name}="{_esc_label(value)}"'
            for name, value in zip(self.labelnames, key, strict=True)
        ]
        pairs.append(f'le="{le}"')
        return "{" + ", ".join(pairs) + "}"


class MetricsRegistry:
    """A named collection of metrics with get-or-create semantics."""

    def __init__(self) -> None:
        self._metrics: dict[str, _Base] = {}
        self._lock = threading.Lock()

    def _get_or_create(self, metric: _Base) -> _Base:
        with self._lock:
            existing = self._metrics.get(metric.name)
            if existing is not None:
                if type(existing) is not type(metric):
                    raise TypeError(
                        f"metric {metric.name} already registered as "
                        f"{existing.kind}, not {metric.kind}"
                    )
                return existing
            self._metrics[metric.name] = metric
            return metric

    def counter(
        self, name: str, documentation: str, labelnames: Sequence[str] = ()
    ) -> Counter:
        metric = self._get_or_create(Counter(name, documentation, labelnames))
        assert isinstance(metric, Counter)
        return metric

    def gauge(
        self, name: str, documentation: str, labelnames: Sequence[str] = ()
    ) -> Gauge:
        metric = self._get_or_create(Gauge(name, documentation, labelnames))
        assert isinstance(metric, Gauge)
        return metric

    def histogram(
        self,
        name: str,
        documentation: str,
        buckets: Iterable[float] = DEFAULT_BUCKETS,
        labelnames: Sequence[str] = (),
    ) -> Histogram:
        metric = self._get_or_create(
            Histogram(name, documentation, buckets, labelnames)
        )
        assert isinstance(metric, Histogram)
        return metric

    def render(self) -> str:
        """Render every metric in the Prometheus text exposition format."""
        lines: list[str] = []
        with self._lock:
            metrics = list(self._metrics.values())
        for metric in metrics:
            lines.append(f"# HELP {metric.name} {metric.documentation}")
            lines.append(f"# TYPE {metric.name} {metric.kind}")
            lines.extend(metric.collect_lines())
        return "\n".join(lines) + "\n"


class RuntimeMetrics:
    """Domain wiring over a :class:`MetricsRegistry` (RFC-0004 §29).

    Constructed once per app. ``on_event`` is the single hook the emitter calls;
    it is a no-op when disabled and never raises (I-25). ``events_total`` is
    labeled only by the bounded ``EventType`` enum, so every run/task/tool/memory
    metric is derivable from it without unbounded cardinality (I-28).
    """

    def __init__(
        self, registry: MetricsRegistry, *, enabled: bool, version: str
    ) -> None:
        self.registry = registry
        self.enabled = enabled
        self._events = registry.counter(
            "atlas_events_total", "Total run events emitted, by type", ["type"]
        )
        self._emit_seconds = registry.histogram(
            "atlas_event_emit_seconds", "emit() latency in seconds"
        )
        # A pull-updated gauge; the /metrics handler sets it from the hub at scrape.
        registry.gauge("atlas_ws_subscribers", "Live WebSocket subscribers")
        registry.gauge(
            "atlas_build_info", "Build info (value always 1)", ["version"]
        ).set(1.0, version=version)

    def on_event(self, type_value: str, seconds: float) -> None:
        """Record one emitted event. Best-effort; never raises (I-25)."""
        if not self.enabled:
            return
        try:
            self._events.inc(type=type_value)
            self._emit_seconds.observe(seconds)
        except Exception:  # noqa: BLE001 - observability must never break a run
            logger.debug("metrics.on_event failed", exc_info=True)
