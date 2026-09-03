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

class AgentRunner:
    def __init__(
        self,
        config: Dict[str, Any],
        workspace_root=None,
        permission_mode: str = "ask",
        max_steps: int = 10,
        stream_callback: Optional[Callable[[str], None]] = None,
        live_callback: Optional[Callable[[str, str], None]] = None
    ):
        self.config = config
        self.workspace_root = workspace_root
        self.max_steps = max_steps
        self.stream_callback = stream_callback
        self.live_callback = live_callback

        # 初始化沙箱、权限与工具库
        self.sandbox = Sandbox(workspace_root=self.workspace_root)
        self.permissions = PermissionManager(mode=permission_mode)
        self.tool_registry = ToolRegistry(sandbox=self.sandbox, permissions=self.permissions)
        self.context_engine = ContextEngine(
            workspace_root=self.sandbox.workspace_root,
            active_subject=self.config.get("active_subject", "math")
        )

        self.history: List[Dict[str, Any]] = []

    def set_subject(self, subject: str):
        self.config["active_subject"] = subject
        self.context_engine.set_subject(subject)

    def run(self, user_input: str, interactive: bool = True) -> str:
        """运行完整的 Agent Loop 交互循环"""
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

        # 2. 上下文防爆压缩
        active_messages = self.context_engine.compact_context(active_messages)

        # 3. Agent 循环 (最多 max_steps 步)
        step = 0
        final_answer = ""

        while step < self.max_steps:
            step += 1
            
            # 向 LLM 请求（带 tools 参数）
            response_data = self._call_llm(active_messages)
            if not response_data:
                break

            choice = response_data.get("choices", [{}])[0]
            message = choice.get("message", {})
            content = message.get("content") or ""
            tool_calls = message.get("tool_calls") or []

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

                if content:
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
                    print(f"\n\033[96m🛠️  [Agent Tool] 智能私教正在调用: \033[1m{fn_name}\033[0m\033[96m({args_summary})\033[0m")

                    # 执行工具
                    exec_result = self.tool_registry.execute_tool(fn_name, fn_args, interactive=interactive)

                    # 简短结果提示
                    res_preview = str(exec_result)[:80].replace("\n", " ")
                    if "Error" in exec_result or "PermissionDenied" in exec_result:
                        print(f"   \033[93m↳ 结果: {res_preview}...\033[0m")
                    else:
                        print(f"   \033[92m↳ 完成: {res_preview}...\033[0m")

                    # 追加 tool 结果回包
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": fn_name,
                        "content": exec_result
                    }
                    active_messages.append(tool_msg)

                # 继续下一轮循环，让 LLM 拿到工具结果进行最终综合分析
                continue

            # ── 情形 B: 模型输出最终答案 (Final Answer) ──
            final_answer = content
            # 打字机流式输出给学员
            self._display_final_answer(final_answer)
            break

        # 4. 更新持久化对话历史 (保留最近 12 条记录)
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
        base_url = self.config.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
        url = f"{base_url}/chat/completions"
        api_key = self.config.get("api_key", "").strip()
        model = self.config.get("model", "deepseek-chat")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain-Agent/1.0"
        }

        openai_tools = self.tool_registry.get_openai_tools()

        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.config.get("temperature", 0.3),
            "tools": openai_tools,
            "tool_choice": "auto"
        }

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

        # Spinner 动态等待指示器
        import threading
        stop_spinner = threading.Event()

        def spinner_task():
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

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                stop_spinner.set()
                spinner_thread.join(timeout=0.2)
                resp_data = json.loads(resp.read().decode("utf-8"))
                return resp_data
        except urllib.error.HTTPError as e:
            stop_spinner.set()
            err_msg = e.read().decode("utf-8", errors="ignore")
            # 若模型明确不支持 tools 参数，尝试剔除 tools 降级请求
            if "tools" in err_msg.lower() or "not support" in err_msg.lower():
                return self._call_llm_without_tools(messages)
            print(f"\n\033[91m[API 错误 {e.code}]: {err_msg}\033[0m\n")
            return None
        except Exception as e:
            stop_spinner.set()
            print(f"\n\033[91m[连接异常]: {e}\033[0m\n")
            return None

    def _call_llm_without_tools(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """降级纯文本请求 (针对不支持 tools 字段的轻量模型)"""
        base_url = self.config.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
        url = f"{base_url}/chat/completions"
        api_key = self.config.get("api_key", "").strip()
        model = self.config.get("model", "deepseek-chat")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain-Agent/1.0"
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.config.get("temperature", 0.3)
        }

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
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
        """流式打字机逐字输出给终端学员"""
        if not text:
            return
        for char in text:
            sys.stdout.write(char)
            sys.stdout.flush()
            if self.stream_callback:
                self.stream_callback(char)
            # 极速打字机手感
            time.sleep(0.002)
        print()
