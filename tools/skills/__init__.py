# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 专有技能中枢 (Skills Registry)
汇总所有考研专用技能插件：
  1. vision_solver: 多模态图像识别、手写草稿逐行批改、LaTeX 提取
  2. math_verifier: SymPy 高精度符号计算与验算 (极限、导数、积分、矩阵、级数)
  3. english_dissector: 考研英语长难句搭积木解剖与翻译
  4. pdf_extractor: 资料库教材与真题 PDF 文本抽取
  5. error_logger: 错题本自动沉淀与薄弱点雷达更新
"""

from . import vision_solver
from . import math_verifier
from . import english_dissector
from . import pdf_extractor
from . import error_logger

SKILLS_REGISTRY = {
    "vision_solver": {
        "name": "👁️ 视觉看图与手写批改技能 (Vision & OCR Solver)",
        "desc": "支持上传手写草稿与试卷截图，逐行批改、采分点赋分、LaTeX公式提取",
        "command": "/img <路径> 或 /ocr <路径>",
        "status": "已就绪"
    },
    "math_verifier": {
        "name": "📐 数学高精度符号计算技能 (Math & SymPy Verifier)",
        "desc": "极限/求导/微积分/行列式/特征值/泰勒展开精确验算，杜绝计算幻觉",
        "command": "/calc <数学表达式>",
        "status": math_verifier.get_status()
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
    "error_logger": {
        "name": "📌 错题自动归档技能 (Error Logger)",
        "desc": "将批改出的错题与错因自动写入对应科目的「错题本/」",
        "command": "自动触发或 /log",
        "status": "已就绪"
    }
}

def list_skills():
    """返回当前已加载的所有专有技能清单"""
    return SKILLS_REGISTRY
