# -*- coding: utf-8 -*-
"""用于自动化测试的轻量级标准 JSON-RPC 2.0 stdio MCP 服务"""
import sys
import json

def main():
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            req = json.loads(line)
            req_id = req.get("id")
            method = req.get("method")

            if method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mock-mcp-server", "version": "1.0"}
                    }
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            elif method == "notifications/initialized":
                # 通知无需回复
                continue
            elif method == "tools/list":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": [
                            {
                                "name": "study_calc",
                                "description": "考研专用轻量级加权计算器",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "score": {"type": "number", "description": "原始成绩"}
                                    },
                                    "required": ["score"]
                                }
                            }
                        ]
                    }
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            elif method == "tools/call":
                params = req.get("params", {})
                args = params.get("arguments", {})
                score = args.get("score", 100)
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"WeightedScore: {score * 1.2}"}]
                    }
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            else:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": "Method not found"}
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        except Exception:
            break

if __name__ == "__main__":
    main()
