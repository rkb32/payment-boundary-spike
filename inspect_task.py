"""The 25 boundary cases as an Inspect (inspect_ai) task.

The "model" is never called: the solver runs the scripted agent for each case
and the scorer checks the verdict. Run with the built-in mock model, no API key:

    inspect eval inspect_task.py --model mockllm/model
"""
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer
from inspect_ai.solver import Generate, TaskState, solver

import grade

_BY_ID = {c.id: c for c in grade.CASES}


@solver
def run_boundary_case():
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        c = _BY_ID[state.sample_id]
        verdict, report, n = grade.run_case(c.agent, c.faults, c.approval, c.tamper)
        state.output.completion = verdict
        state.metadata.update(told_user=report.status, records=n, verdict=verdict)
        return state
    return solve


@scorer(metrics=[accuracy()])
def verdict_matches():
    async def score(state: TaskState, target: Target) -> Score:
        got = state.metadata["verdict"]
        return Score(value=CORRECT if got == target.text else INCORRECT, answer=got)
    return score


@task
def payment_boundaries():
    return Task(
        dataset=[Sample(id=c.id, input=c.id, target=c.expected) for c in grade.CASES],
        solver=run_boundary_case(),
        scorer=verdict_matches(),
    )
