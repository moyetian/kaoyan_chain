# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 专有技能中枢 (Skills Registry)
汇总所有考研专用技能插件：
  1. vision_solver: 多模态图像识别、手写草稿逐行批改、LaTeX 提取
  2. math_verifier: SymPy 高精度符号计算与验算 (微分方程、二次型正定、极限、求导、微积分、矩阵、级数)
  3. english_dissector: 考研英语长难句搭积木解剖与翻译
  4. socratic_tutor: 苏格拉底式三级阶梯微步骤启发引导 (/hint)
  5. error_logger: 错题归档与艾宾浩斯盲盒复测闭环引擎 (/review /quiz)
  6. pdf_extractor: 资料库教材与真题 PDF 文本抽取
  7. latex_beautifier: 终端数学公式 Unicode 美化与实时网页伴侣联动
"""

from . import vision_solver
from . import math_verifier
from . import english_dissector
from . import socratic_tutor
from . import error_logger
from . import pdf_extractor
from . import latex_beautifier
from . import exam_composer
from . import variant_retriever
from . import knowledge_map
from . import exam_diagnoser

SKILLS_REGISTRY = {
    "vision_solver": {
        "name": "👁️ 视觉看图与手写批改技能 (Vision & OCR Solver)",
        "desc": "支持上传手写草稿与试卷截图，逐行批改、采分点赋分、LaTeX公式提取",
        "command": "/img <路径> 或 /ocr <路径>",
        "status": "已就绪"
    },
    "math_verifier": {
        "name": "📐 数学高精度符号计算技能 (Math & SymPy Verifier)",
        "desc": "常微分方程/二次型正定/级数求和/极限/微积分/矩阵，杜绝计算幻觉",
        "command": "/calc <数学表达式>",
        "status": math_verifier.get_status()
    },
    "socratic_tutor": {
        "name": "💡 苏格拉底式微步骤脚手架 (Socratic Scaffolding Tutor)",
        "desc": "拒绝直接剧透答案，通过三级微步骤（破题定性/首步搭桥/避坑指南）循循善诱",
        "command": "/hint [题目] 或快捷键 [5]",
        "status": "已就绪"
    },
    "error_logger": {
        "name": "🎯 错题归档与艾宾浩斯盲盒复测闭环 (Error Logger & Quiz Engine)",
        "desc": "自动提取错题现场，隐去原解析生成盲盒试题，复测合格自动标记出库",
        "command": "/review [科目] 或 /quiz 或快捷键 [2]",
        "status": "已就绪"
    },
    "latex_beautifier": {
        "name": "🌐 终端公式美化与实时网页伴侣 (LaTeX Beautifier & Live View)",
        "desc": "将晦涩的 LaTeX 语法转为易读 Unicode 符号，并联动 KaTeX 实时网页渲染",
        "command": "/view (打开网页伴侣) 或快捷键 [3]",
        "status": "已就绪"
    },
    "english_dissector": {
        "name": "🧱 英语长难句搭积木切分技能 (Sentence Dissector)",
        "desc": "五步切分长难句主干、从句层级、非谓语与润色翻译",
        "command": "/dissect <长难句>",
        "status": "已就绪"
    },
    "pdf_extractor": {
        "name": "📚 参考书与真题检索技能 (PDF & Document Extractor)",
        "desc": "快速检索四科「参考资料/」教材与历年真题库内容",
        "command": "/pdf [关键词或页码]",
        "status": "已就绪"
    },
    "exam_composer": {
        "name": "📝 错题反向靶向组卷技能 (Exam Composer)",
        "desc": "基于历史高频错因与艾宾浩斯到期错题，靶向生成阶段专项自测卷",
        "command": "ky exam [科目] [--count=N] 或 /exam",
        "status": "已就绪"
    },
    "variant_retriever": {
        "name": "🔍 考研同类真题变式检索与防幻觉溯源 (Real Variant Retriever)",
        "desc": "优先检索白名单题库同类变式题，若无则标注自拟警告，严禁伪造题源",
        "command": "ky variant <考点> 或 /variant",
        "status": "已就绪"
    },
    "knowledge_map": {
        "name": "🗺️ 官方考纲知识点图谱与掌握度映射 (Knowledge Map)",
        "desc": "将官方考试大纲要求、历年题型与学员错题薄弱点多维对齐映射",
        "command": "ky map [科目] 或 /map",
        "status": "已就绪"
    },
    "exam_diagnoser": {
        "name": "🩺 整卷级多题诊断与失分聚类引擎 (Exam Diagnoser)",
        "desc": "分析模考整卷答题情况，输出章节失分排行、错因分布与薄弱攻坚战术",
        "command": "ky diagnose <试卷文本/路径> 或 /diagnose",
        "status": "已就绪"
    }
}

def list_skills():
    """返回当前已加载的所有专有技能清单"""
    return SKILLS_REGISTRY
