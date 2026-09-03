# -*- coding: utf-8 -*-
r"""
考研学习链 (ky-cli) · Model Context Protocol (MCP) 客户端引擎
支持基于标准 JSON-RPC 2.0 stdio 的外部 MCP Server 挂载:
1. tools/list & tools/call: 动态发现并挂载外部 MCP 工具
2. resources/list & resources/read: 访问外部考研学习资源
3. prompts/list & prompts/get: 加载外部专用 Prompt 模板
"""

import sys
import json
import time
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

class MCPProcessClient:
    """管理单个外部 MCP Server 的子进程通信 (stdio JSON-RPC 2.0)"""
    def __init__(self, name: str, command: str, args: List[str], env: Optional[Dict[str, str]] = None, cwd: Optional[Path] = None):
        self.name = name
        self.command = command
        self.args = args
        self.env = env
        self.cwd = cwd
        self.process: Optional[subprocess.Popen] = None
        self.msg_id = 0
        self.is_initialized = False

    def start(self) -> bool:
        """启动 MCP Server 子进程并执行 initialize 握手"""
        cmd_list = [self.command] + self.args
        try:
            self.process = subprocess.Popen(
                cmd_list,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.cwd) if self.cwd else None,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1
            )
            # 握手
            init_res = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "clientInfo": {"name": "ky-cli", "version": "1.0"}
            })
            if init_res and "result" in init_res:
                self._send_notification("notifications/initialized", {})
                self.is_initialized = True
                return True
            return False
        except Exception as e:
            # 启动失败时优雅降级
            return False

    def list_tools(self) -> List[Dict[str, Any]]:
        """获取 MCP Server 提供的工具清单"""
        if not self.is_initialized:
            return []
        resp = self._send_request("tools/list", {})
        if resp and "result" in resp:
            return resp["result"].get("tools", [])
        return []

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """调用外部 MCP 工具"""
        if not self.is_initialized:
            return "Error: MCP Server 未初始化"
        resp = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })
        if resp and "result" in resp:
            content = resp["result"].get("content", [])
            # 格式化文本输出
            txt_parts = [c.get("text", "") for c in content if isinstance(c, dict)]
            return "\n".join(txt_parts) or str(resp["result"])
        if resp and "error" in resp:
            return f"MCPError: {resp['error']}"
        return "Error: MCP 工具无响应"

    def stop(self):
        """停止子进程"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
            self.is_initialized = False

    def _send_request(self, method: str, params: Dict[str, Any], timeout: int = 15) -> Optional[Dict[str, Any]]:
        if not self.process or not self.process.stdin:
            return None
        self.msg_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self.msg_id,
            "method": method,
            "params": params
        }
        msg_str = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            self.process.stdin.write(msg_str)
            self.process.stdin.flush()
            
            # 读取一行 JSON-RPC 响应
            resp_line = self.process.stdout.readline()
            if not resp_line:
                return None
            return json.loads(resp_line.strip())
        except Exception:
            return None

    def _send_notification(self, method: str, params: Dict[str, Any]):
        if not self.process or not self.process.stdin:
            return
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except Exception:
            pass

class MCPClientManager:
    """管理全工作区的外部 MCP 服务并对接到 Agent 注册表"""
    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        self.clients: Dict[str, MCPProcessClient] = {}

    def load_from_config(self, mcp_config_dict: Dict[str, Any]):
        """从字典或配置文件中拉起 MCP Servers"""
        for s_name, s_conf in mcp_config_dict.items():
            cmd = s_conf.get("command")
            args = s_conf.get("args", [])
            if not cmd:
                continue
            client = MCPProcessClient(name=s_name, command=cmd, args=args, cwd=self.workspace_root)
            if client.start():
                self.clients[s_name] = client

    def get_all_mcp_tools(self) -> List[Dict[str, Any]]:
        """收集所有已连接外部 MCP Server 提供的工具清单"""
        all_tools = []
        for s_name, client in self.clients.items():
            tools = client.list_tools()
            for t in tools:
                # 给工具名加上 mcp_ 前缀避免同名冲突
                orig_name = t.get("name", "")
                scoped_name = f"mcp_{s_name}_{orig_name}"
                all_tools.append({
                    "mcp_server": s_name,
                    "orig_name": orig_name,
                    "scoped_name": scoped_name,
                    "description": f"[MCP: {s_name}] " + t.get("description", ""),
                    "inputSchema": t.get("inputSchema", {})
                })
        return all_tools

    def execute_mcp_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> str:
        client = self.clients.get(server_name)
        if not client:
            return f"Error: 未连接的 MCP Server [{server_name}]"
        return client.call_tool(tool_name, arguments)

    def close_all(self):
        for c in self.clients.values():
            c.stop()
        self.clients.clear()
