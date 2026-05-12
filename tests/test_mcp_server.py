import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

from nemo_coding_platform.core.memory import NEMO_TOOL_REGISTRY
from nemo_coding_platform.mcp_server import MCPServerHandler, mcp_tool_definitions, tool_policy_decision
from nemo_coding_platform.spacecode_mcp_tools import mcp_call_nemo_tool, mcp_get_self_mod_continuity, mcp_record_self_mod_decision


class MCPServerToolSurfaceTests(unittest.TestCase):
    def test_tool_definitions_include_all_nemo_registry_tools(self) -> None:
        names = {definition["name"] for definition in mcp_tool_definitions()}

        for tool in NEMO_TOOL_REGISTRY:
            self.assertIn(f"spacecode.{tool.name}", names)

        self.assertIn("spacecode.run_headless", names)
        self.assertIn("spacecode.list_runs", names)
        self.assertIn("spacecode.get_run_result", names)
        self.assertIn("spacecode.record_self_mod_decision", names)
        self.assertIn("spacecode.record_self_mod_feedback", names)
        self.assertIn("spacecode.learn_from_self_mod_failure", names)
        self.assertIn("spacecode.mark_portfolio_effective", names)
        self.assertIn("spacecode.get_self_mod_continuity", names)
        self.assertIn("spacecode.query_self_mod_risk_patterns", names)

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

    def test_policy_requires_approval_for_destructive_static_tool(self) -> None:
        decision = tool_policy_decision("spacecode.self_mod_apply", {})

        self.assertEqual(decision["risk"], "destructive")
        self.assertTrue(decision["approval_required"])
        self.assertFalse(decision["allowed"])

    def test_policy_requires_approval_for_destructive_nemo_tool(self) -> None:
        decision = tool_policy_decision("spacecode.delete_reminder", {})

        self.assertEqual(decision["risk"], "destructive")
        self.assertTrue(decision["approval_required"])
        self.assertFalse(decision["allowed"])

    def test_policy_allows_when_approval_explicitly_granted(self) -> None:
        decision = tool_policy_decision("spacecode.delete_reminder", {"approve_review": True})

        self.assertTrue(decision["approval_required"])
        self.assertTrue(decision["approved"])
        self.assertTrue(decision["allowed"])


class MCPServerJsonRpcPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MCPServerHandler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_tools_call_denied_without_approval_returns_policy_error(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": "deny-1",
            "method": "tools/call",
            "params": {
                "name": "spacecode.delete_reminder",
                "arguments": {},
            },
        }

        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/mcp/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))

        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], -32003)
        audit = result["error"]["data"]["tool_call_audit"]
        self.assertEqual(audit["tool"], "spacecode.delete_reminder")
        self.assertEqual(audit["risk"], "destructive")
        self.assertFalse(audit["allowed"])

    def test_denied_risky_tool_call_persists_audit_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit_log = Path(tmp) / "mcp-audit.jsonl"
            original = os.environ.get("SPACE_CODE_MCP_AUDIT_LOG")
            os.environ["SPACE_CODE_MCP_AUDIT_LOG"] = str(audit_log)
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": "deny-2",
                    "method": "tools/call",
                    "params": {
                        "name": "spacecode.self_mod_apply",
                        "arguments": {"run_json": "fake-run.json"},
                    },
                }
                request = urllib.request.Request(
                    f"http://127.0.0.1:{self.port}/mcp/messages",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    result = json.loads(response.read().decode("utf-8"))

                self.assertEqual(result["error"]["code"], -32003)
                self.assertTrue(audit_log.exists())
                lines = [line for line in audit_log.read_text(encoding="utf-8").splitlines() if line.strip()]
                self.assertEqual(len(lines), 1)
                entry = json.loads(lines[0])
                self.assertEqual(entry["request_id"], "deny-2")
                self.assertEqual(entry["outcome"], "denied")
                self.assertEqual(entry["tool_call_audit"]["tool"], "spacecode.self_mod_apply")
                self.assertFalse(entry["tool_call_audit"]["allowed"])
            finally:
                if original is None:
                    os.environ.pop("SPACE_CODE_MCP_AUDIT_LOG", None)
                else:
                    os.environ["SPACE_CODE_MCP_AUDIT_LOG"] = original


if __name__ == "__main__":
    unittest.main()
