"""Role definitions: system prompt, allowed tools and finish schema per role."""

from __future__ import annotations

from dataclasses import dataclass

READ_ONLY = ("read_file", "list_dir", "grep", "run_tests")
ALL_TOOLS = ("read_file", "list_dir", "grep", "write_file", "apply_patch",
             "run_command", "run_tests", "git_diff")

_str = {"type": "string"}

COMMON = """You are an autonomous software engineer working inside a git worktree of a real \
repository. Paths are relative to the repository root. Commands run in a sandbox with no \
network access. You cannot push, open PRs or touch .git; the orchestrator handles that.

Work like a careful senior engineer: read the relevant code before changing it, make the \
smallest change that fully solves the problem at its root cause, follow the existing style, \
and verify with the test suite. Do not modify tests to make them pass unless the task is \
about the tests themselves. When you are done, call the `finish` tool - that is the only way \
your work is recorded."""


@dataclass(frozen=True)
class Role:
    name: str
    system: str
    tools: tuple[str, ...]
    finish_description: str
    finish_schema: dict


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


PLANNER = Role(
    "planner",
    COMMON + """

Your role: PLANNER. Explore the codebase and break the task into 1-4 independent subtasks \
that separate engineers can do in parallel on separate branches. Subtasks must not edit the \
same lines; if the task is small or tightly coupled, return exactly one subtask. Each \
description must be self-contained: name the files, functions and the expected behaviour. \
Do not edit any files.""",
    READ_ONLY,
    "Submit the plan.",
    _schema({"summary": _str,
             "subtasks": {"type": "array", "items": _schema(
                 {"title": _str, "description": _str}, ["title", "description"])}},
            ["summary", "subtasks"]),
)

WORKER = Role(
    "worker",
    COMMON + """

Your role: WORKER. Implement exactly your assigned subtask. Run the tests before finishing. \
In `finish`, summarize what you changed and why, and report the final test result honestly.""",
    ALL_TOOLS,
    "Report that the subtask is complete.",
    _schema({"summary": _str, "tests_passed": {"type": "boolean"}}, ["summary", "tests_passed"]),
)

REVIEWER = Role(
    "reviewer",
    COMMON + """

Your role: REVIEWER. You are given a diff produced by another engineer for a subtask. Check \
it for correctness, root-cause fixes (not symptom patches), regressions, missing edge cases, \
unrelated changes and test results. You may read files and run the tests but must not edit \
anything. Approve if the change is correct and complete; otherwise request changes with \
specific, actionable feedback.""",
    READ_ONLY + ("git_diff",),
    "Submit the review verdict.",
    _schema({"verdict": {"type": "string", "enum": ["approve", "request_changes"]},
             "feedback": _str}, ["verdict", "feedback"]),
)

INTEGRATOR = Role(
    "integrator",
    COMMON + """

Your role: INTEGRATOR. Several subtask branches are being merged into one branch. Resolve \
merge conflicts (remove every conflict marker, keeping the intent of both sides) and/or fix \
integration test failures. Keep changes minimal. Run the full test suite before finishing.""",
    ALL_TOOLS,
    "Report that integration is complete.",
    _schema({"summary": _str, "tests_passed": {"type": "boolean"}}, ["summary", "tests_passed"]),
)

ROLES = {r.name: r for r in (PLANNER, WORKER, REVIEWER, INTEGRATOR)}
