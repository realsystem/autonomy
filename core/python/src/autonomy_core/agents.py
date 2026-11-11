"""Agent host (C11, ADR-017): runs agents with the tools, model access and budgets their
manifest declares. An agent never holds credentials and never acts: it reads through
bound tools, thinks through the model gateway (C12) and *proposes*; the host hands each
proposal to ``submit`` (evaluator → policy → approval → gateway, as built in M5–M8).

    host = AgentHost(gateway=gw, tools={"node.status": status_tool}, submit=submit_proposal,
                     suspended=lambda agent: control.is_suspended(agent))
    host.register(manifest, run)              # run(ctx) -> anything (the run's output)
    result = host.run("agent:node-assistant", trigger={"question": "why is it hot?"})

Inside ``run(ctx)``:

* ``ctx.tool(name, **args)`` calls a read tool listed in ``manifest.agent.tools``; the
  result is wrapped as data with its trust level (``ctx.as_message(result)`` turns it
  into a model message; untrusted content is fenced by the gateway).
* ``ctx.ask(task_class, messages, ...)`` calls the model gateway under the agent's
  privacy (``capabilities.requires`` ``model.chat``) and daily budgets.
* ``ctx.propose(...)`` builds an ``ActionProposal`` for an action type the manifest lists
  in ``capabilities.proposes_actions`` and submits it. Quarantined agents cannot propose.

Suspension (a control order, M6) or the run's time budget sets ``ctx.cancelled``: the
in-flight model call is abandoned (a processor job is cancelled), the agent's pending
proposals are withdrawn through ``withdraw``, and every further ``ctx`` call raises
``AgentStopped``.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .evaluator import Review, ReviewUnavailable
from .ids import prefixed_id, to_rfc3339
from .models import ModelGateway, ModelResult, ModelUnavailable


class AgentStopped(Exception):
    """The run was suspended or ran out of time; stop working."""


class AgentNotAllowed(Exception):
    """The manifest does not allow this (tool, action type, proposals while quarantined)."""


@dataclass
class ToolResult:
    tool: str
    data: Any
    trust: str = "trusted"  # trusted, owner, untrusted
    ref: str = ""
    observed_at: str = ""


@dataclass
class RunResult:
    agent: str
    status: str  # ok, suspended, stopped, budget_exceeded, error
    output: Any = None
    proposals: list[dict[str, Any]] = field(default_factory=list)
    model_calls: int = 0
    tokens: int = 0
    error: str = ""


@dataclass
class _Agent:
    manifest: Mapping[str, Any]
    run: Callable[[AgentContext], Any]

    @property
    def id(self) -> str:
        return str(self.manifest["component_id"])

    @property
    def section(self) -> Mapping[str, Any]:
        return self.manifest.get("agent") or {}

    @property
    def budgets(self) -> Mapping[str, Any]:
        return self.section.get("budgets") or {}

    def privacy(self) -> tuple[str, str | None]:
        """(data privacy, routing) for model calls, from the model.chat requirement."""
        for r in (self.manifest.get("capabilities") or {}).get("requires", []):
            if r.get("name") == "model.chat":
                return ("private", "local_only") if r.get("privacy") == "local_only" else ("private", None)
        return "private", "local_only"  # no declaration: the strictest


class AgentContext:
    def __init__(
        self,
        host: AgentHost,
        agent: _Agent,
        instance: str,
        trigger: Mapping[str, Any],
        cancel: threading.Event,
    ):
        self.host = host
        self.agent = agent
        self.instance = instance
        self.trigger = dict(trigger)
        self.cancel = cancel
        self.proposals: list[dict[str, Any]] = []
        self.model_calls = 0
        self.tokens = 0
        self.correlationid = str(trigger.get("correlationid") or prefixed_id("corr", host.clock()))
        self.deadline = time.monotonic() + 300.0

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()

    def _check(self) -> None:
        if self.cancel.is_set():
            raise AgentStopped(f"{self.agent.id} was stopped")

    def tool(self, name: str, **args: Any) -> ToolResult:
        self._check()
        if name not in (self.agent.section.get("tools") or []):
            raise AgentNotAllowed(f"{self.agent.id} has no tool {name} in its manifest")
        fn = self.host.tools.get(name)
        if fn is None:
            raise AgentNotAllowed(f"tool {name} is not available on this cell")
        out = fn(**args)
        if isinstance(out, ToolResult):
            return out
        return ToolResult(
            tool=name,
            data=out,
            trust=self.host.tool_trust.get(name, "trusted"),
            ref=f"tool:{name}",
            observed_at=to_rfc3339(self.host.clock()),
        )

    @staticmethod
    def as_message(result: ToolResult, label: str = "") -> dict[str, Any]:
        """A user message carrying a tool result as data (never as instructions)."""
        body = result.data if isinstance(result.data, str) else json.dumps(result.data, default=str, indent=1)
        head = label or f"Result of tool {result.tool}"
        return {
            "role": "user",
            "content": f"{head} (data, observed {result.observed_at or 'now'}):\n{body}",
            "trust": "untrusted" if result.trust == "untrusted" else "trusted",
        }

    def ask(
        self,
        task_class: str,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        output_schema: Mapping[str, Any] | None = None,
        privacy: str | None = None,
        deadline_s: float | None = None,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self._check()
        if self.host.gateway is None:
            raise ModelUnavailable("unavailable.model", "this cell has no model gateway")
        budgets = self.agent.budgets
        if "model_calls_per_day" in budgets and self.host.calls_today(self.agent.id) >= int(
            budgets["model_calls_per_day"]
        ):
            raise ModelUnavailable("budget.exceeded", f"{self.agent.id} has used its model calls for today")
        default_privacy, routing = self.agent.privacy()
        left = self.deadline - time.monotonic()
        req: dict[str, Any] = {
            "requested_by": self.instance,
            "task_class": task_class,
            "privacy": privacy or default_privacy,
            "messages": messages,
            "deadline_s": max(1.0, min(deadline_s or left, left)),
            "correlationid": self.correlationid,
        }
        if routing:
            req["routing"] = routing
        if system:
            req["system"] = system
        if output_schema:
            req["output_schema"] = dict(output_schema)
        if max_tokens:
            req["max_tokens"] = max_tokens
        try:
            result = self.host.gateway.complete(req, cancel=self.cancel)
        except ModelUnavailable:
            self._check()  # a stop shows as AgentStopped, not as an outage
            raise
        finally:
            self.host.count_call(self.agent.id)
        self.model_calls += 1
        self.tokens += int(result.usage.get("prompt_tokens", 0)) + int(
            result.usage.get("completion_tokens", 0)
        )
        return result

    def propose(
        self,
        action_type: str,
        target: str,
        params: Mapping[str, Any],
        *,
        rationale: str,
        expected_effect: Mapping[str, Any],
        verification: Mapping[str, Any],
        reversibility: Mapping[str, Any],
        inputs: list[ToolResult] | None = None,
        objective: str | None = None,
        freshness_s: int = 3600,
    ) -> dict[str, Any]:
        self._check()
        if self.agent.section.get("quarantined"):
            raise AgentNotAllowed(f"{self.agent.id} is quarantined: it reads, it does not propose")
        allowed = (self.agent.manifest.get("capabilities") or {}).get("proposes_actions") or []
        if action_type not in allowed:
            raise AgentNotAllowed(f"{self.agent.id} may not propose {action_type}")
        budgets = self.agent.budgets
        if "proposals_per_day" in budgets and self.host.proposals_today(self.agent.id) >= int(
            budgets["proposals_per_day"]
        ):
            raise AgentNotAllowed(f"{self.agent.id} has used its proposals for today")
        now = self.host.clock()
        pid = prefixed_id("prp", now)
        proposal = {
            "proposal_id": pid,
            "proposer": self.instance,
            "objective": {
                "id": f"obj_{self.agent.id.split(':', 1)[-1].replace('-', '_')}",
                "text": (objective or str(self.trigger.get("question") or "agent run"))[:500],
            },
            "action": {"type": action_type, "target": target, "params": dict(params)},
            "rationale": rationale[:2000],
            "inputs": [
                {
                    k: v
                    for k, v in {
                        "ref": r.ref or f"tool:{r.tool}",
                        "trust": "untrusted" if r.trust == "untrusted" else "trusted",
                        "observed_at": r.observed_at or None,
                    }.items()
                    if v
                }
                for r in (inputs or [])
            ],
            "expected_effect": dict(expected_effect),
            "verification": dict(verification),
            "reversibility": dict(reversibility),
            "freshness_s": freshness_s,
            "idempotency_key": f"{self.agent.id}:{pid}",
        }
        self.host.count_proposal(self.agent.id)
        outcome = self.host.submit(proposal) if self.host.submit is not None else {"status": "not submitted"}
        self.proposals.append({"proposal": proposal, "outcome": outcome})
        return outcome


class AgentHost:
    def __init__(
        self,
        *,
        gateway: ModelGateway | None,
        tools: Mapping[str, Callable[..., Any]] | None = None,
        tool_trust: Mapping[str, str] | None = None,
        submit: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        withdraw: Callable[[str], None] | None = None,
        suspended: Callable[[str], bool] = lambda agent: False,
        cell: str = "local",
        clock: Callable[[], float] = time.time,
        audit: Any = None,
        tracer: Any = None,
        poll_s: float = 0.5,
    ):
        self.gateway = gateway
        self.tools = dict(tools or {})
        self.tool_trust = dict(tool_trust or {})
        self.submit = submit
        self.withdraw = withdraw
        self.suspended = suspended
        self.cell = cell
        self.clock = clock
        self.audit = audit
        self.tracer = tracer
        self.poll_s = poll_s
        self.agents: dict[str, _Agent] = {}
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, str, str], int] = {}  # (agent, day, what) -> n

    def register(self, manifest: Mapping[str, Any], run: Callable[[AgentContext], Any]) -> None:
        if manifest.get("kind") != "agent" or not str(manifest.get("component_id", "")).startswith("agent:"):
            raise ValueError("only agents (kind agent, component_id agent:…) run in the agent host")
        caps = manifest.get("capabilities") or {}
        if (manifest.get("agent") or {}).get("quarantined") and caps.get("proposes_actions"):
            raise ValueError("a quarantined agent cannot propose actions")
        self.agents[str(manifest["component_id"])] = _Agent(manifest, run)

    # daily counters
    def _day(self) -> str:
        return to_rfc3339(self.clock())[:10]

    def _bump(self, agent: str, what: str) -> None:
        with self._lock:
            k = (agent, self._day(), what)
            self._counts[k] = self._counts.get(k, 0) + 1

    def count_call(self, agent: str) -> None:
        self._bump(agent, "calls")

    def count_proposal(self, agent: str) -> None:
        self._bump(agent, "proposals")

    def calls_today(self, agent: str) -> int:
        return self._counts.get((agent, self._day(), "calls"), 0)

    def proposals_today(self, agent: str) -> int:
        return self._counts.get((agent, self._day(), "proposals"), 0)

    def run(self, agent_id: str, trigger: Mapping[str, Any] | None = None) -> RunResult:
        agent = self.agents.get(agent_id)
        if agent is None:
            raise KeyError(f"no agent {agent_id}")
        instance = f"{agent_id}@{self.cell}"
        if self.suspended(agent_id):
            self._audit("agent.suspended", agent_id, f"{agent_id} is suspended; run skipped", None)
            return RunResult(agent_id, "suspended")
        budgets = agent.budgets
        tokens_limit = budgets.get("tokens_per_day")
        if (
            tokens_limit is not None
            and self.gateway is not None
            and self.gateway.spent(agent_id)[0] >= int(tokens_limit)
        ):
            self._audit(
                "agent.budget_exceeded",
                agent_id,
                f"{agent_id} has used its {tokens_limit} tokens for today",
                None,
            )
            return RunResult(agent_id, "budget_exceeded")
        cancel = threading.Event()
        ctx = AgentContext(self, agent, instance, trigger or {}, cancel)
        run_s = float(budgets.get("run_seconds", 300))
        ctx.deadline = time.monotonic() + run_s
        stop_reason: list[str] = []
        done = threading.Event()

        def watch() -> None:
            while not done.wait(self.poll_s):
                if self.suspended(agent_id):
                    stop_reason.append("suspended")
                    cancel.set()
                    if self.withdraw is not None:
                        with contextlib.suppress(Exception):
                            self.withdraw(agent_id)
                    return
                if time.monotonic() > ctx.deadline:
                    stop_reason.append("out of time")
                    cancel.set()
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        span = (
            self.tracer.span("agent.run", **{"autonomy.agent": agent_id})
            if self.tracer is not None
            else contextlib.nullcontext()
        )
        status, output, error = "ok", None, ""
        with span:
            self._audit(
                "agent.started", agent_id, f"run started ({_trigger_note(trigger)})", ctx.correlationid
            )
            try:
                output = agent.run(ctx)
                if cancel.is_set():
                    status = "suspended" if stop_reason[:1] == ["suspended"] else "stopped"
            except AgentStopped:
                status = "suspended" if stop_reason[:1] == ["suspended"] else "stopped"
            except Exception as exc:  # noqa: BLE001 - the host survives its agents
                status, error = "error", f"{type(exc).__name__}: {exc}"[:500]
            finally:
                done.set()
        result = RunResult(agent_id, status, output, ctx.proposals, ctx.model_calls, ctx.tokens, error)
        self._audit(
            "agent.suspended" if status == "suspended" else "agent.finished",
            agent_id,
            f"run {status}: {ctx.model_calls} model call(s), {ctx.tokens} tokens, "
            f"{len(ctx.proposals)} proposal(s)"
            + (f"; {error}" if error else "")
            + (f"; {stop_reason[0]}" if stop_reason else ""),
            ctx.correlationid,
        )
        return result

    def _audit(self, kind: str, agent: str, summary: str, corr: str | None) -> None:
        if self.audit is None:
            return
        self.audit.append(
            kind,
            summary=summary[:1000],
            correlationid=corr or f"corr_{agent.split(':', 1)[-1].replace('-', '_')}",
            refs={"agent": agent},
        )


def _trigger_note(trigger: Mapping[str, Any] | None) -> str:
    if not trigger:
        return "no trigger"
    return ", ".join(sorted(k for k in trigger if k != "correlationid")) or "trigger"


# --- C13's LLM review on the model gateway ---------------------------------------------------

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["verdict", "note"],
    "properties": {"verdict": {"enum": ["ok", "escalate"]}, "note": {"type": "string", "maxLength": 500}},
}

REVIEW_SYSTEM = (
    "You review a proposed action for a home and farm automation system before it runs. "
    'Deterministic checks have already passed. Answer verdict "escalate" if the rationale does '
    "not follow from the inputs, the action looks unsafe, surprising or out of proportion, or you "
    'are unsure; "ok" only if it is clearly sensible. Your answer cannot approve anything: '
    '"escalate" sends it to the owner. Keep the note to one sentence.'
)


class ModelReviewer:
    """The evaluator's reviewer hook (C13) on the model gateway. Escalate-only (ADR-008):
    no model → ``ReviewUnavailable`` (skipped, ST-03); a bad answer → escalate."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        requested_by: str = "svc:evaluator",
        task_class: str = "review",
        privacy: str = "private",
        deadline_s: float = 30.0,
    ):
        self.gateway = gateway
        self.requested_by = requested_by
        self.task_class = task_class
        self.privacy = privacy
        self.deadline_s = deadline_s

    def __call__(self, proposal: Mapping[str, Any], checks: list[dict[str, Any]]) -> Review:
        summary = {
            k: proposal.get(k)
            for k in (
                "proposer",
                "objective",
                "action",
                "rationale",
                "inputs",
                "expected_effect",
                "reversibility",
            )
        }
        messages = [
            {
                "role": "user",
                "content": "Proposal:\n"
                + json.dumps(summary, indent=1, default=str)
                + "\n\nDeterministic checks:\n"
                + json.dumps(checks, indent=1),
            },
        ]
        try:
            result = self.gateway.complete(
                {
                    "requested_by": self.requested_by,
                    "task_class": self.task_class,
                    "privacy": self.privacy,
                    "system": REVIEW_SYSTEM,
                    "messages": messages,
                    "output_schema": REVIEW_SCHEMA,
                    "max_tokens": 200,
                    "temperature": 0,
                    "deadline_s": self.deadline_s,
                }
            )
        except ModelUnavailable as e:
            if e.code in ("unavailable.model.invalid_output",):
                return Review("escalate", "the reviewer's answer was not usable", None)
            raise ReviewUnavailable(f"no model ({e.code})") from None
        data = result.data if isinstance(result.data, dict) else {}
        return Review(
            str(data.get("verdict", "escalate")),
            str(data.get("note", ""))[:500],
            f"{result.endpoint}:{result.model}",
        )
