# -*- coding: utf-8 -*-
r"""
考研学习链 (ky-cli) · Model Context Protocol (MCP) 客户端引擎
支持基于标准 JSON-RPC 2.0 stdio 的外部 MCP Server 挂载:
1. tools/list & tools/call: 动态发现并挂载外部 MCP 工具
2. resources/list & resources/read: 访问外部考研学习资源
3. prompts/list & prompts/get: 加载外部专用 Prompt 模板
"""

import os
import json
import queue
import threading
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
        # [P2 修复] 常驻 reader 线程 + 单一接收队列。
        # 旧实现每次请求临时起一个 reader 线程：超时后该线程仍阻塞在 readline 上，
        # 之后读到的那行被塞进一个已无人消费的 queue 而丢失，下一次请求便读到
        # 「上一条请求的响应」—— 实测确认 id=1 的响应被当成 id=2 的返回值返回。
        self._rx_queue: "queue.Queue" = queue.Queue()
        self._reader_thread: Optional[threading.Thread] = None
        self._reader_stop = threading.Event()

    def _ensure_reader(self) -> None:
        """惰性启动常驻读取线程（每个客户端最多一个）。"""
        if self._reader_thread is not None and self._reader_thread.is_alive():
            return
        if not self.process or not self.process.stdout:
            return
        self._reader_stop.clear()

        def _loop():
            # [G2 修复·reader 线程串台] 把进程句柄**局部固化**：旧实现每轮读
            # self.process.stdout，一旦 start() 换掉 self.process，旧线程下一轮
            # 就会去读**新进程**的 stdout，与新线程交错消费同一管道。
            proc = self.process
            if proc is None or proc.stdout is None:
                return
            while not self._reader_stop.is_set():
                try:
                    line = proc.stdout.readline()
                except Exception as err:      # pragma: no cover - 进程异常退出
                    self._rx_queue.put(err)
                    return
                if not line:                  # EOF：子进程已关闭 stdout
                    return
                self._rx_queue.put(line)

        self._reader_thread = threading.Thread(target=_loop, daemon=True)
        self._reader_thread.start()

    def _read_response(self, timeout: float) -> Optional[Dict[str, Any]]:
        """读取并**校验 id** 的 JSON-RPC 响应；丢弃通知与过期响应，直到匹配或超时。"""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = self._rx_queue.get(timeout=remaining)
            except queue.Empty:
                return None
            if isinstance(line, Exception) or not line:
                continue
            try:
                resp = json.loads(str(line).strip())
            except Exception:
                continue
            if not isinstance(resp, dict):
                continue
            # 通知（无 id）直接跳过；id 不匹配的是上一条请求的迟到响应，同样丢弃
            if "id" not in resp:
                continue
            if resp.get("id") != self.msg_id:
                continue
            return resp

    def start(self) -> bool:
        """启动 MCP Server 子进程并执行 initialize 握手"""
        # [G2 修复·旧 reader 线程未回收] 旧实现只置 _reader_stop 标志、把
        # _reader_thread 置 None 就 Popen 新进程：旧线程未必已退出，且其 _loop
        # 每轮重新解引用 self.process，下一轮就读到**新进程**的 stdout，与新线程
        # 交错消费同一管道。改走 stop()：先停进程、抽干队列、再收敛线程（可重入）。
        self.stop()
        cmd_list = [self.command] + self.args
        try:
            merged_env = os.environ.copy()
            if self.env and isinstance(self.env, dict):
                merged_env.update({str(k): str(v) for k, v in self.env.items()})

            self.process = subprocess.Popen(
                cmd_list,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # 避免 Windows 管道填满导致子进程阻塞死锁
                env=merged_env,
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
        """停止子进程并收敛常驻 reader 线程。"""
        self._reader_stop.set()
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
        # 丢弃残留行，避免下一个会话读到上一个进程的响应
        while True:
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                break
        self._reader_thread = None

    def _send_request(self, method: str, params: Dict[str, Any], timeout: int = 15) -> Optional[Dict[str, Any]]:
        if not self.process or not self.process.stdin or not self.process.stdout:
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
            self._ensure_reader()
            self.process.stdin.write(msg_str)
            self.process.stdin.flush()
            # 超时保护 + **id 校验**：只有 id 与本次请求一致的响应才算数，
            # 迟到的旧响应/通知一律丢弃（否则 tools/list 与 tools/call 会串位）。
            return self._read_response(timeout)
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
            client = MCPProcessClient(
                name=s_name,
                command=cmd,
                args=args,
                env=s_conf.get("env"),
                cwd=self.workspace_root
            )
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
