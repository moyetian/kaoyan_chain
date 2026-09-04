# -*- coding: utf-8 -*-
"""
考研学习链 (ky-cli) · 标准工具实现库 (Tools Implementation & Registry)
包含:
1. 文件工具 (read_file, write_file, edit_file, delete_file, list_directory, search_files)
2. 搜索工具 (grep)
3. Shell与系统工具 (run_command)
4. Git 工具 (git_status, git_diff, git_log)
5. 网络工具 (fetch_url)
6. 考研专属能力工具 (read_exam_paper, verify_math, socratic_hint, log_mistake, review_mistakes)
"""

import os
import sys
import json
import fnmatch
import subprocess
import urllib.request
from pathlib import Path
from typing import Dict, Any, Callable, List, Optional

from .sandbox import Sandbox, SecurityException
from .permissions import PermissionLevel, PermissionManager

# 引入现有考研 Skills 模块
ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = ROOT / "tools" / "skills"
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

try:
    from skills import math_verifier
    from skills import socratic_tutor
    from skills import error_logger
    from skills import pdf_extractor
    from skills import variant_retriever
    from skills import exam_composer
except ImportError:
    math_verifier = None
    socratic_tutor = None
    error_logger = None
    pdf_extractor = None
    variant_retriever = None
    exam_composer = None

class ToolDefinition:
    def __init__(self, name: str, desc: str, params_schema: Dict[str, Any], func: Callable, level: int):
        self.name = name
        self.desc = desc
        self.params_schema = params_schema
        self.func = func
        self.level = level

    def to_openai_dict(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.desc,
                "parameters": self.params_schema
            }
        }

class ToolRegistry:
    def __init__(self, sandbox: Sandbox, permissions: PermissionManager, memory_manager=None):
        self.sandbox = sandbox
        self.permissions = permissions
        self.memory_manager = memory_manager
        self.tools: Dict[str, ToolDefinition] = {}
        self._register_all_tools()

    def register(self, name: str, desc: str, params_schema: Dict[str, Any], level: int):
        def decorator(func: Callable):
            self.tools[name] = ToolDefinition(name, desc, params_schema, func, level)
            return func
        return decorator

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        return [t.to_openai_dict() for t in self.tools.values()]

    def execute_tool(self, name: str, args: Dict[str, Any], interactive: bool = True) -> str:
        """统一执行入口: 经过沙箱与权限验证"""
        tool_def = self.tools.get(name)
        if not tool_def:
            return f"Error: 未知工具 [{name}]"

        # 1. 权限审批检查
        allowed, reason = self.permissions.check_permission(name, tool_def.level, args, interactive=interactive)
        if not allowed:
            return f"PermissionDenied: 操作被拦截 ({reason})"

        # 2. 执行工具
        try:
            result = tool_def.func(**args)
            return str(result)
        except SecurityException as se:
            return f"SecurityError: {se}"
        except Exception as e:
            return f"ExecutionError in [{name}]: {type(e).__name__} - {str(e)}"

    def _register_all_tools(self):
        # ─────────────────────────────────────────────────────────────
        # 1. 文件工具
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="read_file",
            desc="读取本地文本或PDF文件。若路径为.pdf，将自动提取前若干页或指定页码的文本内容。",
            params_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件相对或绝对路径"},
                    "offset": {"type": "integer", "description": "起始字符偏移行 (默认0)"},
                    "limit": {"type": "integer", "description": "最大读取行数/字数 (默认2000)"}
                },
                "required": ["path"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
            p = self.sandbox.resolve_safe_path(path)
            if not p.exists():
                return f"Error: 文件不存在 [{p}]"

            # 智能 PDF 格式处理
            if p.suffix.lower() == ".pdf":
                if pdf_extractor:
                    pdf_info = pdf_extractor.extract_pdf_pages(str(p), max_pages=8)
                    if pdf_info.get("success"):
                        pages_txt = "\n".join([f"--- 第 {pg['page']} 页 ---\n{pg['text']}" for pg in pdf_info.get("pages", [])])
                        return f"【PDF文档自动提取: {p.name} (共 {pdf_info.get('total_pages', 0)} 页，提取前8页)】\n\n{pages_txt[:limit]}"
                    else:
                        return f"PDF提取失败: {pdf_info.get('error')}"
                return f"Error: 未检测到 PDF 提取模块"

            # 纯文本读取
            for enc in ("utf-8", "utf-8-sig", "gbk"):
                try:
                    lines = p.read_text(encoding=enc).splitlines()
                    selected = lines[offset: offset + limit]
                    return "\n".join(selected)
                except UnicodeDecodeError:
                    continue
            return f"Error: 文件解码失败，请确认是否为有效文本格式"

        @self.register(
            name="write_file",
            desc="新建或覆盖写入文件。在创建错题记录、规划表或作业文件时使用。",
            params_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "content": {"type": "string", "description": "待写入的完整文本内容"},
                    "overwrite": {"type": "boolean", "description": "若文件已存在是否覆盖 (默认False)"}
                },
                "required": ["path", "content"]
            },
            level=PermissionLevel.SAFE_EDIT
        )
        def write_file(path: str, content: str, overwrite: bool = False) -> str:
            p = self.sandbox.resolve_safe_path(path, allow_create=True)
            if p.exists() and not overwrite:
                return f"Error: 文件已存在且 overwrite=False [{p}]"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Success: 成功写入文件 [{p.name}] ({len(content)} 字符)"

        @self.register(
            name="edit_file",
            desc="精确替换文件中的特定文本块，实现安全的文件修改。",
            params_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "target_content": {"type": "string", "description": "需要被替换的原文内容 (必须精确匹配)"},
                    "replacement": {"type": "string", "description": "替换后的新内容"}
                },
                "required": ["path", "target_content", "replacement"]
            },
            level=PermissionLevel.SAFE_EDIT
        )
        def edit_file(path: str, target_content: str, replacement: str) -> str:
            p = self.sandbox.resolve_safe_path(path)
            if not p.exists():
                return f"Error: 文件不存在 [{p}]"
            raw = p.read_text(encoding="utf-8")
            if target_content not in raw:
                return f"Error: 在文件中未找到指定的 target_content 文本"
            updated = raw.replace(target_content, replacement, 1)
            p.write_text(updated, encoding="utf-8")
            return f"Success: 成功修改文件 [{p.name}]"

        @self.register(
            name="delete_file",
            desc="删除指定文件。受 Level 5 最高安全策略管控。",
            params_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "待删除文件路径"}
                },
                "required": ["path"]
            },
            level=PermissionLevel.DANGEROUS
        )
        def delete_file(path: str) -> str:
            p = self.sandbox.resolve_safe_path(path)
            if not p.exists():
                return f"Error: 文件不存在 [{p}]"
            p.unlink()
            return f"Success: 文件已删除 [{p.name}]"

        @self.register(
            name="list_directory",
            desc="列出指定目录下的文件与子目录清单。",
            params_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径 (默认当前工作区)"},
                    "max_depth": {"type": "integer", "description": "递归深度 (默认2)"}
                }
            },
            level=PermissionLevel.READ_ONLY
        )
        def list_directory(path: str = ".", max_depth: int = 2) -> str:
            target_dir = self.sandbox.resolve_safe_path(path)
            if not target_dir.is_dir():
                return f"Error: 路径不是有效目录 [{target_dir}]"

            entries = []
            for root, dirs, files in os.walk(target_dir):
                rel_p = Path(root).relative_to(target_dir)
                depth = len(rel_p.parts)
                if depth >= max_depth:
                    dirs.clear()
                    continue
                indent = "  " * depth
                if depth > 0:
                    entries.append(f"{indent}📁 {rel_p.name}/")
                for f in files:
                    if f.startswith(".git"):
                        continue
                    f_size = (Path(root) / f).stat().st_size
                    entries.append(f"{indent}  📄 {f} ({f_size} bytes)")

            return f"目录列表 [{target_dir.name}]:\n" + ("\n".join(entries[:60]) or "空目录")

        @self.register(
            name="search_files",
            desc="按文件名模式通配搜索工作区内的文件 (例如 *.pdf, *真题*, *中值定理*)。",
            params_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "文件名匹配通配符 (如 *.pdf 或 *真题*)"},
                    "path": {"type": "string", "description": "搜索起始目录 (默认当前目录)"}
                },
                "required": ["pattern"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def search_files(pattern: str, path: str = ".") -> str:
            start_dir = self.sandbox.resolve_safe_path(path)
            matched = []
            for root, dirs, files in os.walk(start_dir):
                for f in files:
                    if fnmatch.fnmatch(f.lower(), pattern.lower()):
                        full_p = Path(root) / f
                        matched.append(str(full_p.relative_to(self.sandbox.workspace_root) if full_p.is_relative_to(self.sandbox.workspace_root) else full_p))
                        if len(matched) >= 30:
                            break
            if not matched:
                return f"未找到匹配模式 [{pattern}] 的文件"
            return f"搜索结果 (找到 {len(matched)} 项):\n" + "\n".join(matched)

        # ─────────────────────────────────────────────────────────────
        # 2. 全文检索 (grep)
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="grep",
            desc="在指定目录或文件中搜索包含特定关键词或公式的文本行。",
            params_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索文本或关键词"},
                    "path": {"type": "string", "description": "搜索文件或目录路径 (默认当前目录)"},
                    "case_sensitive": {"type": "boolean", "description": "是否区分大小写 (默认False)"}
                },
                "required": ["query"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def grep(query: str, path: str = ".", case_sensitive: bool = False) -> str:
            target = self.sandbox.resolve_safe_path(path)
            results = []
            q_comp = query if case_sensitive else query.lower()

            def scan_file(fp: Path):
                if fp.suffix.lower() not in (".md", ".txt", ".py", ".json", ".template", ".tex"):
                    return
                try:
                    lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
                    for idx, line in enumerate(lines, 1):
                        target_line = line if case_sensitive else line.lower()
                        if q_comp in target_line:
                            rel_p = str(fp.relative_to(self.sandbox.workspace_root) if fp.is_relative_to(self.sandbox.workspace_root) else fp)
                            results.append(f"{rel_p}:{idx}: {line.strip()[:100]}")
                            if len(results) >= 25:
                                return
                except Exception:
                    pass

            if target.is_file():
                scan_file(target)
            else:
                for root, dirs, files in os.walk(target):
                    if ".git" in root:
                        continue
                    for f in files:
                        scan_file(Path(root) / f)
                        if len(results) >= 25:
                            break

            if not results:
                return f"在 [{path}] 中未找到包含 [{query}] 的匹配行"
            return f"Grep 搜索命中:\n" + "\n".join(results)

        # ─────────────────────────────────────────────────────────────
        # 3. 命令行工具 (run_command)
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="run_command",
            desc="在工作区安全子进程中执行 Shell/PowerShell 命令 (如运行测试 py tools/test_ky_suite.py)。",
            params_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "待执行的命令字符串"},
                    "timeout": {"type": "integer", "description": "超时秒数 (默认30)"}
                },
                "required": ["command"]
            },
            level=PermissionLevel.SHELL_EXEC
        )
        def run_command(command: str, timeout: int = 30) -> str:
            self.sandbox.check_command_safety(command)
            try:
                proc = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(self.sandbox.workspace_root),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    encoding="utf-8",
                    errors="replace"
                )
                out = (proc.stdout or "").strip()
                err = (proc.stderr or "").strip()
                return f"ReturnCode: {proc.returncode}\nStdout: {out[:1500]}\nStderr: {err[:800]}"
            except subprocess.TimeoutExpired:
                return f"Error: 命令执行超时 ({timeout}秒)"
            except Exception as e:
                return f"Error: 执行异常 - {e}"

        # ─────────────────────────────────────────────────────────────
        # 4. Git 工具
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="git_status",
            desc="查看当前工作区 Git 版本控制状态与未暂存修改。",
            params_schema={"type": "object", "properties": {}},
            level=PermissionLevel.READ_ONLY
        )
        def git_status() -> str:
            res = subprocess.run("git status --short", shell=True, cwd=str(self.sandbox.workspace_root), capture_output=True, text=True, errors="replace")
            return res.stdout.strip() or "工作区干净，无未提交更改"

        @self.register(
            name="git_diff",
            desc="查看当前工作区修改的代码与文档差异。",
            params_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "指定比较的文件路径 (可选)"}
                }
            },
            level=PermissionLevel.READ_ONLY
        )
        def git_diff(path: str = "") -> str:
            cmd = f"git diff {path}".strip()
            res = subprocess.run(cmd, shell=True, cwd=str(self.sandbox.workspace_root), capture_output=True, text=True, errors="replace")
            return res.stdout[:2000].strip() or "无 Diff 差异"

        # ─────────────────────────────────────────────────────────────
        # 5. 网络工具 (fetch_url & web_search)
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="fetch_url",
            desc="获取公开网络 URL 的网页文本内容 (例如查询真题解析或考纲最新动态)。自动过滤脚本与排版噪点。",
            params_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "目标网页 HTTP/HTTPS URL"}
                },
                "required": ["url"]
            },
            level=PermissionLevel.NETWORK
        )
        def fetch_url(url: str) -> str:
            if not url.startswith(("http://", "https://")):
                return "Error: 仅支持 http:// 或 https:// 协议"
            try:
                import re
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Kaoyan-Tutor/1.0"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    html_bytes = resp.read(80000)
                    text = html_bytes.decode("utf-8", errors="ignore")
                    # 深度过滤 script, style, nav, footer 噪点
                    text = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
                    clean_txt = re.sub(r"<[^>]+>", " ", text)
                    clean_txt = re.sub(r"\s+", " ", clean_txt).strip()
                    return clean_txt[:3000]
            except Exception as e:
                return f"Error 访问网页失败: {e}"

        @self.register(
            name="web_search",
            desc="安全联网检索考研真题、官方大纲变动通知、目标院校考研专业课简章等外部权威资讯。",
            params_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词 (例如 2026考研数学二大纲变动, 浙江大学408自命题)"},
                    "num_results": {"type": "integer", "description": "返回结果条数 (默认5)"}
                },
                "required": ["query"]
            },
            level=PermissionLevel.NETWORK
        )
        def web_search(query: str, num_results: int = 5) -> str:
            import urllib.parse
            import re
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                    snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
                    titles = re.findall(r'<a[^>]+class="result__url"[^>]*>(.*?)</a>', html, re.DOTALL)
                    results = []
                    limit = min(len(snippets), num_results)
                    for i in range(limit):
                        t_clean = re.sub(r'<[^>]+>', '', titles[i]).strip() if i < len(titles) else f"结果 {i+1}"
                        s_clean = re.sub(r'<[^>]+>', '', snippets[i]).strip()
                        results.append(f"[{i+1}] {t_clean}\n    摘要: {s_clean}")
                    if results:
                        return f"【DuckDuckGo 考研网络检索: {query}】:\n\n" + "\n\n".join(results)
                    else:
                        clean_txt = re.sub(r"<[^>]+>", " ", html)
                        clean_txt = re.sub(r"\s+", " ", clean_txt).strip()
                        return f"【网络检索摘要: {query}】:\n" + clean_txt[:1000]
            except Exception as e:
                return f"【网络检索提示】: 当前本地网络暂时无法访问外部搜索引擎 ({e})。请优先参考工作区内置的官方考纲与参考资料。"

        # ─────────────────────────────────────────────────────────────
        # 6. 考研专属能力工具 (针对用户痛点定制)
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="read_exam_paper",
            desc="专门从考研真题或习题集 PDF 中检索并提取指定年份、题号或知识点的原版题干。专门用于解决从真题集抽题需求！",
            params_schema={
                "type": "object",
                "properties": {
                    "pdf_path": {"type": "string", "description": "真题 PDF 相对或绝对路径 (例如 01-数学/参考资料/xxx.pdf)"},
                    "year": {"type": "string", "description": "真题年份 (例如 2018, 2021 等)"},
                    "question_no": {"type": "string", "description": "题目序号 (例如 第15题, 3, 大题 等)"},
                    "keyword": {"type": "string", "description": "考点关键词 (例如 中值定理, 泰勒, 二重积分)"}
                },
                "required": ["pdf_path"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def read_exam_paper(pdf_path: str, year: str = "", question_no: str = "", keyword: str = "") -> str:
            p = self.sandbox.resolve_safe_path(pdf_path)
            if not p.exists() or p.suffix.lower() != ".pdf":
                return f"Error: 指定的真题 PDF 不存在或格式不正确 [{pdf_path}]"

            if not pdf_extractor:
                return f"Error: 未挂载 PDF 提取技能 pdf_extractor"

            # 提取前 40 页或全量轻量扫描
            res = pdf_extractor.extract_pdf_pages(str(p), max_pages=35)
            if not res.get("success"):
                return f"PDF提取失败: {res.get('error')}"

            pages = res.get("pages", [])
            matched_snippets = []

            search_terms = [t.strip() for t in (year, question_no, keyword) if t.strip()]

            for pg in pages:
                pg_num = pg["page"]
                pg_txt = pg["text"]
                # 检查是否命中全部搜索词
                if search_terms:
                    hit = all(term.lower() in pg_txt.lower() for term in search_terms)
                    if hit:
                        matched_snippets.append(f"=== [命中真题页面: 第 {pg_num} 页] ===\n{pg_txt[:1200]}")
                        if len(matched_snippets) >= 3:
                            break
                else:
                    matched_snippets.append(f"=== [试卷页面: 第 {pg_num} 页] ===\n{pg_txt[:800]}")
                    if len(matched_snippets) >= 2:
                        break

            if not matched_snippets:
                # 若完全匹配失败，退回第一页和前文说明
                sample = pages[0]["text"][:600] if pages else "无内容"
                return f"提示: 在试卷前35页中未精确定位到检索词 {search_terms}。试卷首部样例:\n{sample}"

            return f"【从真题合集 ({p.name}) 成功调出真实试卷题干】:\n\n" + "\n\n".join(matched_snippets)

        @self.register(
            name="verify_math",
            desc="高精度符号运算引擎。支持常微分方程(ODE)、二次型正定性判断、级数求和、方程驻点求解与微积分严格推导，杜绝算力幻觉。",
            params_schema={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "数学式或运算命令 (如 ode y''+4*y=0, quad [[2,1],[1,2]], sum 1/n^2 from 1 to oo, diff x^3*sin(x))"}
                },
                "required": ["expression"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def verify_math(expression: str) -> str:
            if not math_verifier:
                return "Error: 未加载 math_verifier 技能"
            return math_verifier.run_math_query(expression)

        @self.register(
            name="socratic_hint",
            desc="苏格拉底三级微步骤脚手架引导。当学员做题卡壳时分级提供提示，严禁直接剧透最终答案。",
            params_schema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "卡壳题目的题干"},
                    "level": {"type": "integer", "description": "提示级别 (1: 破题定性; 2: 首步搭桥; 3: 命题避坑指南)"}
                },
                "required": ["question"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def socratic_hint(question: str, level: int = 1) -> str:
            if not socratic_tutor:
                return "Error: 未加载 socratic_tutor 技能"
            return socratic_tutor.build_hint_prompt(question, hint_level=level)

        @self.register(
            name="log_mistake",
            desc="将学员做错的题目规范归档沉淀到科目错题本 Markdown 文件中，记录错因五分类与改进处方。",
            params_schema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "科目代码: math, eng, pol, pro"},
                    "title": {"type": "string", "description": "错题标题 (如 2018数学二中值定理第15题)"},
                    "mistake_type": {"type": "string", "description": "错因五分类之一: 概念漏洞 / 审题偏差 / 公式记错 / 计算失误 / 书写丢分"},
                    "card_content": {"type": "string", "description": "错误点与改进处方分析"},
                    "question": {"type": "string", "description": "完整原题目设问 (供盲盒重测使用)"}
                },
                "required": ["subject", "title", "mistake_type", "card_content"]
            },
            level=PermissionLevel.SAFE_EDIT
        )
        def log_mistake(subject: str, title: str, mistake_type: str = "", card_content: str = "", question: str = "", **kwargs) -> str:
            if not error_logger:
                return "Error: 未加载 error_logger 技能"
            final_err_type = mistake_type or kwargs.get("error_type", "概念漏洞")
            final_detail = card_content or kwargs.get("detail", "")
            final_prescription = kwargs.get("prescription", "严格对照采分点复盘")
            final_question = question or kwargs.get("question", "")

            fp = error_logger.log_error_record(
                subject=subject,
                title=title,
                error_type=final_err_type,
                detail=final_detail,
                prescription=final_prescription,
                question=final_question
            )
            return f"Success: 错题已成功归档入库 [{fp}]"

        @self.register(
            name="review_mistakes",
            desc="扫描错题本并提取艾宾浩斯记忆周期到期的题目，生成抹去历史推导的盲盒重测试卷。",
            params_schema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "科目代码: math, eng, pol, pro"}
                }
            },
            level=PermissionLevel.READ_ONLY
        )
        def review_mistakes(subject: str = "math") -> str:
            if not error_logger:
                return "Error: 未加载 error_logger 技能"
            due_list = error_logger.get_due_reviews(subject)
            if not due_list:
                return f"恭喜！当前科目【{subject}】暂无到期待复测错题，所有薄弱点均已攻克！"
            first = due_list[0]
            card = error_logger.generate_blind_quiz(first)
            return f"【艾宾浩斯盲盒复测 (共待测 {len(due_list)} 题)】:\n\n{card}"

        @self.register(
            name="search_variant",
            desc="按考点关键词检索真实参考资料或真题变式题；若本地未挂载实体书则生成带防虚构水印的自拟变式，严禁虚构题源。",
            params_schema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "科目代码: math, eng, pol, pro"},
                    "keyword": {"type": "string", "description": "考点关键词 (如 中值定理、泰勒公式、定积分物理应用)"}
                },
                "required": ["subject", "keyword"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def search_variant(subject: str, keyword: str) -> str:
            if not variant_retriever:
                return "Error: 未加载 variant_retriever 技能"
            res = variant_retriever.search_real_variant(subject, keyword)
            out = [f"【变式题检索结果 · {res['subject_name']} · 考点: {res.get('keyword', keyword)}】({res['source_status']}):\n"]
            for v in res.get("variants", []):
                out.append(f"出处: {v.get('source_name')}\n{v.get('question')}\n")
            return "\n".join(out)

        @self.register(
            name="compose_exam",
            desc="从艾宾浩斯到期错题与薄弱点雷达中抽取题目拼装一张盲盒自测试卷。",
            params_schema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "科目代码: math, eng, pol, pro"},
                    "count": {"type": "integer", "description": "出卷题量，默认 3 题"}
                },
                "required": ["subject"]
            },
            level=PermissionLevel.SAFE_EDIT
        )
        def compose_exam(subject: str, count: int = 3) -> str:
            if not exam_composer:
                return "Error: 未加载 exam_composer 技能"
            res = exam_composer.compose_exam_paper(subject=subject, count=count, save_file=True)
            return f"【自测卷已生成】编号: {res['paper_id']} (共 {res['count']} 题)\n保存路径: {res['saved_path']}\n\n试卷内容概览:\n{res['content'][:500]}..."

        # ─────────────────────────────────────────────────────────────
        # 7. 三级记忆自主管理工具
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="manage_memory",
            desc="读取或更新三级记忆库 (global: 学员全局习惯; project: 考研战役配置; decisions: 关键避坑决策; session: 当前即时工作记忆)。",
            params_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "操作类型: read (读取), write (覆写), append (追加)", "enum": ["read", "write", "append"]},
                    "scope": {"type": "string", "description": "记忆分层: global, project, decisions, session", "enum": ["global", "project", "decisions", "session"]},
                    "content": {"type": "string", "description": "记忆内容 (action为write或append时必填)"}
                },
                "required": ["action", "scope"]
            },
            level=PermissionLevel.SAFE_EDIT
        )
        def manage_memory(action: str, scope: str, content: str = "") -> str:
            if not self.memory_manager:
                return "Error: 未挂载 MemoryManager"
            act = action.lower().strip()
            sc = scope.lower().strip()
            if act == "read":
                res = self.memory_manager.read_memory(sc)
                return f"【记忆库 {sc} 内容】:\n{res or '(空)'}"
            elif act == "write":
                ok = self.memory_manager.write_memory(sc, content)
                return f"Success: 已成功覆写 {sc} 记忆" if ok else f"Error: 写入 {sc} 记忆失败"
            elif act == "append":
                ok = self.memory_manager.append_memory(sc, content)
                return f"Success: 已成功向 {sc} 记忆追加要点" if ok else f"Error: 追加 {sc} 记忆失败"
            return f"Error: 未知操作 {action}"

    def register_mcp_tools(self, mcp_manager):
        """动态将外部 MCP Server 提供的工具注入注册表"""
        if not mcp_manager:
            return
        for mcp_tool in mcp_manager.get_all_mcp_tools():
            scoped_name = mcp_tool["scoped_name"]
            server_name = mcp_tool["mcp_server"]
            orig_name = mcp_tool["orig_name"]
            desc = mcp_tool["description"]
            schema = mcp_tool.get("inputSchema") or {"type": "object", "properties": {}}

            def make_mcp_caller(s_name, o_name):
                return lambda **kwargs: mcp_manager.execute_mcp_tool(s_name, o_name, kwargs)

            self.tools[scoped_name] = ToolDefinition(
                name=scoped_name,
                desc=desc,
                params_schema=schema,
                func=make_mcp_caller(server_name, orig_name),
                level=PermissionLevel.SHELL_EXEC
            )

