import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent.loop import _SPAWN_SUBAGENT_SCHEMA, run_agent
from agent.subagents import (
    CODER_TOOLS,
    RESEARCHER_TOOLS,
    REVIEWER_TOOLS,
    ROLE_CODER,
    ROLE_GENERAL,
    ROLE_RESEARCHER,
    ROLE_REVIEWER,
    filter_tools_for_role,
    get_role_config,
)
from agent.tools import build_tools


class TestSubagents(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_role_configs(self):
        """Verify role configurations and defaults."""
        researcher = get_role_config("researcher")
        self.assertEqual(researcher.role, ROLE_RESEARCHER)
        self.assertEqual(researcher.max_iterations, 8)
        self.assertEqual(researcher.icon, "🔬")

        coder = get_role_config("coder")
        self.assertEqual(coder.role, ROLE_CODER)
        self.assertEqual(coder.max_iterations, 10)
        self.assertEqual(coder.icon, "⚡")

        reviewer = get_role_config("reviewer")
        self.assertEqual(reviewer.role, ROLE_REVIEWER)
        self.assertEqual(reviewer.max_iterations, 6)
        self.assertEqual(reviewer.icon, "🔍")

        # Unknown or None role falls back to general
        general = get_role_config("unknown_role")
        self.assertEqual(general.role, ROLE_GENERAL)
        none_cfg = get_role_config(None)
        self.assertEqual(none_cfg.role, ROLE_GENERAL)

        # Case-insensitive
        upper_cfg = get_role_config("RESEARCHER")
        self.assertEqual(upper_cfg.role, ROLE_RESEARCHER)

    def test_researcher_tool_restrictions(self):
        """Researcher must strictly have zero write, edit, or shell execution tools."""
        forbidden = {"write_file", "edit_file", "run_shell", "shell_output", "undo"}
        self.assertTrue(forbidden.isdisjoint(RESEARCHER_TOOLS))

        base_schemas, base_impls = build_tools(self.temp_dir)
        r_schemas, r_impls = filter_tools_for_role(base_schemas, base_impls, RESEARCHER_TOOLS)

        r_schema_names = {s["function"]["name"] for s in r_schemas}
        r_impl_names = set(r_impls.keys())

        # Assert no sensitive tools in researcher
        for tool in forbidden:
            self.assertNotIn(tool, r_schema_names)
            self.assertNotIn(tool, r_impl_names)

        # Assert read tools present
        for expected in ["read_file", "list_dir", "find_files", "grep", "finish"]:
            self.assertIn(expected, r_schema_names)
            self.assertIn(expected, r_impl_names)

    def test_coder_tool_permissions(self):
        """Coder must have write, edit, and testing tools."""
        base_schemas, base_impls = build_tools(self.temp_dir)
        c_schemas, c_impls = filter_tools_for_role(base_schemas, base_impls, CODER_TOOLS)

        c_schema_names = {s["function"]["name"] for s in c_schemas}
        for expected in ["read_file", "write_file", "edit_file", "run_shell", "undo", "finish"]:
            self.assertIn(expected, c_schema_names)
            self.assertIn(expected, c_impls)

    def test_reviewer_tool_permissions(self):
        """Reviewer has inspection and test execution tools, but no file editing."""
        base_schemas, base_impls = build_tools(self.temp_dir)
        rev_schemas, rev_impls = filter_tools_for_role(base_schemas, base_impls, REVIEWER_TOOLS)

        rev_schema_names = {s["function"]["name"] for s in rev_schemas}
        self.assertIn("read_file", rev_schema_names)
        self.assertIn("run_shell", rev_schema_names)
        self.assertIn("grep", rev_schema_names)

        # Cannot edit or write files
        self.assertNotIn("write_file", rev_schema_names)
        self.assertNotIn("edit_file", rev_schema_names)

    def test_recursion_prevention(self):
        """Subagents must never be given spawn_subagent (Depth = 1 limit)."""
        base_schemas, base_impls = build_tools(self.temp_dir)
        # Add spawn_subagent to base
        dummy_schemas = base_schemas + [_SPAWN_SUBAGENT_SCHEMA]
        dummy_impls = {**base_impls, "spawn_subagent": lambda: None}

        # Check all roles
        for role_name in ["researcher", "coder", "reviewer", "general"]:
            cfg = get_role_config(role_name)
            filt_schemas, filt_impls = filter_tools_for_role(dummy_schemas, dummy_impls, cfg.allowed_tools)
            filt_names = {s["function"]["name"] for s in filt_schemas}
            self.assertNotIn("spawn_subagent", filt_names)
            self.assertNotIn("spawn_subagent", filt_impls)

    def test_spawn_subagent_schema(self):
        """Verify schema properties for spawn_subagent tool call."""
        fn = _SPAWN_SUBAGENT_SCHEMA["function"]
        self.assertEqual(fn["name"], "spawn_subagent")
        props = fn["parameters"]["properties"]
        self.assertIn("subtask", props)
        self.assertIn("role", props)
        self.assertEqual(props["role"]["enum"], ["researcher", "coder", "reviewer", "general"])
        self.assertEqual(fn["parameters"]["required"], ["subtask"])

    def test_subagent_events_emission(self):
        """Test that running an agent that calls spawn_subagent emits start, enriched child events, and finish."""
        events = []

        def on_event(ev):
            events.append(ev)

        # Build tools with allow_subagents=True
        # Simulate subagent run by mocking run_agent for the child
        with patch("agent.loop._safe_chat") as mock_chat:
            def make_resp(tool_name, tool_args, content=""):
                call = MagicMock()
                call.id = f"call_{tool_name}"
                call.function = MagicMock()
                call.function.name = tool_name
                call.function.arguments = tool_args
                msg = MagicMock()
                msg.content = content
                msg.tool_calls = [call]
                msg.model_dump.return_value = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [{
                        "id": call.id,
                        "type": "function",
                        "function": {"name": tool_name, "arguments": tool_args}
                    }]
                }
                choice = MagicMock()
                choice.message = msg
                resp = MagicMock()
                resp.choices = [choice]
                return resp

            first_resp = make_resp("spawn_subagent", '{"subtask": "Search for models", "role": "researcher"}', "Delegating.")
            child_finish_resp = make_resp("finish", '{"summary": "Child completed research."}', "Done child.")
            parent_finish_resp = make_resp("finish", '{"summary": "Parent complete."}', "Done parent.")

            mock_chat.side_effect = [
                (first_resp, None, False),
                (child_finish_resp, None, False),
                (parent_finish_resp, None, False),
            ]

            result = run_agent(
                self.temp_dir,
                "Explore models",
                on_event=on_event,
                allow_subagents=True,
                verbose=False,
                force_task=True,
            )

            # Check that subagent_start was emitted
            start_events = [e for e in events if e.get("type") == "subagent_start"]
            self.assertTrue(len(start_events) >= 1)
            self.assertEqual(start_events[0]["role"], "researcher")
            self.assertEqual(start_events[0]["name"], "Researcher")
            self.assertEqual(start_events[0]["icon"], "🔬")
            self.assertEqual(start_events[0]["task"], "Search for models")

            # Check that subagent_finish was emitted
            finish_events = [e for e in events if e.get("type") == "subagent_finish"]
            self.assertTrue(len(finish_events) >= 1)
            self.assertEqual(finish_events[0]["role"], "researcher")


if __name__ == "__main__":
    unittest.main()
