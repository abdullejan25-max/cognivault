"""Execute a trusted finite dependency plan, never permissions from model text."""
from dataclasses import dataclass, field
from copy import deepcopy


@dataclass(frozen=True)
class PlanStep:
    task: object = field(repr=False)
    depends_on: tuple[str, ...] = ()
    agent_id: str | None = None


@dataclass(frozen=True)
class DependencyReceipt:
    state: str = 'BLOCKED'
    error_code: str = 'DEPENDENCY_NOT_COMPLETED'


@dataclass(frozen=True)
class PlanReceipt:
    state: str
    tasks: dict


async def run_plan(engine, steps, *, cancel=None):
    """Topological sequential execution; child task journals provide replay safety."""
    from .models import Task, identifier
    if type(steps) not in (list, tuple) or not 1 <= len(steps) <= 64:
        raise ValueError('INVALID_PLAN')
    nodes = {}
    for step in steps:
        if (type(step) is not PlanStep or type(step.task) is not Task
                or step.task.task_id in nodes or type(step.depends_on) is not tuple
                or not all(identifier(ref) for ref in step.depends_on)
                or len(set(step.depends_on)) != len(step.depends_on)
                or step.agent_id is not None and not identifier(step.agent_id)):
            raise ValueError('INVALID_PLAN')
        step.task.validate()
        nodes[step.task.task_id] = step
    ordered, remaining = [], dict(nodes)
    while remaining:
        ready = [key for key, step in remaining.items() if set(step.depends_on) <= set(ordered)]
        if not ready:
            raise ValueError('INVALID_PLAN')
        for key in ready:
            ordered.append(key)
            remaining.pop(key)
    nodes = deepcopy(nodes)  # Bind all task contexts before the first execution.
    receipts = {}
    for key in ordered:
        step = nodes[key]
        if any(receipts[ref].state != 'COMPLETED' for ref in step.depends_on):
            receipts[key] = DependencyReceipt()
        else:
            receipts[key] = await engine.run(step.task, step.agent_id, cancel=cancel)
    states = [r.state for r in receipts.values()]
    if all(state == 'COMPLETED' for state in states):
        state = 'COMPLETED'
    elif 'PARTIAL' in states or 'COMPLETED' in states:
        state = 'PARTIAL'
    elif all(state == 'WAITING_FOR_REVIEW' for state in states):
        state = 'WAITING_FOR_REVIEW'
    elif any(getattr(row, 'verified', False) for row in receipts.values()):
        state = 'PARTIAL'  # A pending candidate may already have been saved.
    elif 'FAILED' in states or 'RETRYABLE_FAILURE' in states:
        state = 'FAILED'
    elif 'WAITING_FOR_REVIEW' in states:
        state = 'WAITING_FOR_REVIEW'
    elif 'CANCELLED' in states:
        state = 'CANCELLED'
    else:
        state = 'BLOCKED'
    return PlanReceipt(state, receipts)
