import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.memory import NEMO_TOOL_REGISTRY
from nemo_coding_platform.mcp_server import mcp_tool_definitions
from nemo_coding_platform.nemocode_mcp_tools import mcp_call_nemo_tool, mcp_get_self_mod_continuity, mcp_record_self_mod_decision


class MCPServerToolSurfaceTests(unittest.TestCase):
    def test_tool_definitions_include_all_nemo_registry_tools(self) -> None:
        names = {definition["name"] for definition in mcp_tool_definitions()}

        for tool in NEMO_TOOL_REGISTRY:
            self.assertIn(f"nemocode.{tool.name}", names)

        self.assertIn("nemocode.run_headless", names)
        self.assertIn("nemocode.list_runs", names)
        self.assertIn("nemocode.get_run_result", names)
        self.assertIn("nemocode.record_self_mod_decision", names)
        self.assertIn("nemocode.record_self_mod_feedback", names)
        self.assertIn("nemocode.learn_from_self_mod_failure", names)
        self.assertIn("nemocode.mark_portfolio_effective", names)
        self.assertIn("nemocode.get_self_mod_continuity", names)
        self.assertIn("nemocode.query_self_mod_risk_patterns", names)

    def test_generic_mcp_nemo_tool_call_round_trips_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory_db = str(Path(tmp) / "memory.sqlite")
            write_result = mcp_call_nemo_tool(
                "store_architectural_decision",
                lifecycle_phase="review",
                memory_db=memory_db,
                decision="MCP generic dispatcher stores decisions.",
                topic="mcp",
            )
            search_result = mcp_call_nemo_tool(
                "search_memories",
                lifecycle_phase="review",
                memory_db=memory_db,
                query="dispatcher",
                topic="mcp",
            )

        self.assertTrue(write_result["ok"])
        self.assertTrue(search_result["ok"])
        memories = search_result["payload"]["memories"]
        self.assertEqual(len(memories), 1)
        self.assertIn("dispatcher", memories[0]["content"])

    def test_self_mod_learning_mcp_wrappers_round_trip_continuity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory_db = str(Path(tmp) / "memory.sqlite")
            decision = mcp_record_self_mod_decision(
                memory_db=memory_db,
                objective="Improve continuity retrieval",
                chosen_path="persist decision atoms",
                rationale="Future MCP self-mod calls need prior choices.",
                task_type="tool_expansion",
            )
            continuity = mcp_get_self_mod_continuity(memory_db=memory_db, task_objective="continuity retrieval", task_type="tool_expansion")

        self.assertTrue(decision["ok"])
        self.assertTrue(continuity["ok"])
        self.assertEqual(continuity["count"], 1)
        self.assertIn("persist decision atoms", continuity["items"][0]["content"])


if __name__ == "__main__":
    unittest.main()
