# -*- coding: utf-8 -*-
r"""
考研学习链 (ky-cli) · Model Context Protocol (MCP) 客户端引擎
支持基于标准 JSON-RPC 2.0 stdio 的外部 MCP Server 挂载:
1. tools/list & tools/call: 动态发现并挂载外部 MCP 工具
2. resources/list & resources/read: 访问外部考研学习资源
3. prompts/list & prompts/get: 加载外部专用 Prompt 模板

[B4 修复·声明与实现一致] 此前 ``initialize`` 请求里声明了 tools/resources/prompts
三类 capability，但客户端只实现了 tools/* —— "声明 ≠ 实现"：server 会按声明把
资源与 Prompt 暴露出来，客户端却取不到。现补齐 resources/prompts 四个方法，
让声明与实现对齐（方案 A：补齐实现，而不是把声明收缩掉）。

[B4 修复·健康可辨识] ``start()`` 此前失败一律静默返回 False，崩溃的 server 在
会话里毫无痕迹。现在任何失败都把可辨识原因写进 ``last_error``，并由 ``health()``
汇总为 healthy / degraded / dead 三档，供 ``MCPClientManager.mcp_health()`` 与
doctor 展示。``start()`` 返回值保持 bool 以兼容既有调用方。
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
        # [B4] 失败原因与健康计数：start() 不再"静默 False"，
        # 每次失败都把可辨识原因写入 last_error，供 health()/mcp_health() 展示。
        self.last_error: str = ""
        self.server_info: Dict[str, Any] = {}
        self._request_failures = 0     # 连续失败计数（成功即清零）
        self._last_tool_count = 0      # 最近一次 tools/list 的工具数（health 免 IO）
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

    def start(self, timeout: int = 15) -> bool:
        """启动 MCP Server 子进程并执行 initialize 握手。

        [B4] 失败不再"静默 False"：命令不存在、进程立刻崩溃、握手超时/被拒、
        输出不是 JSON-RPC —— 每种情况都把可辨识原因写入 ``self.last_error``
        （经 ``health()`` 读取），由 manager 汇总进 ``mcp_health()`` 告警。
        返回值保持 bool 以兼容既有调用方（``load_from_config`` 等）。

        ``timeout`` 为 initialize 握手的等待秒数：交互会话用默认 15s；体检类
        调用方（doctor）可传更短的值避免卡住。
        """
        # [G2 修复·旧 reader 线程未回收] 旧实现只置 _reader_stop 标志、把
        # _reader_thread 置 None 就 Popen 新进程：旧线程未必已退出，且其 _loop
        # 每轮重新解引用 self.process，下一轮就读到**新进程**的 stdout，与新线程
        # 交错消费同一管道。改走 stop()：先停进程、抽干队列、再收敛线程（可重入）。
        self.stop()
        self.last_error = ""
        self.server_info = {}
        self._request_failures = 0
        self._last_tool_count = 0
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
        except FileNotFoundError as e:
            self.last_error = f"无法启动命令 {self.command!r}（文件不存在）: {e}"
            return False
        except Exception as e:
            self.last_error = f"启动子进程失败: {type(e).__name__}: {e}"
            return False

        # 握手
        try:
            init_res = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "clientInfo": {"name": "ky-cli", "version": "1.0"}
            }, timeout=timeout)
        except Exception as e:
            self.last_error = f"initialize 握手异常: {type(e).__name__}: {e}"
            self.stop()
            return False

        if init_res and "result" in init_res:
            result = init_res.get("result")
            if isinstance(result, dict):
                info = result.get("serverInfo")
                if isinstance(info, dict):
                    self.server_info = info
            self._send_notification("notifications/initialized", {})
            self.is_initialized = True
            self.last_error = ""
            return True

        # 失败路径：留下可辨识原因，并回收半启动的子进程（health 将据此报 dead）
        if init_res and "error" in init_res:
            self.last_error = f"initialize 被 server 拒绝: {init_res['error']}"
        else:
            self.last_error = ("initialize 握手无响应/超时"
                               "（server 可能已崩溃，或输出不是 JSON-RPC 行）")
        self.stop()
        return False

    def health(self) -> Dict[str, Any]:
        """[B4] 运行时健康快照（无 IO、毫秒级，供 mcp_health() / doctor 汇总）。

        三档语义：
          * ``dead``     —— 子进程未启动或已退出（含"起来就立刻崩"的外部 server）；
          * ``degraded`` —— 进程活着但 initialize 握手未完成，或连续多次请求失败
                            （如 server 乱输出导致响应永远对不上 id）；
          * ``healthy``  —— 已握手且最近没有连续失败。

        ``reason`` 始终可辨识，绝不静默；``tool_count`` 取最近一次 tools/list 的
        缓存值（避免健康检查本身触发一次可能超时的 IO）。
        """
        proc = self.process
        if proc is None:
            return {"name": self.name, "status": "dead",
                    "reason": self.last_error or "子进程未启动", "tool_count": 0}
        poll = getattr(proc, "poll", None)
        exit_code = poll() if callable(poll) else None
        if exit_code is not None:
            extra = f"；{self.last_error}" if self.last_error else ""
            return {"name": self.name, "status": "dead",
                    "reason": f"子进程已退出 (returncode={exit_code}){extra}",
                    "tool_count": 0}
        if not self.is_initialized:
            return {"name": self.name, "status": "degraded",
                    "reason": self.last_error or "initialize 握手未完成",
                    "tool_count": 0}
        if self._request_failures >= 2:
            extra = f"：{self.last_error}" if self.last_error else ""
            return {"name": self.name, "status": "degraded",
                    "reason": f"连续 {self._request_failures} 次请求失败{extra}",
                    "tool_count": self._last_tool_count}
        return {"name": self.name, "status": "healthy",
                "reason": "已握手，请求正常",
                "tool_count": self._last_tool_count}

    def list_tools(self, timeout: int = 15) -> List[Dict[str, Any]]:
        """获取 MCP Server 提供的工具清单（``timeout`` 供健康汇总用短超时）"""
        if not self.is_initialized:
            return []
        resp = self._send_request("tools/list", {}, timeout=timeout)
        if resp and "result" in resp:
            tools = resp["result"].get("tools", [])
            tools = tools if isinstance(tools, list) else []
            self._last_tool_count = len(tools)   # [B4] 供 health() 免 IO 汇报
            return tools
        return []

    def list_resources(self) -> List[Dict[str, Any]]:
        """[B4] 获取 MCP Server 暴露的资源清单 (``resources/list``)。

        与 tools 一样走 id 校验的请求通道；未初始化/失败返回空列表
        （失败原因见 ``health().reason``，绝不静默吞掉异常语义）。
        """
        if not self.is_initialized:
            return []
        resp = self._send_request("resources/list", {})
        if resp and "result" in resp:
            resources = resp["result"].get("resources", [])
            return resources if isinstance(resources, list) else []
        return []

    def read_resource(self, uri: str) -> Optional[Dict[str, Any]]:
        """[B4] 读取指定资源内容 (``resources/read``)；失败返回 None。"""
        if not self.is_initialized:
            return None
        resp = self._send_request("resources/read", {"uri": uri})
        if resp and "result" in resp and isinstance(resp["result"], dict):
            return resp["result"]
        return None

    def list_prompts(self) -> List[Dict[str, Any]]:
        """[B4] 获取 MCP Server 提供的 Prompt 模板清单 (``prompts/list``)。"""
        if not self.is_initialized:
            return []
        resp = self._send_request("prompts/list", {})
        if resp and "result" in resp:
            prompts = resp["result"].get("prompts", [])
            return prompts if isinstance(prompts, list) else []
        return []

    def get_prompt(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """[B4] 取回指定 Prompt 模板 (``prompts/get``)；失败返回 None。"""
        if not self.is_initialized:
            return None
        resp = self._send_request("prompts/get", {
            "name": name,
            "arguments": arguments or {}
        })
        if resp and "result" in resp and isinstance(resp["result"], dict):
            return resp["result"]
        return None

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
        # [F3 修复·reader 线程 join] 旧实现只把 _reader_thread 置 None 而不 join：
        # 若 terminate+kill 双失败（或子进程忽略 SIGTERM），旧线程仍阻塞在
        # readline 上滞留（daemon 线程，进程退出时才回收）。此处带 2s 上限 join，
        # 既回收句柄又不让 stop() 无限卡死；超时未退出的线程仍靠 _loop 内的
        # 局部 proc 固化 + _read_response 的 id 校验兜底，不会串台。
        _old_reader = self._reader_thread
        if _old_reader is not None and _old_reader is not threading.current_thread():
            try:
                _old_reader.join(timeout=2)
            except Exception:
                pass
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
        resp: Optional[Dict[str, Any]] = None
        try:
            self._ensure_reader()
            self.process.stdin.write(msg_str)
            self.process.stdin.flush()
            # 超时保护 + **id 校验**：只有 id 与本次请求一致的响应才算数，
            # 迟到的旧响应/通知一律丢弃（否则 tools/list 与 tools/call 会串位）。
            resp = self._read_response(timeout)
        except Exception as e:
            # [B4] 记录失败原因（写入管道失败 / 进程已崩等），不再无声吞掉
            self.last_error = f"{method} 请求发送失败: {type(e).__name__}: {e}"
        # [B4] 连续失败计数：health() 据此把"活着但请求永远对不上"的 server
        # 从 healthy 降为 degraded（如乱输出的 server）。
        if resp is None:
            self._request_failures += 1
            if not self.last_error:
                self.last_error = f"{method} 请求超时/无有效响应（{timeout}s 内未匹配到 id={self.msg_id}）"
        else:
            self._request_failures = 0
            self.last_error = ""
        return resp

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
        #: [B4] 启动失败的 server 名称 → 可辨识原因（mcp_health() 汇总用）。
        #: 失败的 client 不进入 clients（不参与工具注册），但**不静默丢弃** ——
        #: 否则崩溃的 server 在会话里毫无痕迹。
        self.failed: Dict[str, str] = {}

    def load_from_config(self, mcp_config_dict: Dict[str, Any], start_timeout: int = 15):
        """从字典或配置文件中拉起 MCP Servers。

        ``start_timeout``：initialize 握手的等待秒数（体检类调用方可传更短的值）。
        """
        for s_name, s_conf in mcp_config_dict.items():
            cmd = s_conf.get("command")
            args = s_conf.get("args", [])
            if not cmd:
                self.failed[s_name] = "配置缺少 command 字段"
                continue
            client = MCPProcessClient(
                name=s_name,
                command=cmd,
                args=args,
                env=s_conf.get("env"),
                cwd=self.workspace_root
            )
            if client.start(timeout=start_timeout):
                self.clients[s_name] = client
                self.failed.pop(s_name, None)
            else:
                # [B4] 失败原因保留在 failed 中，供 mcp_health() 报 dead + reason
                self.failed[s_name] = client.last_error or "启动失败（原因未知）"

    def mcp_health(self) -> Dict[str, Any]:
        """[B4] 全部 MCP Server 的健康汇总（供 doctor / 告警 / 会话自检）。

        输出结构::

            {
              "total": 2, "healthy": 1, "degraded": 0, "dead": 1,
              "servers": [
                {"name": "学习工具", "status": "healthy",
                 "reason": "已握手，请求正常", "tool_count": 1},
                {"name": "崩溃服务", "status": "dead",
                 "reason": "子进程已退出 (returncode=1)", "tool_count": 0},
              ]
            }

        每个 server 都带 名称/状态/原因/工具数；启动失败的 server 也在列
        （status=dead），绝不让崩溃静默。

        对 **healthy** 的 server 会顺带刷新一次工具数（``tools/list``，3s 短超时）：
        否则首次调用 mcp_health() 时 tool_count 恒为 0，汇总失真。degraded/dead
        的 server 不做任何 IO，避免健康检查本身被卡住。
        """
        servers: List[Dict[str, Any]] = []
        for _name, client in self.clients.items():
            snapshot = client.health()
            if snapshot["status"] == "healthy":
                client.list_tools(timeout=3)
                snapshot = client.health()
            servers.append(snapshot)
        for name, reason in self.failed.items():
            servers.append({"name": name, "status": "dead",
                            "reason": reason, "tool_count": 0})
        counts = {"healthy": 0, "degraded": 0, "dead": 0}
        for s in servers:
            status = str(s.get("status", "dead"))
            counts[status] = counts.get(status, 0) + 1
        return {
            "total": len(servers),
            "healthy": counts.get("healthy", 0),
            "degraded": counts.get("degraded", 0),
            "dead": counts.get("dead", 0),
            "servers": servers,
        }

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
        self.failed.clear()
