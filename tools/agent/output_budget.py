# -*- coding: utf-8 -*-
r"""
考研学习链 (ky-cli) · 工具输出预算 (Output Budget)
职责：把「工具结果体积」的截断规则收敛为**单一真源** + `execute_tool` 的
后置兜底：

1. ``TOOL_OUTPUT_LIMITS``：各工具自带的截断上限（fetch_url / git_diff /
   run_command / read_file 的 PDF 与文本分页）—— 数值与此前散落在
   ``tools_impl`` 各工具实现里的字面量逐字一致（K8 只搬家、不改值）。
2. ``apply_output_budget``：``execute_tool`` 出口的统一兜底 —— 结果超过
   ``ToolDefinition.budget``（默认 400k 字符，远大于各工具自带截断，
   **默认零行为变化**）时：截断返回值 + 把完整原文落盘到
   ``.memory/tool_outputs/<日期>/<工具>_<call_id>.txt``（原子写）。

失败语义（刻意保守）：
- 落盘失败（严格只读模式 ``PermissionDeniedError`` / 任何 IO 异常）→
  **静默降级**为「未落盘」提示，截断后的结果照常返回 —— 预算兜底绝不
  让一次工具调用失败。
- 落盘目录 ``.memory/tool_outputs/`` 是运行期产物：D0 快照与测试的
  「工作区零污染」断言须将其排除（与 ``.memory/sessions/`` 同类）。
"""

import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from ky_io import atomic_write_text
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # type: ignore

#: 工具输出截断上限（单一真源；数值与各工具实现的历史字面量逐字一致）。
TOOL_OUTPUT_LIMITS = {
    # fetch_url：清洗后的正文截断（tools_impl.fetch_url）
    "fetch_url_chars": 3000,
    # git_diff：diff 文本截断（tools_impl.git_diff）
    "git_diff_chars": 2000,
    # run_command：stdout / stderr 分项截断（tools_impl.run_command）
    "run_command_stdout_chars": 1500,
    "run_command_stderr_chars": 800,
    # read_file：PDF 最大字符数 / 文本默认最大行数（tools_impl.read_file）
    "read_file_pdf_chars": 12000,
    "read_file_text_lines": 2000,
}

#: execute_tool 后置兜底预算（字符）。默认 400k —— 远大于所有工具自带截断，
#: 默认路径零行为变化；只兜住「漏截断」的新工具/新路径（如 MCP 工具）。
DEFAULT_TOOL_OUTPUT_BUDGET = 50 * 1024
DEFAULT_TOOL_OUTPUT_MAX_LINES = 2_000

#: 落盘子目录（相对工作区根）。
TOOL_OUTPUTS_DIR = (".memory", "tool_outputs")


def _safe_component(text: Any, fallback: str) -> str:
    """把工具名 / call_id 变成安全的单层文件名组件。"""
    s = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(text or "")).strip("._")
    return (s or fallback)[:80]


def _save_full_output(workspace_root: Optional[Path], tool_name: str,
                      call_id: str, text: str) -> Optional[str]:
    """把完整原文落盘到 ``.memory/tool_outputs/<日期>/``；失败返回 None。

    ``atomic_write_text`` 自带只读模式闸门与父目录创建；任何异常都收敛为
    None（调用方降级为「未落盘」），绝不让预算兜底变成新的故障点。
    """
    if workspace_root is None:
        return None
    try:
        root = Path(workspace_root)
        day = date.today().strftime("%Y-%m-%d")
        dest_dir = root.joinpath(*TOOL_OUTPUTS_DIR) / day
        name = (f"{_safe_component(tool_name, 'tool')}_"
                f"{_safe_component(call_id, 'nocall')}.txt")
        dest = dest_dir / name
        atomic_write_text(dest, text)
        try:
            return str(dest.relative_to(root)).replace("\\", "/")
        except Exception:
            return str(dest)
    except Exception:
        return None


def apply_output_budget(result_text: Any, tool_name: str, *,
                        call_id: str = "",
                        budget: int = DEFAULT_TOOL_OUTPUT_BUDGET,
                        max_lines: int = DEFAULT_TOOL_OUTPUT_MAX_LINES,
                        workspace_root: Optional[Path] = None) -> str:
    """``execute_tool`` 出口兜底：超预算截断 + 完整原文落盘。

    * ``budget <= 0`` 或结果未超预算 → 原样返回（零行为变化）；
    * 超预算 → 返回「前 budget 字符 + 截断说明」；完整原文尝试落盘
      ``.memory/tool_outputs/<日期>/<工具>_<call_id>.txt``，失败则说明
      中如实标注「未落盘」。
    """
    text = str(result_text)
    try:
        limit = int(budget)
    except (TypeError, ValueError):
        limit = DEFAULT_TOOL_OUTPUT_BUDGET
    if limit <= 0:
        return text

    try:
        line_limit = int(max_lines)
    except (TypeError, ValueError):
        line_limit = DEFAULT_TOOL_OUTPUT_MAX_LINES
    lines = text.splitlines(keepends=True)
    over_lines = line_limit > 0 and len(lines) > line_limit
    if not over_lines and len(text) <= limit:
        return text
    preview = "".join(lines[:line_limit]) if over_lines else text
    preview = preview[:limit]

    saved = _save_full_output(workspace_root, tool_name, call_id, text)
    if saved:
        note = f"完整输出已落盘: {saved}（可用 read_file 分页续读）"
    else:
        note = "完整输出落盘失败（严格只读模式或 IO 异常），仅保留截断内容"
    return (
        preview
        + f"\n\n[... 输出超长（共 {len(text)} 字符，超预算 {limit}、行数上限 {line_limit}），已截断；{note} ...]"
    )
