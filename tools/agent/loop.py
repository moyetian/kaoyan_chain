# -*- coding: utf-8 -*-
"""
考研学习链 (ky-cli) · 核心智能体执行循环 (Agent Loop)
标准工作流:
User ➔ LLM ➔ 判断是否需要 Tool ➔ Tool 执行 ➔ Tool Result ➔ LLM ➔ ... ➔ Final Answer
"""

import sys
import json
import time
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional, Callable

from .sandbox import Sandbox
from .permissions import PermissionManager
from .tools_impl import ToolRegistry
from .context_engine import ContextEngine
from .memory import MemoryManager
from .hooks import HookManager
from .mcp_client import MCPClientManager

try:  # 网络访问安全与响应体积上限（双导入路径兼容）
    from net_guard import MAX_HTTP_RESPONSE_BYTES, decompress_limited, safe_urlopen
except ImportError:  # pragma: no cover
    from tools.net_guard import MAX_HTTP_RESPONSE_BYTES, decompress_limited, safe_urlopen  # type: ignore


def normalize_openai_url(base_url: str, endpoint: str = "chat/completions") -> str:
    """智能规范化 OpenAI 兼容接口地址 (自动补齐 /v1 容错，并兼容 /v1, /v2, /v3, /v4 等多版本端点与反代)"""
    import re
    b = (base_url or "https://api.deepseek.com/v1").strip().rstrip("/")
    ep = (endpoint or "chat/completions").strip().lstrip("/")
    if b.endswith("/" + ep) or b.endswith("/chat/completions"):
        return b
    # 若已显式包含 API 版本号路径（如 /v1, /v2, /v3, /v4 等）
    if re.search(r"/v\d+(?:/.*)?$", b):
        return f"{b}/{ep}"
    # 针对未带版本号的标准根代理或中转站，补充 /v1
    return f"{b}/v1/{ep}"


class AgentRunner:
    def __init__(
        self,
        config: Dict[str, Any],
        workspace_root=None,
        permission_mode: str = "ask",
        max_steps: int = 10,
        stream_callback: Optional[Callable[[str], None]] = None,
        step_callback: Optional[Callable[[str], None]] = None,
        live_callback: Optional[Callable[[str, str], None]] = None,
        request_timeout: Optional[float] = None,
        # GUI 场景设 True：不在 stdout 打字机输出（否则控制台与界面各刷一份）
        quiet: bool = False,
    ):
        self.config = config
        self.workspace_root = workspace_root
        self.max_steps = max_steps
        self.stream_callback = stream_callback
        self.step_callback = step_callback
        self.live_callback = live_callback
        self.quiet = bool(quiet)
        # [P2 修复·GUI 卡死] 上游对话请求此前硬编码 120s 超时，GUI 端点击一次
        # 若上游无响应会「转圈」两分钟且无任何反馈。现允许调用方覆盖，
        # 并支持通过配置项 request_timeout / GUI 传入值调低。
        # [健壮性] 配置值可能被用户写成非数字（如 "60s"），此处做安全解析，
        # 解析失败一律回落到 120，绝不因一个可选配置让整个 AgentRunner 构造崩溃。
        self.request_timeout = self._resolve_timeout(request_timeout, config)
        
        # 1. 初始化三级记忆引擎并预装考研默认偏好
        self.memory_manager = MemoryManager(workspace_root=self.workspace_root)
        self.memory_manager.init_defaults_from_config(self.config)

        # 2. 初始化生命周期拦截钩子系统
        self.hooks = HookManager(workspace_root=self.workspace_root, memory_manager=self.memory_manager)

        # 3. 初始化外部 MCP 客户端管理器并尝试加载配置
        self.mcp_manager = MCPClientManager(workspace_root=self.workspace_root)
        if "mcp_servers" in self.config and isinstance(self.config["mcp_servers"], dict):
            self.mcp_manager.load_from_config(self.config["mcp_servers"])

        # 4. 初始化沙箱、权限与工具库
        self.sandbox = Sandbox(workspace_root=self.workspace_root)
        self.permissions = PermissionManager(mode=permission_mode, workspace_root=self.workspace_root)
        self.tool_registry = ToolRegistry(
            sandbox=self.sandbox,
            permissions=self.permissions,
            memory_manager=self.memory_manager
        )
        # 挂载外部 MCP 工具
        self.tool_registry.register_mcp_tools(self.mcp_manager)

        # 5. 初始化上下文引擎 (挂载三级分层记忆)
        self.context_engine = ContextEngine(
            workspace_root=self.sandbox.workspace_root,
            active_subject=self.config.get("active_subject", "math"),
            memory_manager=self.memory_manager
        )

        self.history: List[Dict[str, Any]] = []

    @staticmethod
    def _resolve_timeout(explicit, config) -> float:
        """解析请求超时秒数：显式参数 > 配置项 > 默认 120；非法值一律回落默认。"""
        for cand in (explicit, (config or {}).get("request_timeout")):
            if cand is None or cand == "":
                continue
            try:
                val = float(cand)
                if val > 0:
                    return val
            except (TypeError, ValueError):
                continue
        return 120.0

    def set_subject(self, subject: str):
        self.config["active_subject"] = subject
        self.context_engine.set_subject(subject)

    def run(self, user_input: str, interactive: bool = True) -> str:
        """运行完整的 Agent Loop 交互循环"""
        # 把当前数学科目编码注入 ctx，便于 hooks.py 的考纲红线区分 math1/2/3/396
        study_plan = self.config.get("study_plan") or {}
        ctx = {
            "active_subject": self.config.get("active_subject", "math"),
            "math_key": study_plan.get("math_key", "math2") if self.config.get("active_subject") == "math" else None,
            "user_input": user_input,
        }
        self.hooks.trigger_session_start(ctx)

        api_key = self.config.get("api_key", "").strip()
        if not api_key:
            err_msg = "[!] 错误: 未配置大模型 API Key！请在终端输入 /config 进行配置。"
            print(f"\033[91m{err_msg}\033[0m")
            return err_msg

        # 1. 组装对话上下文
        sys_prompt = self.context_engine.build_system_prompt()
        
        # 构建当前请求的消息列表
        active_messages: List[Dict[str, Any]] = [{"role": "system", "content": sys_prompt}]
        active_messages.extend(self.history)
        active_messages.append({"role": "user", "content": user_input})

        # 2. 上下文防爆压缩 (联动 BeforeCompact 自动提炼决策记忆)
        active_messages = self.context_engine.compact_context(active_messages, hook_manager=self.hooks)

        # 3. Agent 循环 (最多 max_steps 步)
        step = 0
        final_answer = ""

        while step < self.max_steps:
            step += 1
            if self.step_callback and step == 1:
                self.step_callback("⏳ [私教审阅中] 正在分析题干要求与教学规划...")
            
            # 向 LLM 请求（带 tools 参数）
            response_data = self._call_llm(active_messages)
            if not response_data:
                break

            choice = response_data.get("choices", [{}])[0]
            message = choice.get("message", {})
            content = message.get("content") or ""
            tool_calls = message.get("tool_calls") or []
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            if reasoning and self.step_callback:
                self.step_callback(f"🧠 [私教深度思考]\n{str(reasoning).strip()}")

            # ── 检查是否包含 XML 格式的 Fallback Tool Call ──
            if not tool_calls and "<tool_call>" in content:
                fallback_calls = self._parse_fallback_tool_calls(content)
                if fallback_calls:
                    tool_calls = fallback_calls
                    # 剔除掉 tool_call 标签纯文本
                    content = content.split("<tool_call>")[0].strip()

            # ── 情形 A: 模型要求调用外部工具 (Tool Call) ──
            if tool_calls:
                # 将 assistant 带 tool_calls 的消息记入上下文
                assistant_msg = {"role": "assistant", "content": content or None, "tool_calls": tool_calls}
                active_messages.append(assistant_msg)

                if content and not self.quiet:
                    print(content)

                for tc in tool_calls:
                    tc_id = tc.get("id", f"call_{int(time.time()*1000)}")
                    fn_info = tc.get("function", {})
                    fn_name = fn_info.get("name", "")
                    fn_args_raw = fn_info.get("arguments", "{}")

                    if isinstance(fn_args_raw, str):
                        try:
                            fn_args = json.loads(fn_args_raw)
                        except Exception:
                            fn_args = {}
                    else:
                        fn_args = fn_args_raw

                    # 优雅的高科技状态行显示
                    args_summary = ", ".join(f"{k}='{v}'" if len(str(v))<40 else f"{k}='...'" for k, v in fn_args.items())
                    if not self.quiet:
                        print(f"\n\033[96m🛠️  [Agent Tool] 智能私教正在调用: \033[1m{fn_name}\033[0m\033[96m({args_summary})\033[0m")
                    if self.step_callback:
                        self.step_callback(f"🛠️ [调用工具] {fn_name}({args_summary})")

                    # 触发 PreToolUse 钩子 (沙箱与考纲红线硬拦截)
                    allow, hook_reason, mod_args = self.hooks.trigger_pre_tool_use(fn_name, fn_args, ctx)
                    if not allow:
                        if not self.quiet:
                            print(f"   \033[91m↳ [考纲红线拦截]: {hook_reason}\033[0m")
                        if self.step_callback:
                            self.step_callback(f"   ↳ [考纲红线拦截]: {hook_reason}")
                        exec_result = f"HookBlocked: {hook_reason}"
                    else:
                        # 执行工具
                        exec_result = self.tool_registry.execute_tool(fn_name, mod_args, interactive=interactive)
                        # 触发 PostToolUse 钩子 (自检与联动)
                        exec_result = self.hooks.trigger_post_tool_use(fn_name, mod_args, exec_result, ctx)

                    # 简短结果提示
                    res_preview = str(exec_result)[:80].replace("\n", " ")
                    is_err = "Error" in exec_result or "PermissionDenied" in exec_result or "HookBlocked" in exec_result
                    if not self.quiet:
                        if is_err:
                            print(f"   \033[93m↳ 结果: {res_preview}...\033[0m")
                        else:
                            print(f"   \033[92m↳ 完成: {res_preview}...\033[0m")
                    if self.step_callback:
                        self.step_callback(f"   ↳ {'异常: ' if is_err else '完成: '}{res_preview}...")

                    # 追加 tool 结果回包
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": fn_name,
                        "content": exec_result
                    }
                    active_messages.append(tool_msg)

                # 工具回包可能包含大文件或多轮结果，在循环内动态防爆压缩
                active_messages = self.context_engine.compact_context(active_messages, hook_manager=self.hooks)

                # 继续下一轮循环，让 LLM 拿到工具结果进行最终综合分析
                continue

            # ── 情形 B: 模型输出最终答案 (Final Answer) ──
            final_answer = content
            # 打字机流式输出给学员
            self._display_final_answer(final_answer)
            break

        # 更新历史
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": final_answer})
        if len(self.history) > 12:
            self.history = self.history[-12:]

        # 同步推送到网页伴侣
        if self.live_callback:
            self.live_callback("user", user_input)
            self.live_callback("assistant", final_answer)

        return final_answer

    def _call_llm(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """调用兼容 OpenAI tools 规范的模型 API"""
        raw_base_url = self.config.get("base_url", "https://api.deepseek.com/v1")
        url = normalize_openai_url(raw_base_url, "chat/completions")
        api_key = self.config.get("api_key", "").strip()
        model = self.config.get("model", "deepseek-chat")

        accept_enc = "gzip, deflate, identity"
        try:
            import brotli  # noqa: F401
            accept_enc = "gzip, deflate, br, identity"
        except Exception:
            pass

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain-Agent/1.0",
            "Connection": "close",
            "Accept-Encoding": accept_enc
        }

        tools_list = self.tool_registry.get_openai_tools()
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.config.get("temperature", 0.3),
            "max_tokens": self.config.get("max_tokens", 4096),
        }
        if tools_list:
            payload["tools"] = tools_list
            payload["tool_choice"] = "auto"

        data_bytes = json.dumps(payload).encode("utf-8")

        import threading
        import socket
        import http.client

        stop_spinner = threading.Event()

        def spinner_task():
            if self.quiet:
                return
            if not sys.stdout.isatty():
                sys.stdout.write("  \033[96m*\033[0m \033[2m[考研私教正在审阅题干与规划工具调用...]\033[0m\n")
                sys.stdout.flush()
                return
            frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            idx = 0
            while not stop_spinner.is_set():
                frame = frames[idx % len(frames)]
                sys.stdout.write(f"\r  \033[96m{frame}\033[0m \033[2m[考研私教正在审阅题干与规划工具调用...]\033[0m")
                sys.stdout.flush()
                idx += 1
                time.sleep(0.08)
            sys.stdout.write("\r" + " " * 52 + "\r")
            sys.stdout.flush()

        spinner_thread = threading.Thread(target=spinner_task, daemon=True)
        spinner_thread.start()

        max_retries = 1
        for attempt in range(max_retries + 1):
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            try:
                # [B1 同类·跳转泄漏 Bearer] 经 safe_urlopen 发送：SSRF 逐跳复核 +
                # 跨域剥离 Authorization。UnsafeURLError 由下方通用 except 收口。
                with safe_urlopen(req, timeout=self.request_timeout) as resp:
                    stop_spinner.set()
                    spinner_thread.join(timeout=0.2)
                    # [P2 修复] 此前 resp.read() 无上限、gzip/zlib/brotli 解压也无上限，
                    # 恶意/被劫持的上游返回几十 KB 的「解压炸弹」即可撑爆内存。
                    raw_bytes = resp.read(MAX_HTTP_RESPONSE_BYTES)
                    headers_obj = getattr(resp, "headers", None)
                    enc = headers_obj.get("Content-Encoding", "").lower() if headers_obj and hasattr(headers_obj, "get") else ""
                    raw_bytes, _truncated = decompress_limited(raw_bytes, enc)
                    if _truncated:
                        print("\n\033[91m[响应过大] 上游响应解压后超过安全体积上限，已拒绝处理。\033[0m\n")
                        if self.step_callback:
                            self.step_callback("❌ [响应过大] 上游响应解压后超过安全体积上限，已拒绝处理。")
                        return None
                    raw_text = raw_bytes.decode("utf-8", errors="ignore").strip()
                    if raw_text.startswith("<!doctype html") or raw_text.startswith("<html"):
                        raise ValueError(f"服务端返回了网页 HTML 而非 API JSON 数据 (请求地址: {url})，请检查 base_url 配置")
                    resp_data = json.loads(raw_text)
                    return resp_data
            except urllib.error.HTTPError as e:
                stop_spinner.set()
                spinner_thread.join(timeout=0.2)
                err_msg = e.read(MAX_HTTP_RESPONSE_BYTES).decode("utf-8", errors="ignore")
                err_low = err_msg.lower()
                # 某些端点或反代对 tools、tool_choice、schema 敏感而报 400
                if e.code == 400 and (
                    "tool" in err_low
                    or "function" in err_low
                    or "support" in err_low
                    or "param" in err_low
                    or "extra" in err_low
                    or "unknown" in err_low
                    or "invalid" in err_low
                ):
                    if self.step_callback:
                        self.step_callback("⚡ [自动兼容] 检测到端点对工具调用敏感 (HTTP 400)，已平滑切换为纯文本对话模式...")
                    return self._call_llm_without_tools(messages)
                print(f"\n\033[91m[API 错误 {e.code}]: {err_msg}\033[0m\n")
                if self.step_callback:
                    self.step_callback(f"❌ [API 响应异常 HTTP {e.code}]: {err_msg}")
                return None
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionResetError, http.client.RemoteDisconnected) as e:
                if attempt < max_retries:
                    time.sleep(1.5)
                    continue
                stop_spinner.set()
                spinner_thread.join(timeout=0.2)
                print(f"\n\033[91m[连接异常]: {e}\033[0m\n")
                return None
            except Exception as e:
                stop_spinner.set()
                spinner_thread.join(timeout=0.2)
                print(f"\n\033[91m[连接异常]: {e}\033[0m\n")
                return None

    def _call_llm_without_tools(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """降级纯文本请求 (针对不支持 tools 字段或对 payload 敏感的轻量/非标模型)"""
        raw_base_url = self.config.get("base_url", "https://api.deepseek.com/v1")
        url = normalize_openai_url(raw_base_url, "chat/completions")
        api_key = self.config.get("api_key", "").strip()
        model = self.config.get("model", "deepseek-chat")

        accept_enc = "gzip, deflate, identity"
        try:
            import brotli  # noqa: F401
            accept_enc = "gzip, deflate, br, identity"
        except Exception:
            pass

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain-Agent/1.0",
            "Connection": "close",
            "Accept-Encoding": accept_enc
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.config.get("temperature", 0.3),
            "max_tokens": self.config.get("max_tokens", 4096),
        }

        def _send(p_data):
            data_bytes = json.dumps(p_data).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            # [B1 同类] 同 _call_llm：安全通道发送（调用方通用 except 收口）。
            with safe_urlopen(req, timeout=self.request_timeout) as resp:
                # [P2 修复] 同 _call_llm：读取与解压都加上体积上限。
                raw_bytes = resp.read(MAX_HTTP_RESPONSE_BYTES)
                headers_obj = getattr(resp, "headers", None)
                enc = headers_obj.get("Content-Encoding", "").lower() if headers_obj and hasattr(headers_obj, "get") else ""
                raw_bytes, _truncated = decompress_limited(raw_bytes, enc)
                if _truncated:
                    raise ValueError("上游响应解压后超过安全体积上限，已拒绝处理")
                return json.loads(raw_bytes.decode("utf-8", errors="ignore"))

        try:
            return _send(payload)
        except urllib.error.HTTPError as e2:
            e2_err = e2.read().decode("utf-8", errors="ignore")
            # 若某些特定模型拒绝 system 消息，将系统提示词合并进首个 user 消息重试
            if e2.code == 400 and ("system" in e2_err.lower() or "role" in e2_err.lower()):
                new_msgs = []
                sys_prefix = ""
                for m in messages:
                    if m.get("role") == "system":
                        sys_prefix += f"[系统指令: {m.get('content', '')}]\n\n"
                    else:
                        new_msgs.append(dict(m))
                if new_msgs and sys_prefix:
                    new_msgs[0]["content"] = sys_prefix + str(new_msgs[0].get("content", ""))
                try:
                    return _send({"model": model, "messages": new_msgs, "temperature": self.config.get("temperature", 0.3)})
                except Exception:
                    pass
            print(f"\n\033[91m[降级纯文本请求错误 HTTP {e2.code}]: {e2_err}\033[0m\n")
            return None
        except Exception as exc:
            print(f"\n\033[91m[纯文本对话异常]: {exc}\033[0m\n")
            return None

    def _parse_fallback_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        """从纯文本中解析 <tool_call>...</tool_call> 降级标签"""
        import re
        calls = []
        pattern = r"<tool_call>(.*?)</tool_call>"
        matches = re.findall(pattern, content, re.DOTALL)
        for idx, m in enumerate(matches):
            try:
                data = json.loads(m.strip())
                calls.append({
                    "id": f"call_fallback_{idx}_{int(time.time())}",
                    "type": "function",
                    "function": {
                        "name": data.get("name"),
                        "arguments": data.get("arguments", {})
                    }
                })
            except Exception:
                continue
        return calls

    def _display_final_answer(self, text: str):
        """流式打字机逐字输出给终端学员。

        [S3 改善] ``quiet=True``（GUI 场景）时不再往 stdout 打字机输出，避免控制台与
        界面各刷一份；有 ``stream_callback`` 时仍逐字符推送给调用方（GUI 的 chunk_signal）
        ，并且把「每次 1 个字符 + sleep 2ms」改为**按小片段推送**：原实现对上千字答案会
        产生上千次跨线程信号，GUI 主线程事件循环被刷爆，表现为界面卡顿。
        """
        if not text:
            return
        if self.stream_callback:
            step = 12                       # 每 12 字推送一次，兼顾手感与主线程压力
            for i in range(0, len(text), step):
                self.stream_callback(text[i:i + step])
                if not self.quiet:
                    sys.stdout.write(text[i:i + step])
                    sys.stdout.flush()
                time.sleep(0.02)
        elif not self.quiet:
            for char in text:
                sys.stdout.write(char)
                sys.stdout.flush()
                time.sleep(0.002)

        if not self.quiet:
            print()
