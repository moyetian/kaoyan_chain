# -*- coding: utf-8 -*-
r"""
考研学习链 (ky-cli) · 回合恢复 (Turn Recovery)
职责：RunLoop 的「回合级」自愈设施 —— 目前承载 Doom-loop 熔断器：

模型（尤其 flash 级）在工具失败后有时会**原样重发**同一次调用（相同工具 +
相同参数），把步数烧在不可能成功的重复上（实测：搜索引擎直抓被拦后换引擎
重试、命令被拒后换壳重试，均属同类；更隐蔽的是成功工具的无进展重复）。
本模块按「同签名连续计数」熔断：连续第 3 次完全相同的调用不再执行，
合成一条 tool 结果引导模型改变策略。

设计约定：
- 签名 = (工具名, 规范化参数)；参数 JSON 排序序列化（键序无关），
  ``interactive`` 为执行层注入键（非模型参数）—— 计算签名时忽略，
  避免「同参数不同交互态」被误判为不同调用。
- 「连续」语义：签名变化即重置计数；每轮 run 新建实例（run 级作用域）。
- 熔断后**继续观察**：同签名第 4、5 次调用仍熔断（streak 继续累积），
  直到模型改变签名为止。
"""

import json
from typing import Any, Dict, Optional, Tuple

#: 熔断阈值：同签名连续出现达到该次数 → 该次调用不再执行。
FUSE_THRESHOLD = 3

#: 执行层注入键（模型参数之外），签名计算时忽略。
_INJECTED_ARG_KEYS = ("interactive",)


class DoomLoopBreaker:
    """同签名连续重复的工具调用熔断器（回合级；每轮 run 新建实例）。"""

    #: 连续同签名达到该次数即熔断（与 FUSE_THRESHOLD 同源，供实例读）。
    threshold = FUSE_THRESHOLD

    def __init__(self, threshold: Optional[int] = None):
        self.threshold = int(threshold) if threshold else FUSE_THRESHOLD
        self._last_sig: Optional[str] = None
        self._streak = 0

    @staticmethod
    def signature(tool_name: str, tool_args: Any) -> str:
        """工具调用的规范化签名（键序无关；忽略执行层注入键）。

        序列化失败（异常对象等）时退化为「工具名 + 原样 repr」——绝不抛错
        （熔断器自身不得成为新的故障点）。
        """
        try:
            args = tool_args if isinstance(tool_args, dict) else {}
            clean = {k: v for k, v in args.items() if k not in _INJECTED_ARG_KEYS}
            return f"{tool_name}|" + json.dumps(clean, sort_keys=True,
                                                ensure_ascii=False, default=str)
        except Exception:
            return f"{tool_name}|{tool_args!r}"

    def observe(self, tool_name: str, tool_args: Any) -> Tuple[bool, int]:
        """观察一次即将执行的调用，返回 ``(fused, streak)``。

        ``fused=True`` 表示连续同签名已达阈值 —— 调用方应**跳过执行**并
        合成提示结果；``streak`` 为该签名当前连续次数（熔断文案用）。
        """
        sig = self.signature(tool_name, tool_args)
        if sig == self._last_sig:
            self._streak += 1
        else:
            self._last_sig = sig
            self._streak = 1
        return self._streak >= self.threshold, self._streak

    def fused_result(self, tool_name: str, streak: int) -> str:
        """熔断时合成的 tool 结果（提示模型改道；前缀供 is_err 判定复用）。"""
        return (
            f"DoomLoopFused: 检测到连续 {streak} 次完全相同的工具调用 "
            f"[{tool_name}]（工具名与参数均未变化），已熔断本次调用——不再重复执行。"
            f"继续原样重发不会产生新信息。请立即改变策略："
            f"更换工具或调整参数（如更换检索词 / 读取不同页段 / 改用其他工具），"
            f"或基于已获取的信息直接作答。"
        )
