# -*- coding: utf-8 -*-
r"""
考研学习链 (ky-cli) · 三级分层记忆系统 (Hierarchical Memory System)
分层结构:
1. Global    (~/.ky/memory/user.md): 跨项目学员长期偏好、英语底子、辅导风格
2. Project   (.memory/project.md): 当前考研战役大盘、院校专业、科目考纲、白名单资料
3. Decisions (.memory/decisions.md): 长期复习决策与题型取舍指南 (如数二绝不做三重积分)
4. Session   (.memory/session.md): 当日会话即时工作区与当前正在攻坚的题目
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional

class MemoryScope:
    GLOBAL = "global"       # 全局学员习惯
    PROJECT = "project"     # 考研项目战役
    DECISIONS = "decisions" # 复习决策与避坑
    SESSION = "session"     # 当日会话工作记忆

class MemoryManager:
    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        
        # 1. 本地项目记忆目录
        self.project_memory_dir = self.workspace_root / ".memory"
        self.project_memory_dir.mkdir(parents=True, exist_ok=True)
        
        # 2. 全局用户记忆目录 (~/.ky/memory)
        home_dir = Path.home()
        self.global_memory_dir = home_dir / ".ky" / "memory"
        try:
            self.global_memory_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # 降级在工作区内部
            self.global_memory_dir = self.project_memory_dir / "global_fallback"
            self.global_memory_dir.mkdir(parents=True, exist_ok=True)

        self._files = {
            MemoryScope.GLOBAL: self.global_memory_dir / "user.md",
            MemoryScope.PROJECT: self.project_memory_dir / "project.md",
            MemoryScope.DECISIONS: self.project_memory_dir / "decisions.md",
            MemoryScope.SESSION: self.project_memory_dir / "session.md",
        }

    def get_file_path(self, scope: str) -> Path:
        norm_scope = scope.lower().strip()
        if norm_scope in self._files:
            return self._files[norm_scope]
        return self.project_memory_dir / f"{norm_scope}.md"

    def read_memory(self, scope: str) -> str:
        """读取指定作用域的记忆文本"""
        fp = self.get_file_path(scope)
        if not fp.exists():
            return ""
        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                return fp.read_text(encoding=enc).strip()
            except Exception:
                continue
        return ""

    def write_memory(self, scope: str, content: str) -> bool:
        """覆写指定作用域的记忆文本"""
        fp = self.get_file_path(scope)
        try:
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content.strip() + "\n", encoding="utf-8")
            return True
        except Exception:
            return False

    def append_memory(self, scope: str, item: str) -> bool:
        """向指定作用域记忆追加一条记录"""
        existing = self.read_memory(scope)
        clean_item = item.strip()
        if not clean_item.startswith("- "):
            clean_item = "- " + clean_item
        
        updated = f"{existing}\n{clean_item}".strip() if existing else clean_item
        return self.write_memory(scope, updated)

    def init_defaults_from_config(self, cfg: Dict[str, Any]):
        """从项目现有配置自动生成初始化记忆模板"""
        plan = cfg.get("study_plan", {})
        
        # 1. Project Memory
        if not self._files[MemoryScope.PROJECT].exists() or not self.read_memory(MemoryScope.PROJECT):
            lines = [
                "# 考研战役项目记忆 (Project Memory)",
                f"- 目标年份: {plan.get('target_year', '2026')}",
                f"- 目标院校与专业: {plan.get('school', '目标院校')} · {plan.get('major', '报考专业')}",
                f"- 初试日期: {plan.get('exam_date', '2026-12-19')}",
                f"- 锁定大纲: {plan.get('math_name', '数学二 (302)')} / {plan.get('eng_name', '英语二 (204)')} / 思想政治理论 / {plan.get('pro_name', '408')}",
                f"- 总分提分目标: {plan.get('total_target', '370+ 分')}",
                f"- 核心薄弱防线: 数学({plan.get('math_weakness', '导数中值定理')}), 英语({plan.get('eng_weakness', '长难句定位')})"
            ]
            self.write_memory(MemoryScope.PROJECT, "\n".join(lines))

        # 2. Global Memory
        if not self._files[MemoryScope.GLOBAL].exists() or not self.read_memory(MemoryScope.GLOBAL):
            user_lines = [
                "# 全局学员习惯偏好 (Global Memory)",
                "- 默认语言: 简体中文 (优先使用规范考研阅卷学术术语)",
                "- 辅导风格偏好: 严格把关·保姆提分型 (每步给分，严查错因)",
                "- 解题习惯: 习惯在草稿纸上手写推导后拍照或逐步输入",
                "- 交互准则: 拒绝直接给出答案，先提示考点与第一步思路"
            ]
            self.write_memory(MemoryScope.GLOBAL, "\n".join(user_lines))

        # 3. Decisions Memory
        if not self._files[MemoryScope.DECISIONS].exists() or not self.read_memory(MemoryScope.DECISIONS):
            dec_lines = [
                "# 关键复习决策与避坑指南 (Decisions Memory)",
                "- [考纲红线]: 数学二严禁复习三重积分、曲面积分与无穷级数，严防超纲耗时",
                "- [方法取舍]: 导数中值定理证明题一律优先构造辅助函数，规避柯西中值定理复杂展开",
                "- [真题范围]: 英语二真题以 2010 年之后的规范真题为主，不盲目刷英语一超纲长难句"
            ]
            self.write_memory(MemoryScope.DECISIONS, "\n".join(dec_lines))

    def load_all_memory(self) -> str:
        """加载四层记忆并编译为统一的提示词上下文板块"""
        sections = []

        g_txt = self.read_memory(MemoryScope.GLOBAL)
        if g_txt:
            sections.append(f"【🌐 全局学员画像记忆 (Global)】\n{g_txt}")

        p_txt = self.read_memory(MemoryScope.PROJECT)
        if p_txt:
            sections.append(f"【🎯 考研战役项目记忆 (Project)】\n{p_txt}")

        d_txt = self.read_memory(MemoryScope.DECISIONS)
        if d_txt:
            sections.append(f"【📌 历史复习决策与避坑指南 (Decisions)】\n{d_txt}")

        s_txt = self.read_memory(MemoryScope.SESSION)
        if s_txt:
            sections.append(f"【📋 当前会话即时工作区 (Session)】\n{s_txt}")

        if not sections:
            return ""

        return "=== 🧠【三级分层智能记忆体系 (Memory Context)】===\n" + "\n\n".join(sections)
