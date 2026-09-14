"""Sub-Agent Hierarchy & Teamwork Orchestration for SOVA.

Provides role definitions, tool filtering, iteration budgets, and role-tuned
system prompts for specialized sub-agents (Researcher, Coder, Reviewer, General).
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


ROLE_RESEARCHER = "researcher"
ROLE_CODER = "coder"
ROLE_REVIEWER = "reviewer"
ROLE_GENERAL = "general"

VALID_ROLES = {ROLE_RESEARCHER, ROLE_CODER, ROLE_REVIEWER, ROLE_GENERAL}

# Strictly read-only tools for the Researcher role
RESEARCHER_TOOLS: Set[str] = {
    "read_file",
    "list_dir",
    "find_files",
    "grep",
    "get_outline",
    "find_definition",
    "find_references",
    "workspace_summary",
    "search_code",
    "memory_read",
    "todo_read",
    "finish",
}

# Precision editing and verification tools for the Coder role
CODER_TOOLS: Set[str] = {
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "find_files",
    "grep",
    "search_code",
    "run_tests",
    "run_shell",
    "shell_output",
    "get_outline",
    "find_definition",
    "find_references",
    "workspace_summary",
    "todo_write",
    "todo_read",
    "undo",
    "finish",
}

# Quality verification and test execution tools for the Reviewer role
REVIEWER_TOOLS: Set[str] = {
    "read_file",
    "list_dir",
    "find_files",
    "grep",
    "search_code",
    "run_tests",
    "get_outline",
    "find_definition",
    "find_references",
    "workspace_summary",
    "run_shell",
    "shell_output",
    "finish",
}

RESEARCHER_PROMPT = """You are SOVA Researcher, a specialized read-only research sub-agent.
Your goal is to thoroughly explore and analyze the codebase to answer questions, find implementations, trace symbol usages, or map architecture.
You have strictly READ-ONLY tools. You CANNOT edit or write files, and you CANNOT run arbitrary shell commands.
Use `grep`, `find_files`, `get_outline`, `find_definition`, `find_references`, and `read_file` to gather precise facts.
When done, call `finish(summary=...)` with a concise, structured, and factual summary of your findings."""

CODER_PROMPT = """You are SOVA Coder, a precision code editing and implementation sub-agent.
Your goal is to implement, refactor, or fix code according to the given subtask.
Always inspect files with `read_file` before editing. Use `edit_file` for targeted changes or `write_file` for new files.
You can run local tests using `run_shell` to verify your changes.
If a change introduces errors, fix them or use `undo` if needed.
When the task is complete and verified, call `finish(summary=...)` describing what was changed and verified."""

REVIEWER_PROMPT = """You are SOVA Reviewer, a code review and quality verification sub-agent.
Your goal is to review code modifications, inspect diffs, verify correctness, and run test suites to ensure zero regressions.
You have read-only and shell execution tools for running test suites. You do NOT make code edits yourself.
Inspect code quality, edge cases, and test results.
When done, call `finish(summary=...)` summarizing the review findings, test results, and any remaining risks or approvals."""

GENERAL_PROMPT = """You are a SOVA Sub-Agent.
Complete the specified subtask efficiently using the available tools.
When the subtask is done, call `finish(summary=...)` with a concise summary of the outcome."""


@dataclass(frozen=True)
class RoleConfig:
    role: str
    name: str
    icon: str
    color: str
    max_iterations: int
    allowed_tools: Optional[Set[str]]
    system_prompt: str


_ROLE_CONFIGS: Dict[str, RoleConfig] = {
    ROLE_RESEARCHER: RoleConfig(
        role=ROLE_RESEARCHER,
        name="Researcher",
        icon="🔬",
        color="#06b6d4",
        max_iterations=8,
        allowed_tools=RESEARCHER_TOOLS,
        system_prompt=RESEARCHER_PROMPT,
    ),
    ROLE_CODER: RoleConfig(
        role=ROLE_CODER,
        name="Coder",
        icon="⚡",
        color="#10b981",
        max_iterations=10,
        allowed_tools=CODER_TOOLS,
        system_prompt=CODER_PROMPT,
    ),
    ROLE_REVIEWER: RoleConfig(
        role=ROLE_REVIEWER,
        name="Reviewer",
        icon="🔍",
        color="#a855f7",
        max_iterations=6,
        allowed_tools=REVIEWER_TOOLS,
        system_prompt=REVIEWER_PROMPT,
    ),
    ROLE_GENERAL: RoleConfig(
        role=ROLE_GENERAL,
        name="General",
        icon="🤖",
        color="#3b82f6",
        max_iterations=8,
        allowed_tools=None,
        system_prompt=GENERAL_PROMPT,
    ),
}


def get_role_config(role: Optional[str]) -> RoleConfig:
    """Return the RoleConfig for a role name, falling back to General."""
    if not role:
        return _ROLE_CONFIGS[ROLE_GENERAL]
    normalized = role.strip().lower()
    return _ROLE_CONFIGS.get(normalized, _ROLE_CONFIGS[ROLE_GENERAL])


def filter_tools_for_role(
    schemas: List[dict],
    impls: dict,
    allowed_tools: Optional[Set[str]],
) -> Tuple[List[dict], dict]:
    """Filter tool schemas and implementations based on role permissions.

    Subagents are never allowed to spawn child subagents (enforcing Max Depth = 1).
    """
    if allowed_tools is None:
        # General role: allow all tools except spawn_subagent
        filtered_schemas = [
            s for s in schemas
            if s.get("function", {}).get("name") != "spawn_subagent"
        ]
        filtered_impls = {
            k: v for k, v in impls.items()
            if k != "spawn_subagent"
        }
        return filtered_schemas, filtered_impls

    # Filter to explicitly allowed tools
    filtered_schemas = [
        s for s in schemas
        if s.get("function", {}).get("name") in allowed_tools
    ]
    filtered_impls = {
        k: v for k, v in impls.items()
        if k in allowed_tools or k == "_checkpoints"
    }
    return filtered_schemas, filtered_impls
