import unittest
import threading
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from nemo_coding_platform.core.nemo_adapter import McpNemoAdapter
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase


class MockMCPServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"event: endpoint\ndata: /messages\n\n")
        self.wfile.flush()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(content_length)

        # McpNemoAdapter POSTs to /api/tools/{tool_name} (REST, not jsonrpc)
        tool_name = self.path.rstrip("/").split("/")[-1]
        if tool_name == "prime_context":
            response = {"context": "mocked-context", "topic": "test"}
        else:
            response = {"error": "Unknown tool"}

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode("utf-8"))


class TestMcpAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockMCPServer)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_mcp_adapter_call(self):
        url = f"http://127.0.0.1:{self.port}/sse"
        adapter = McpNemoAdapter(url)
        
        _, result = adapter.call(NemoLifecyclePhase.START, "prime_context", topic="test")
        
        self.assertTrue(result.ok)
        self.assertEqual(result.payload["context"], "mocked-context")


if __name__ == "__main__":
    unittest.main()
