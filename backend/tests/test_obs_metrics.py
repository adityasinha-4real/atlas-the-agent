"""Passive metrics registry, /metrics + /ready endpoints, and parity (M6)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atlas.core.config import Settings
from atlas.obs.metrics import Counter, Gauge, Histogram, MetricsRegistry, RuntimeMetrics

# --------------------------------------------------------------------------- #
# Registry primitives
# --------------------------------------------------------------------------- #


def test_counter_increments_and_renders() -> None:
    c = Counter("things_total", "count of things", ["kind"])
    c.inc(kind="a")
    c.inc(2.0, kind="a")
    c.inc(kind="b")
    assert c.value(kind="a") == 3.0
    assert c.value(kind="b") == 1.0
    lines = list(c.collect_lines())
    assert 'things_total{kind="a"} 3' in lines
    assert 'things_total{kind="b"} 1' in lines


def test_counter_rejects_negative() -> None:
    c = Counter("c_total", "c")
    with pytest.raises(ValueError):
        c.inc(-1)


def test_gauge_set_inc_dec() -> None:
    g = Gauge("g", "a gauge")
    g.set(5)
    g.inc(2)
    g.dec()
    assert g.value() == 6.0
    assert "g 6" in list(g.collect_lines())


def test_histogram_buckets_are_cumulative() -> None:
    h = Histogram("h", "a histogram", buckets=[0.1, 0.5, 1.0])
    for v in (0.05, 0.2, 0.2, 2.0):
        h.observe(v)
    lines = list(h.collect_lines())
    joined = "\n".join(lines)
    # cumulative: <=0.1 -> 1; <=0.5 -> 3; <=1.0 -> 3; +Inf -> 4
    assert 'h_bucket{le="0.1"} 1' in joined
    assert 'h_bucket{le="0.5"} 3' in joined
    assert 'h_bucket{le="1"} 3' in joined
    assert 'h_bucket{le="+Inf"} 4' in joined
    assert "h_count 4" in joined
    assert "h_sum 2.45" in joined


def test_registry_get_or_create_is_idempotent() -> None:
    reg = MetricsRegistry()
    a = reg.counter("x_total", "x")
    b = reg.counter("x_total", "x")
    assert a is b


def test_registry_type_conflict_raises() -> None:
    reg = MetricsRegistry()
    reg.counter("y", "y")
    with pytest.raises(TypeError):
        reg.gauge("y", "y")


def test_registry_render_has_help_and_type() -> None:
    reg = MetricsRegistry()
    reg.counter("z_total", "the z counter").inc()
    out = reg.render()
    assert "# HELP z_total the z counter" in out
    assert "# TYPE z_total counter" in out


def test_runtime_metrics_noop_when_disabled() -> None:
    reg = MetricsRegistry()
    rm = RuntimeMetrics(reg, enabled=False, version="0.6.0")
    rm.on_event("run.created", 0.001)
    # events counter stays at zero when disabled (no-op fast path, I-25).
    events = reg.counter("atlas_events_total", "x", ["type"])
    assert events.value(type="run.created") == 0.0


def test_runtime_metrics_records_when_enabled() -> None:
    reg = MetricsRegistry()
    rm = RuntimeMetrics(reg, enabled=True, version="0.6.0")
    rm.on_event("run.completed", 0.002)
    rm.on_event("run.completed", 0.003)
    assert rm._events.value(type="run.completed") == 2.0
    assert "atlas_build_info" in reg.render()


# --------------------------------------------------------------------------- #
# Endpoints + parity
# --------------------------------------------------------------------------- #


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        environment="test",
        llm_provider="echo",
        llm_model="echo",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}",
        workspace_dir=str(tmp_path / "ws"),
        log_level="WARNING",
        **overrides,  # type: ignore[arg-type]
    )


_TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled"}


def _run_to_terminal(client: TestClient, goal: str, timeout: float = 5.0) -> str:
    """Create a run and wait until its terminal event is in the ledger.

    Waiting for the terminal *event* (not just the run status) avoids the
    finalize/backfill race — the status flips to ``done`` a hair before the final
    ``run.completed`` event is persisted (RFC-0004 §11).
    """
    run_id = client.post("/runs", json={"goal": goal}).json()["id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = client.get(f"/runs/{run_id}/events").json()
        if events and events[-1]["type"] in _TERMINAL_EVENTS:
            return run_id
        time.sleep(0.02)
    raise AssertionError("run did not terminate")


@pytest.fixture
def metrics_client(tmp_path: Path) -> Iterator[TestClient]:
    from atlas.api.app import create_app

    with TestClient(create_app(_settings(tmp_path, metrics_enabled=True))) as c:
        yield c


def test_metrics_404_when_disabled(client: TestClient) -> None:
    assert client.get("/metrics").status_code == 404


def test_metrics_exposition_when_enabled(metrics_client: TestClient) -> None:
    _run_to_terminal(metrics_client, "hello observable world")
    resp = metrics_client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "# TYPE atlas_events_total counter" in body
    assert 'atlas_events_total{type="run.completed"}' in body
    assert "atlas_event_emit_seconds_bucket" in body
    assert "atlas_build_info" in body


def test_ready_reports_checks(client: TestClient) -> None:
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["checks"]["database"] is True
    assert body["checks"]["provider"] is True


def test_metrics_do_not_change_event_stream(tmp_path: Path) -> None:
    """Metrics on vs off produce an identical event stream (invariant I-23)."""
    from atlas.api.app import create_app

    def event_stream(metrics_enabled: bool, sub: str) -> list[tuple[str, object, int]]:
        settings = _settings(tmp_path / sub, metrics_enabled=metrics_enabled)
        with TestClient(create_app(settings)) as c:
            run_id = _run_to_terminal(c, "compare parity of the event stream")
            events = c.get(f"/runs/{run_id}/events").json()
        # Normalize the run-specific id so the two runs are comparable byte-for-byte.
        normalized: list[tuple[str, object, int]] = []
        for e in events:
            payload = json.loads(json.dumps(e["payload"]).replace(run_id, "RID"))
            normalized.append((e["type"], payload, e["seq"]))
        return normalized

    (tmp_path / "off").mkdir()
    (tmp_path / "on").mkdir()
    off = event_stream(False, "off")
    on = event_stream(True, "on")
    assert off == on
    assert len(off) > 0


def test_health_still_reports_version(client: TestClient) -> None:
    assert client.get("/health").json()["version"] == "0.7.0"
