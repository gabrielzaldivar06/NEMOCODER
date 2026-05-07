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
        body = json.loads(self.rfile.read(content_length).decode("utf-8"))
        
        request_id = body.get("id")
        method = body.get("method")
        params = body.get("params", {})
        
        if method == "tools/call" and params.get("name") == "prime_context":
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"context": "mocked-context", "topic": "test"})}]
                }
            }
        else:
            response = {"jsonrpc": "2.0", "id": request_id, "error": {"message": "Unknown"}}
            
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
