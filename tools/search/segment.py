# -*- coding: utf-8 -*-
"""
中文分词模块（零新增依赖）

基于项目现有词典资源（data/universities/registry.json + 考研术语表）
实现最大正向匹配 + 2-gram 回退，解决中文搜索召回失败问题。

根因：原 relevance.py 的正则 `[一-鿿]{2,}` 贪婪匹配整段中文，
导致"华科计算机考研复试线多少分"被当作 1 个 token（13 字），词法得分恒为 0。

验证数据：
- 有空格：`华中科技大学 计算机 复试线` → 3 tokens → 得分 0.667 ✅
- 无空格：`华中科技大学计算机复试线` → 1 token → 得分 0.000 ❌
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Set

ROOT = Path(__file__).resolve().parent.parent.parent

# 考研高频术语词典（按《搜索能力评估报告》建议）
KAOYAN_TERMS = {
    # 分数线相关
    "复试分数线", "复试线", "分数线", "复试基本线", "进入复试初试成绩基本要求",
    "国家线", "自划线", "院线", "校线",

    # 招生相关
    "招生简章", "招生专业目录", "专业目录", "招生目录", "招生计划",
    "报录比", "报考录取比例", "录取比例", "推免比例", "统考名额",

    # 考试相关
    # [回归修复] 2026-09-18：删去复合词 "专业课真题"（保留 "考试真题"/"真题"）。
    # 复合词在词典里会导致最大正向匹配整体吞掉，使 "专业课真题" 无法与文档侧
    # "专业课考试真题"（切分为 专业课/考试真题）互相命中。删去后按下位词切分，
    # 再经同义归一得到 专业课/考试真题，双向可召回。
    "考试大纲", "考纲", "参考书目", "真题", "考试真题",
    "初试科目", "复试科目", "加试科目",

    # 专业学位
    "学术型", "专业型", "学硕", "专硕", "全日制", "非全日制",

    # 学校层级
    "985工程", "211工程", "双一流", "一流学科", "自主划线",
    "教育部直属", "省属重点", "研究生院",

    # 学院专业
    "计算机", "软件工程", "人工智能", "电子信息", "机械工程",
    "马克思主义哲学", "中西哲学史", "思想政治教育",
    "经济学", "金融学", "会计学", "企业管理",

    # 考研流程
    "考研", "研究生入学考试", "硕士研究生", "推免生", "调剂",
    "报名", "现场确认", "准考证", "初试", "复试", "体检",
}

# 同义词规范化表（查询侧与文档侧都要归一）
SYNONYM_CANONICALS = {
    "复试线": "复试分数线",
    "复试基本线": "复试分数线",
    "分数线": "复试分数线",
    "进入复试初试成绩基本要求": "复试分数线",

    "招生目录": "招生专业目录",
    "专业目录": "招生专业目录",

    "考纲": "考试大纲",
    "真题": "考试真题",

    "报录比": "报考录取比例",
}

MAX_WORD_LEN = 12  # 最长词汇长度（避免过度匹配）


def build_lexicon() -> Set[str]:
    """从既有资源构建分词词典（零新增依赖）"""
    lex: Set[str] = set()

    # 1. 从 universities registry 提取院校名和别名
    registry_path = ROOT / "data" / "universities" / "registry.json"
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            for item in registry.values():
                if "name" in item:
                    lex.add(item["name"])
                if "aliases" in item:
                    lex.update(item["aliases"])
                # 提取学院/专业名称
                if "departments" in item:
                    for dept_name in item["departments"].keys():
                        lex.add(dept_name)
        except Exception:
            pass  # 降级：词典加载失败不影响基本功能

    # 2. 添加考研术语
    lex.update(KAOYAN_TERMS)

    # 3. 添加同义词的所有变体
    for synonym, canonical in SYNONYM_CANONICALS.items():
        lex.add(synonym)
        lex.add(canonical)

    # 4. 常见数字词组（年份、科目代码）
    for year in range(2015, 2030):
        lex.add(str(year))
        lex.add(f"{year}年")

    # 5. [修复·回归] 2026-09-18：segment() 入口统一小写，词典中的 ASCII 词条
    #    （如别名 HUST/PKU）必须同步补小写形态，否则大小写查询失配。
    for word in list(lex):
        lowered = word.lower()
        if lowered != word:
            lex.add(lowered)

    return lex


# 全局词典（惰性加载）
_LEXICON: Set[str] | None = None
# 别名→标准名映射（惰性加载）：华科 → 华中科技大学
_ALIAS_MAP: dict | None = None


def _longest_at(text: str, pos: int, lexicon: Set[str]) -> str | None:
    """返回 text[pos:] 处的最长词典匹配，无匹配返回 None。"""
    upper = min(pos + MAX_WORD_LEN, len(text))
    for j in range(upper, pos, -1):
        if text[pos:j] in lexicon:
            return text[pos:j]
    return None


def get_alias_map() -> dict:
    """院校别名→标准名的归一映射（与词典同源，零新增依赖）。

    [回归修复] 2026-09-18：`test_whitespace_and_alias_do_not_change_ranking`
    要求"华科…"与"华中科技大学…"两种问法得分差 < 0.1。子串匹配下别名永远
    命中不了全称文档，故在分词阶段把别名**替换**为标准名（替换而非追加：
    追加会撑大分母，覆盖度照样对不上）。
    """
    global _ALIAS_MAP
    if _ALIAS_MAP is None:
        _ALIAS_MAP = {}
        registry_path = ROOT / "data" / "universities" / "registry.json"
        if registry_path.exists():
            try:
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
                for item in registry.values():
                    name = item.get("name", "") if isinstance(item, dict) else ""
                    if not name:
                        continue
                    for alias in (item.get("aliases", []) or []):
                        if alias and alias != name:
                            _ALIAS_MAP[alias] = name
                            lowered = alias.lower()
                            if lowered != alias:
                                _ALIAS_MAP[lowered] = name
            except Exception:
                pass
    return _ALIAS_MAP


def _canonical_word(token: str) -> str:
    """单 token 归一：同义词 → 别名（两集合不交，顺序无影响，幂等）。"""
    token = SYNONYM_CANONICALS.get(token, token)
    return get_alias_map().get(token, token)


def get_lexicon() -> Set[str]:
    """获取全局词典（单例模式）"""
    global _LEXICON
    if _LEXICON is None:
        _LEXICON = build_lexicon()
    return _LEXICON


def segment(text: str, lexicon: Set[str] | None = None) -> list[str]:
    """最大正向匹配 + 2-gram 回退

    Args:
        text: 待分词文本
        lexicon: 词典（None 则使用全局词典）

    Returns:
        分词后的 token 列表

    Examples:
        >>> segment("华中科技大学计算机复试线")
        ['华中科技大学', '计算机', '复试分数线']

        >>> segment("华科计算机考研复试线多少分")
        ['华中科技大学', '计算机', '考研', '复试分数线', '多少', '分']

        >>> segment("085409")
        ['085409']

        >>> segment("问复试线")
        ['复试分数线']

    [归一说明] 2026-09-18：本函数直接输出归一后形态（同义词→标准形、
    别名→标准名），query 侧与文档侧同走此函数即天然双向可比。
    `segment_and_normalize` 保留作兼容入口（幂等，无二次变化）。

    [修复·回归] 2026-09-18：此前版本有两个回归——
    1. 空白字符会被 2-gram 回退切进 token（如 `' '`、`' 2'`），进而让
       `is_relevant` 的子串兜底恒成立、日文垃圾页被放行；
    2. 纯数字串（如专业代码 `085409`）被打碎成 `08/54/09`，且 query 与
       haystack 切分错位（`08` vs ` 0/85/40`），导致真实结果被误杀。
    故：分词前统一做 NFKC + 去除全部空白；回退时 ASCII 字母数字按整段
    `[A-Za-z0-9]+` 消费（专业代码/年份/院校代码保持完整）。

    [前视规则] 未知字若紧邻一个词典词（如"问|复试线"），只前进 1 格，
    禁止把已知词的词首吞进 2-gram（否则"问复试线"切成 问复/试线，
    "复试分数线"永不出现）。其余未知串仍按 2-gram 回退，保留未知词信号。
    """
    if lexicon is None:
        lexicon = get_lexicon()

    # 统一归一化：NFKC + 小写 + 去除全部空白。
    # 词典内无任何含空白词条，去空白不影响最大正向匹配，且能根除空白 token。
    text = re.sub(r"\s+", "", normalize_text(text or ""))

    out: list[str] = []
    i = 0
    text_len = len(text)

    while i < text_len:
        # 最大正向匹配（命中即归一输出）
        word = _longest_at(text, i, lexicon)
        if word is not None:
            out.append(_canonical_word(word))
            i += len(word)
            continue

        ch = text[i]
        # ASCII 字母数字按整段消费：专业代码 085409、年份 2027、院校代码
        # 必须保持完整，否则 query/haystack 两侧切分错位即误杀。
        if ch.isascii() and ch.isalnum():
            m = re.match(r"[A-Za-z0-9]+", text[i:])
            assert m is not None
            out.append(_canonical_word(m.group(0)))
            i += len(m.group(0))
            continue
        # 前视：未知字后紧邻词典词时只进 1 格
        if i + 1 < text_len and _longest_at(text, i + 1, lexicon) is not None:
            if ch.strip():
                out.append(_canonical_word(ch))
            i += 1
            continue
        # 回退策略：2-gram 切分（避免产生长串 token）
        # 对于未登录词，按 2 字切分，保证不会有超长 token
        if i + 2 <= text_len:
            out.append(_canonical_word(text[i:i+2]))
            i += 2
        else:
            # 最后 1 个字符，单独成 token
            if text[i].strip():  # 忽略纯空白
                out.append(_canonical_word(text[i]))
            i += 1

    # 过滤掉长度 < 2 的 token（单字除外某些有意义的，如"分""线"）
    meaningful_singles = {"分", "线", "年", "届", "级"}
    return [
        t for t in out
        if len(t) >= 2 or t in meaningful_singles
    ]


def canonical_token(token: str) -> str:
    """术语规范化（同义词归并）

    Args:
        token: 原始 token

    Returns:
        规范化后的 token

    Examples:
        >>> canonical_token("复试线")
        '复试分数线'

        >>> canonical_token("考纲")
        '考试大纲'
    """
    return SYNONYM_CANONICALS.get(token, token)


def segment_and_normalize(text: str) -> list[str]:
    """分词 + 规范化（一步到位）

    Args:
        text: 待处理文本

    Returns:
        分词并规范化后的 token 列表

    Examples:
        >>> segment_and_normalize("华科复试线多少分")
        ['华科', '复试分数线', '多少', '分']
    """
    tokens = segment(text)
    return [canonical_token(t) for t in tokens]


def normalize_text(text: str) -> str:
    """文本标准化（NFKC + 转小写）"""
    if not text:
        return ""
    import unicodedata
    return unicodedata.normalize("NFKC", str(text)).lower()


canonical = canonical_token


if __name__ == "__main__":
    # 验证用例（对照《搜索能力评估报告》）
    test_cases = [
        "华中科技大学 计算机 复试线",
        "华中科技大学计算机复试线",
        "华科计算机考研复试线多少分",
        "南方医科大学085409今年招多少人",
        "浙江大学2027考研招生简章",
    ]

    print("=== 中文分词验证 ===\n")
    for text in test_cases:
        tokens = segment(text)
        normalized = segment_and_normalize(text)
        print(f"原文: {text}")
        print(f"分词: {tokens}  ({len(tokens)} tokens)")
        print(f"规范: {normalized}")
        print()
