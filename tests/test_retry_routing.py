"""Unit tests for retry configuration and on_error routing."""
from __future__ import annotations

import asyncio
import logging
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.config.schema import AgentConfig, RetryConfig
from modular_agent_designer.nodes import agent_node
from modular_agent_designer.nodes.agent_node import (
    _ADK_NODE_RUNNER_LOGGERS,
    WORKFLOW_ERROR_OUTPUT_KEY,
    _compute_retry_delay,
    _root_cause_exception,
    _suppress_handled_adk_node_errors,
    build_agent_node,
)
from modular_agent_designer.workflow.builder import _error_edge_matches, build_workflow


def _load(tmp_path: Path, content: str):
    p = tmp_path / "wf.yaml"
    p.write_text(content)
    return load_workflow(p)


class _State(dict):
    def to_dict(self):
        return dict(self)


class _Ctx:
    def __init__(self, state: dict):
        self.state = _State(state)
        self.actions = SimpleNamespace(state_delta={})


async def _run_node_route(node, state: dict) -> str | None:
    async for event in node.run(ctx=_Ctx(state), node_input=None):
        return event.actions.route
    return None


async def _run_node_event(node, state: dict):
    async for event in node.run(ctx=_Ctx(state), node_input=None):
        return event
    return None


async def _collect_node_events(node, ctx) -> list:
    events = []
    async for event in node.run(ctx=ctx, node_input=None):
        events.append(event)
    return events


def _node_by_name(wf, name: str):
    for edge in wf.edges:
        for node in (edge.from_node, edge.to_node):
            if getattr(node, "name", None) == name:
                return node
    raise AssertionError(f"node not found: {name}")


# ---------------------------------------------------------------------------
# RetryConfig schema validation
# ---------------------------------------------------------------------------


def test_retry_config_defaults():
    rc = RetryConfig()
    assert rc.max_retries == 3
    assert rc.backoff == "fixed"
    assert rc.delay_seconds == 1.0


def test_retry_config_custom():
    rc = RetryConfig(max_retries=5, backoff="exponential", delay_seconds=2.0)
    assert rc.max_retries == 5
    assert rc.backoff == "exponential"
    assert rc.delay_seconds == 2.0


def test_retry_config_min_retries():
    with pytest.raises(ValidationError):
        RetryConfig(max_retries=0)


def test_retry_config_max_retries():
    with pytest.raises(ValidationError):
        RetryConfig(max_retries=11)


def test_retry_config_negative_delay():
    with pytest.raises(ValidationError):
        RetryConfig(delay_seconds=-1)


# ---------------------------------------------------------------------------
# Retry delay computation
# ---------------------------------------------------------------------------


def test_fixed_delay():
    cfg = RetryConfig(backoff="fixed", delay_seconds=2.0)
    assert _compute_retry_delay(cfg, 1) == 2.0
    assert _compute_retry_delay(cfg, 2) == 2.0
    assert _compute_retry_delay(cfg, 3) == 2.0


def test_exponential_delay():
    cfg = RetryConfig(backoff="exponential", delay_seconds=1.0)
    assert _compute_retry_delay(cfg, 1) == 1.0   # 1 * 2^0
    assert _compute_retry_delay(cfg, 2) == 2.0   # 1 * 2^1
    assert _compute_retry_delay(cfg, 3) == 4.0   # 1 * 2^2


def test_delay_none_config():
    assert _compute_retry_delay(None, 1) == 0


def test_handled_agent_failures_suppress_adk_node_runner_tracebacks() -> None:
    adk_loggers = [
        logging.getLogger(logger_name)
        for logger_name in _ADK_NODE_RUNNER_LOGGERS
    ]
    original_levels = [adk_logger.level for adk_logger in adk_loggers]

    with _suppress_handled_adk_node_errors(True):
        assert all(
            adk_logger.level > logging.CRITICAL
            for adk_logger in adk_loggers
        )

    assert [adk_logger.level for adk_logger in adk_loggers] == original_levels


def test_agent_failure_unwraps_adk_dynamic_node_error() -> None:
    class WrappedError(Exception):
        def __init__(self) -> None:
            self.error = RuntimeError("ollama model not found")
            super().__init__("Dynamic node failing_agent failed")

    root = _root_cause_exception(WrappedError())

    assert type(root).__name__ == "RuntimeError"
    assert str(root) == "ollama model not found"


def test_agent_node_reraises_without_on_error(monkeypatch: pytest.MonkeyPatch):
    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FailingCtx(_Ctx):
        async def run_node(self, agent, node_input=None, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(agent_node, "Agent", FakeAgent)

    cfg = AgentConfig(model="m", instruction="work")
    node = build_agent_node("worker", cfg, object(), [], handles_errors=False)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(_collect_node_events(node, FailingCtx({})))


def test_agent_node_writes_error_state_with_on_error(monkeypatch: pytest.MonkeyPatch):
    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FailingCtx(_Ctx):
        async def run_node(self, agent, node_input=None, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(agent_node, "Agent", FakeAgent)

    cfg = AgentConfig(model="m", instruction="work")
    node = build_agent_node("worker", cfg, object(), [], handles_errors=True)

    events = asyncio.run(_collect_node_events(node, FailingCtx({})))

    assert len(events) == 1
    errors = events[0].actions.state_delta["_error_worker"]
    assert isinstance(errors, list) and len(errors) == 1
    assert errors[0]["error_type"] == "RuntimeError"
    assert "boom" in errors[0]["error_message"]
    assert (
        events[0].actions.state_delta[WORKFLOW_ERROR_OUTPUT_KEY]
        == "Agent 'worker' failed: RuntimeError: boom"
    )
    assert events[0].output == "Agent 'worker' failed: RuntimeError: boom"
    assert events[0].node_info.message_as_output is True


def test_agent_node_timeout_reraises_without_on_error(monkeypatch: pytest.MonkeyPatch):
    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class SlowCtx(_Ctx):
        async def run_node(self, agent, node_input=None, **kwargs):
            await asyncio.sleep(0.05)
            return SimpleNamespace()

    monkeypatch.setattr(agent_node, "Agent", FakeAgent)

    cfg = AgentConfig(model="m", instruction="work", timeout_seconds=0.001)
    node = build_agent_node("worker", cfg, object(), [], handles_errors=False)

    with pytest.raises(TimeoutError):
        asyncio.run(_collect_node_events(node, SlowCtx({})))


def test_agent_node_without_timeout_does_not_call_wait_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class PassingCtx(_Ctx):
        async def run_node(self, agent, node_input=None, **kwargs):
            return SimpleNamespace()

    def fail_wait_for(*args, **kwargs):
        raise AssertionError("wait_for should not be called")

    monkeypatch.setattr(agent_node, "Agent", FakeAgent)
    monkeypatch.setattr(agent_node.asyncio, "wait_for", fail_wait_for)

    cfg = AgentConfig(model="m", instruction="work")
    node = build_agent_node("worker", cfg, object(), [], handles_errors=False)
    events = asyncio.run(_collect_node_events(node, PassingCtx({})))
    assert len(events) == 1


# ---------------------------------------------------------------------------
# Agent with retry in YAML
# ---------------------------------------------------------------------------


def test_agent_retry_config_parsed(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: retry_test
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          caller:
            model: m
            instruction: call API
            retry:
              max_retries: 5
              backoff: exponential
              delay_seconds: 0.5
        workflow:
          nodes: [caller]
          edges: []
          entry: caller
    """)
    cfg = _load(tmp_path, yaml)
    caller = cfg.agents["caller"]
    assert caller.retry is not None
    assert caller.retry.max_retries == 5
    assert caller.retry.backoff == "exponential"
    assert caller.retry.delay_seconds == 0.5


def test_agent_without_retry(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: no_retry
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          simple:
            model: m
            instruction: simple agent
        workflow:
          nodes: [simple]
          edges: []
          entry: simple
    """)
    cfg = _load(tmp_path, yaml)
    assert cfg.agents["simple"].retry is None


# ---------------------------------------------------------------------------
# on_error edge validation
# ---------------------------------------------------------------------------


def test_on_error_cannot_have_condition(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: bad_on_error
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          a:
            model: m
            instruction: a
          b:
            model: m
            instruction: b
        workflow:
          nodes: [a, b]
          edges:
            - from: a
              to: b
              on_error: true
              condition: "something"
          entry: a
    """)
    with pytest.raises((ValidationError, ValueError), match="on_error"):
        _load(tmp_path, yaml)


# ---------------------------------------------------------------------------
# Build-level: on_error edge wiring
# ---------------------------------------------------------------------------


def test_on_error_edge_creates_error_router(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: error_route_test
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          caller:
            model: m
            instruction: call
            retry:
              max_retries: 2
          success:
            model: m
            instruction: success
          error:
            model: m
            instruction: error
        workflow:
          nodes: [caller, success, error]
          edges:
            - from: caller
              to: success
            - from: caller
              to: error
              on_error: true
          entry: caller
    """)
    cfg = _load(tmp_path, yaml)
    wf = build_workflow(cfg)

    # Edges: START→caller, caller→success, caller→error_router, error_router→error
    routes = [e.route for e in wf.edges if e.route is not None]
    assert "_error_0" in routes


def test_agent_normal_edge_is_gated_by_error_router(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: fail_stop_test
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          caller:
            model: m
            instruction: call
          next:
            model: m
            instruction: next
        workflow:
          nodes: [caller, next]
          edges:
            - from: caller
              to: next
          entry: caller
    """)
    cfg = _load(tmp_path, yaml)
    wf = build_workflow(cfg)

    routes = [e.route for e in wf.edges if e.route is not None]
    assert "_ok" in routes
    assert _node_by_name(wf, "caller_error_router") is not None


# ---------------------------------------------------------------------------
# Typed error routing: schema validation
# ---------------------------------------------------------------------------


def test_on_error_condition_default_is_valid(tmp_path: Path):
    """on_error edges may carry condition: default for explicit fallback ordering."""
    content = textwrap.dedent("""\
        name: typed_err
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          a: {model: m, instruction: a}
          b: {model: m, instruction: b}
        workflow:
          nodes: [a, b]
          edges:
            - from: a
              to: b
              on_error: true
              condition: default
          entry: a
    """)
    cfg = _load(tmp_path, content)
    assert cfg.workflow.edges[0].on_error is True


def test_on_error_non_default_condition_raises(tmp_path: Path):
    content = textwrap.dedent("""\
        name: bad
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          a: {model: m, instruction: a}
          b: {model: m, instruction: b}
        workflow:
          nodes: [a, b]
          edges:
            - from: a
              to: b
              on_error: true
              condition: "some_value"
          entry: a
    """)
    with pytest.raises((ValidationError, ValueError), match="on_error"):
        _load(tmp_path, content)


def test_error_type_on_non_error_edge_raises(tmp_path: Path):
    content = textwrap.dedent("""\
        name: bad
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          a: {model: m, instruction: a}
          b: {model: m, instruction: b}
        workflow:
          nodes: [a, b]
          edges:
            - from: a
              to: b
              error_type: TimeoutError
          entry: a
    """)
    with pytest.raises((ValidationError, ValueError), match="error_type"):
        _load(tmp_path, content)


def test_typed_error_routing_builds(tmp_path: Path):
    content = textwrap.dedent("""\
        name: typed_err
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          caller: {model: m, instruction: call, retry: {max_retries: 1}}
          success: {model: m, instruction: success}
          handle_timeout: {model: m, instruction: timeout}
          handle_other: {model: m, instruction: other}
        workflow:
          nodes: [caller, success, handle_timeout, handle_other]
          edges:
            - from: caller
              to: success
            - from: caller
              to: handle_timeout
              on_error: true
              error_type: TimeoutError
            - from: caller
              to: handle_other
              on_error: true
              condition: default
          entry: caller
    """)
    cfg = _load(tmp_path, content)
    wf = build_workflow(cfg)
    routes = [e.route for e in wf.edges if e.route is not None]
    assert "_error_0" in routes
    assert "_error_1" in routes


# ---------------------------------------------------------------------------
# Typed error routing: _error_edge_matches unit tests
# ---------------------------------------------------------------------------


def _make_error_edge(to, error_type=None, error_match=None, condition=None):
    from modular_agent_designer.config.schema import EdgeConfig
    kwargs = {"from": "src", "to": to, "on_error": True}
    if error_type is not None:
        kwargs["error_type"] = error_type
    if error_match is not None:
        kwargs["error_match"] = error_match
    if condition is not None:
        kwargs["condition"] = condition
    return EdgeConfig(**kwargs)


def test_untyped_edge_matches_any_error():
    edge = _make_error_edge("handler")
    assert _error_edge_matches(edge, "ValueError", "oops") is True
    assert _error_edge_matches(edge, "TimeoutError", "timed out") is True


def test_typed_error_type_match():
    edge = _make_error_edge("handler", error_type="TimeoutError")
    assert _error_edge_matches(edge, "TimeoutError", "timed out") is True
    assert _error_edge_matches(edge, "ValueError", "bad value") is False


def test_typed_error_match_regex():
    edge = _make_error_edge("handler", error_match=r"rate.?limit")
    assert _error_edge_matches(edge, "HTTPError", "rate limit exceeded") is True
    assert _error_edge_matches(edge, "HTTPError", "server error") is False


def test_both_error_type_and_match_must_satisfy():
    edge = _make_error_edge("handler", error_type="TimeoutError", error_match=r"connect")
    assert _error_edge_matches(edge, "TimeoutError", "connect timed out") is True
    assert _error_edge_matches(edge, "TimeoutError", "disk full") is False
    assert _error_edge_matches(edge, "ValueError", "connect refused") is False


def test_default_edge_never_matches_via_helper():
    edge = _make_error_edge("fallback", condition="default")
    # Default edges are excluded from _error_edge_matches (handled as fallback separately)
    assert _error_edge_matches(edge, "ValueError", "anything") is False


def test_typed_error_routing_routes_are_distinct(tmp_path: Path):
    content = textwrap.dedent("""\
        name: typed_err2
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          caller: {model: m, instruction: call, retry: {max_retries: 1}}
          success: {model: m, instruction: success}
          handle_timeout: {model: m, instruction: timeout}
          handle_rate: {model: m, instruction: rate}
          handle_other: {model: m, instruction: other}
        workflow:
          nodes: [caller, success, handle_timeout, handle_rate, handle_other]
          edges:
            - from: caller
              to: success
            - from: caller
              to: handle_timeout
              on_error: true
              error_type: TimeoutError
            - from: caller
              to: handle_rate
              on_error: true
              error_match: "rate.?limit"
            - from: caller
              to: handle_other
              on_error: true
              condition: default
          entry: caller
    """)
    cfg = _load(tmp_path, content)
    wf = build_workflow(cfg)
    routes = [e.route for e in wf.edges if e.route is not None]
    # 3 error edges → _error_0, _error_1, _error_2
    assert "_error_0" in routes
    assert "_error_1" in routes
    assert "_error_2" in routes


def test_on_error_preserves_conditional_success_routing(tmp_path: Path):
    content = textwrap.dedent("""\
        name: mixed_success_error
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          caller: {model: m, instruction: call, retry: {max_retries: 1}}
          low: {model: m, instruction: low}
          high: {model: m, instruction: high}
          fallback: {model: m, instruction: fallback}
          error: {model: m, instruction: error}
        workflow:
          nodes: [caller, low, high, fallback, error]
          edges:
            - from: caller
              to: low
              condition: low
            - from: caller
              to: high
              condition: high
            - from: caller
              to: fallback
              condition: default
            - from: caller
              to: error
              on_error: true
          entry: caller
    """)
    cfg = _load(tmp_path, content)
    wf = build_workflow(cfg)

    routes = [e.route for e in wf.edges if e.route is not None]
    assert "_ok" in routes
    assert "_route_0" in routes
    assert "_route_1" in routes
    assert "_route_2" in routes
    assert "_error_0" in routes

    error_router = _node_by_name(wf, "caller_error_router")
    success_router = _node_by_name(wf, "caller_router")

    ok_route = asyncio.run(_run_node_route(error_router, {"caller": "high"}))
    high_route = asyncio.run(_run_node_route(success_router, {"caller": "high"}))

    assert ok_route == "_ok"
    assert high_route == "_route_1"


def test_error_router_success_path_is_quiet(tmp_path: Path):
    content = textwrap.dedent("""\
        name: quiet_success_gate
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          caller: {model: m, instruction: call}
          success: {model: m, instruction: success}
        workflow:
          nodes: [caller, success]
          edges:
            - from: caller
              to: success
          entry: caller
    """)
    cfg = _load(tmp_path, content)
    wf = build_workflow(cfg)
    error_router = _node_by_name(wf, "caller_error_router")
    success_gate = _node_by_name(wf, "caller_success_gate")

    router_event = asyncio.run(_run_node_event(error_router, {"caller": "done"}))
    gate_event = asyncio.run(_run_node_event(success_gate, {"caller": "done"}))

    assert router_event.actions.route == "_ok"
    assert router_event.output is None
    assert gate_event.output is None


def test_on_error_preserves_dynamic_success_routing(tmp_path: Path):
    content = textwrap.dedent("""\
        name: dynamic_success_error
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          caller: {model: m, instruction: call, retry: {max_retries: 1}}
          node_a: {model: m, instruction: a}
          node_b: {model: m, instruction: b}
          error: {model: m, instruction: error}
        workflow:
          nodes: [caller, node_a, node_b, error]
          edges:
            - from: caller
              to: "{{state.caller.next_node}}"
              allowed_targets: [node_a, node_b]
            - from: caller
              to: error
              on_error: true
          entry: caller
    """)
    cfg = _load(tmp_path, content)
    wf = build_workflow(cfg)

    routes = {e.route for e in wf.edges if e.route is not None}
    assert "_ok" in routes
    assert "node_a" in routes
    assert "node_b" in routes
    assert "_error_0" in routes

    dispatch_edges = [
        e
        for e in wf.edges
        if getattr(e.to_node, "name", "") == "_dispatch_caller_0"
    ]
    assert len(dispatch_edges) == 1
    assert getattr(dispatch_edges[0].from_node, "name", "") == "caller_success_gate"


def test_error_router_matches_typed_regex_wildcard_and_default(tmp_path: Path):
    content = textwrap.dedent("""\
        name: error_match_order
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          caller: {model: m, instruction: call, retry: {max_retries: 1}}
          success: {model: m, instruction: success}
          handle_timeout: {model: m, instruction: timeout}
          handle_rate: {model: m, instruction: rate}
          handle_any: {model: m, instruction: any}
          handle_other: {model: m, instruction: other}
        workflow:
          nodes: [caller, success, handle_timeout, handle_rate, handle_any, handle_other]
          edges:
            - from: caller
              to: success
            - from: caller
              to: handle_timeout
              on_error: true
              error_type: TimeoutError
            - from: caller
              to: handle_rate
              on_error: true
              error_match: "rate.?limit"
            - from: caller
              to: handle_any
              on_error: true
            - from: caller
              to: handle_other
              on_error: true
              condition: default
          entry: caller
    """)
    cfg = _load(tmp_path, content)
    wf = build_workflow(cfg)
    router = _node_by_name(wf, "caller_error_router")

    timeout_route = asyncio.run(_run_node_route(
        router,
        {"_error_caller": [{"error_type": "TimeoutError", "error_message": "timed out"}]},
    ))
    rate_route = asyncio.run(_run_node_route(
        router,
        {"_error_caller": [{"error_type": "HTTPError", "error_message": "rate limit"}]},
    ))
    wildcard_route = asyncio.run(_run_node_route(
        router,
        {"_error_caller": [{"error_type": "ValueError", "error_message": "bad"}]},
    ))

    assert timeout_route == "_error_0"
    assert rate_route == "_error_1"
    assert wildcard_route == "_error_2"


def test_error_router_uses_default_when_no_error_edge_matches(tmp_path: Path):
    content = textwrap.dedent("""\
        name: error_default
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          caller: {model: m, instruction: call, retry: {max_retries: 1}}
          success: {model: m, instruction: success}
          handle_timeout: {model: m, instruction: timeout}
          handle_other: {model: m, instruction: other}
        workflow:
          nodes: [caller, success, handle_timeout, handle_other]
          edges:
            - from: caller
              to: success
            - from: caller
              to: handle_timeout
              on_error: true
              error_type: TimeoutError
            - from: caller
              to: handle_other
              on_error: true
              condition: default
          entry: caller
    """)
    cfg = _load(tmp_path, content)
    wf = build_workflow(cfg)
    router = _node_by_name(wf, "caller_error_router")

    route = asyncio.run(_run_node_route(
        router,
        {"_error_caller": [{"error_type": "ValueError", "error_message": "bad"}]},
    ))

    assert route == "_error_1"
