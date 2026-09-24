# -*- coding: utf-8 -*-
"""用于自动化测试的轻量级标准 JSON-RPC 2.0 stdio MCP 服务。

[B4] 与客户端声明保持一致：initialize 的 capabilities 同时声明
tools / resources / prompts 三类，并**真实实现**对应方法（含错误路径），
用于验证客户端 resources/prompts 补全（而不是"声明了却取不到"）。
"""
import sys
import json

#: 资源与 Prompt 的固定样例数据（纯本地，不触网）
_MOCK_RESOURCES = [
    {
        "uri": "ky://syllabus/sample",
        "name": "样本考纲片段",
        "description": "用于自动化测试的合成考纲资源",
        "mimeType": "text/markdown",
    }
]
_MOCK_RESOURCE_TEXTS = {
    "ky://syllabus/sample": "# 样本考纲\n- 样本考点 A（掌握）\n- 样本考点 B（理解）\n",
}
_MOCK_PROMPTS = [
    {
        "name": "sample_coach_prompt",
        "description": "用于自动化测试的合成私教 Prompt 模板",
        "arguments": [{"name": "topic", "description": "样本考点", "required": True}],
    }
]


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
                        "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
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
            elif method == "resources/list":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"resources": _MOCK_RESOURCES}
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            elif method == "resources/read":
                uri = (req.get("params") or {}).get("uri", "")
                text = _MOCK_RESOURCE_TEXTS.get(uri)
                if text is None:
                    # 错误路径：未知 uri → JSON-RPC 错误对象（客户端应返回 None 而非崩溃）
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32602, "message": f"Unknown resource uri: {uri}"}
                    }
                else:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "contents": [
                                {"uri": uri, "mimeType": "text/markdown", "text": text}
                            ]
                        }
                    }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            elif method == "prompts/list":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"prompts": _MOCK_PROMPTS}
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            elif method == "prompts/get":
                params = req.get("params") or {}
                name = params.get("name", "")
                arguments = params.get("arguments") or {}
                if name != "sample_coach_prompt":
                    # 错误路径：未知 prompt → JSON-RPC 错误对象
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32602, "message": f"Unknown prompt: {name}"}
                    }
                else:
                    topic = arguments.get("topic", "样本考点")
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "description": "样本私教 Prompt",
                            "messages": [
                                {
                                    "role": "user",
                                    "content": {"type": "text", "text": f"请讲解样本考点：{topic}"}
                                }
                            ]
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
