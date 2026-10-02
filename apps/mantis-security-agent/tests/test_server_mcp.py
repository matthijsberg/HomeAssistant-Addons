import os
import json
import pytest
from fastapi.testclient import TestClient

from server import app

client = TestClient(app)


def test_ingress_dashboard():
    response = client.get("/")
    assert response.status_code == 200
    assert "Mantis Security Agent" in response.text


def test_mcp_initialize():
    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "initialize",
        "params": {}
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["result"]["serverInfo"]["name"] == "mantis-security-agent"


def test_mcp_tools_list():
    payload = {
        "jsonrpc": "2.0",
        "id": "2",
        "method": "tools/list",
        "params": {}
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    data = response.json()
    tools = [t["name"] for t in data["result"]["tools"]]
    assert "mantis_review_diff" in tools
    assert "mantis_start_audit" in tools
    assert "mantis_get_report" in tools


def test_mcp_review_diff_clean():
    diff_content = """
    --- a/math.py
    +++ b/math.py
    @@ -1,3 +1,3 @@
    -def add(a, b): return a - b
    +def add(a, b): return a + b
    """
    payload = {
        "jsonrpc": "2.0",
        "id": "3",
        "method": "tools/call",
        "params": {
            "name": "mantis_review_diff",
            "arguments": {"diff": diff_content}
        }
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    res_data = json.loads(response.json()["result"]["content"][0]["text"])
    assert res_data["status"] == "completed"
    assert res_data["verdict"] == "CLEAN"


def test_mcp_review_diff_flagged():
    bad_diff = """
    --- a/runner.py
    +++ b/runner.py
    @@ -1,2 +1,3 @@
    +import subprocess
    +subprocess.Popen(user_cmd, shell=True)
    """
    payload = {
        "jsonrpc": "2.0",
        "id": "4",
        "method": "tools/call",
        "params": {
            "name": "mantis_review_diff",
            "arguments": {"diff": bad_diff}
        }
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    res_data = json.loads(response.json()["result"]["content"][0]["text"])
    assert res_data["status"] == "completed"
    assert res_data["verdict"] == "FLAGGED"
    assert len(res_data["findings"]) >= 1
    assert res_data["findings"][0]["cwe"] == "CWE-78"
