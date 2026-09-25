# -*- coding: utf-8 -*-
"""题源溯源 ID —— 每道题的可追溯身份（C3：可证阶段）。

背景（升级规划 C3）：题卡此前只有自由文本「题源出处」与枚举 ``origin``，
无法回答两个问题 ——
  ① 这道题是不是之前那道题（跨文件去重 / 幂等迁移）；
  ② 题干有没有被改动过（防篡改）。
本模块提供结构化身份 ``QuestionSource``，并在题卡 Markdown 里落一行
``- **【题源ID】**：`origin-xxxxxxxxxxxx` ``。

设计要点
--------
1. **checksum 与既有指纹同口径**：归一化 = 去掉**全部空白字符**（与
   ``exam_grading._question_fingerprint`` 的 48 字截断同源），但改用 sha256
   摘要 —— 后者「只比前 48 字」，长题干尾部被改动时判不出来。
2. **source_id 人类可读且幂等**：``f"{origin}-{checksum[:12]}"`` —— 同题干
   同来源恒得同一 ID（backfill 可安全重复执行），且从 ID 一眼看出来源类别；
   ``origin`` 或题干任一变化都会得到不同 ID。
3. **不变量由性质测试锁定**（``tests/test_c3_question_source.py``，500 组
   随机输入）：同输入恒同 ID、异输入必异 ID、``verify`` 当且仅当题干未被
   改动时为真、``parse_source_id`` 与 ``build_source_id`` 互逆。

边界（刻意不做）
----------------
* 不做加密签名：``checksum`` 防的是「题干被无意改动 / 张冠李戴」，不是
  防有意的伪造（那需要密钥，与本地单机场景不符）；
* 不改写 ``origin`` 语义：类别枚举仍是唯一事实源，本模块只是把常量从
  ``exam_composer`` 迁到此处集中定义。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # 脚本式路径（tools/ 已在 sys.path）
    from ky_io import atomic_write_text
except ImportError:  # pragma: no cover - 包式导入（tools.skills.question_source）
    from tools.ky_io import atomic_write_text


# ════════════════════════════════════════════════════════════════
# 题源类别（单一事实源；exam_composer 从这里导入以保持既有名字）
# ════════════════════════════════════════════════════════════════

#: 题卡 origin 字段取值（写入每张题卡与答案密钥，供题源声明 / 判卷端 / 评测区分）
ORIGIN_MISTAKE = "mistake"              # 错题本（到期 / 未掌握错题）
ORIGIN_WHITELIST = "whitelist"          # 参考资料/题库切片_*.md 白名单真题卡
ORIGIN_SYNTHETIC_LLM = "synthetic_llm"  # 按薄弱点由大模型命制（含参考答案，非真题）
ORIGIN_PLACEHOLDER = "placeholder"      # 考纲级设问框架（无答案；仅 allow_placeholder 时产出）

ALL_ORIGINS: Tuple[str, ...] = (
    ORIGIN_MISTAKE, ORIGIN_WHITELIST, ORIGIN_SYNTHETIC_LLM, ORIGIN_PLACEHOLDER,
)

#: 真实题源（据此判断「是否有资格组卷」）
REAL_ORIGINS: Tuple[str, ...] = (ORIGIN_MISTAKE, ORIGIN_WHITELIST, ORIGIN_SYNTHETIC_LLM)


# ════════════════════════════════════════════════════════════════
# 归一化 / 摘要 / ID 编解码
# ════════════════════════════════════════════════════════════════

#: 归一化：去掉全部空白字符（空格 / 换行 / 制表符 / 全角空格 \u3000 等）。
#: ``\s`` 在 Python 的 str 正则里是 Unicode 感知的，故全角空格也覆盖。
_WS_RE = re.compile(r"\s+")

#: checksum 取 sha256 前 16 位十六进制（64 bit；同库内碰撞概率可忽略）
CHECKSUM_HEX_LEN = 16

#: source_id 里嵌的摘要前缀（12 位 = 48 bit；ID 更短便于人眼核对）
SOURCE_ID_HASH_LEN = 12

#: source_id 分隔符：``{origin}-{checksum前缀}``。origin 本身不含连字符（见常量表）
_ID_SEP = "-"


def normalize_stem(stem: Any) -> str:
    """题干归一化：去掉全部空白字符。

    与 ``exam_grading._question_fingerprint`` 同一口径 —— 这样题卡从渲染
    （Markdown 换行/缩进）到解析（``strip()``）的空白差异不会影响身份判定。
    """
    return _WS_RE.sub("", str(stem if stem is not None else ""))


def compute_checksum(stem: Any) -> str:
    """题干摘要：``sha256(归一化题干)`` 的前 16 位十六进制。"""
    return hashlib.sha256(normalize_stem(stem).encode("utf-8")).hexdigest()[:CHECKSUM_HEX_LEN]


def build_source_id(origin: str, checksum: str) -> str:
    """由来源类别与摘要构造 source_id（纯函数，无副作用）。"""
    return f"{str(origin)}{_ID_SEP}{str(checksum)[:SOURCE_ID_HASH_LEN]}"


def parse_source_id(source_id: Any) -> Optional[Tuple[str, str]]:
    """解析 ``origin-摘要前缀``；格式非法返回 ``None``。

    返回 ``(origin, checksum_prefix)``。注意解析出的只是**前缀**，
    完整摘要以题卡里的 ``checksum`` 为准（见 :meth:`QuestionSource.verify`）。
    """
    text = str(source_id or "").strip()
    if not text or _ID_SEP not in text:
        return None
    origin, _, digest = text.partition(_ID_SEP)
    if origin not in ALL_ORIGINS or not digest:
        return None
    if not re.fullmatch(r"[0-9a-f]+", digest):
        return None
    return origin, digest


# ════════════════════════════════════════════════════════════════
# 数据模型
# ════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class QuestionSource:
    """题源溯源身份（不可变）。

    * ``source_id``    —— ``{origin}-{checksum[:12]}``，人眼可读的唯一标识
    * ``origin``       —— 来源类别（:data:`ALL_ORIGINS` 之一）
    * ``verified``     —— 是否已核验为官方来源（与 material_ingestion 的
                          ``[VERIFIED]`` / ``[USER_IMPORTED]`` 认证戳对齐）
    * ``syllabus_ref`` —— 考纲锚点（考点名；可为空）
    * ``checksum``     —— 题干摘要（sha256 前 16 位），防篡改校验用
    """

    source_id: str
    origin: str
    verified: bool = False
    syllabus_ref: str = ""
    checksum: str = ""

    # ── 构造 ──
    @classmethod
    def build(
        cls,
        stem: Any,
        origin: str,
        *,
        verified: bool = False,
        syllabus_ref: str = "",
    ) -> "QuestionSource":
        """从题干与来源类别现场构建（题干 → checksum → source_id）。"""
        checksum = compute_checksum(stem)
        return cls(
            source_id=build_source_id(origin, checksum),
            origin=str(origin),
            verified=bool(verified),
            syllabus_ref=str(syllabus_ref or ""),
            checksum=checksum,
        )

    # ── 校验 ──
    def verify(self, stem: Any) -> bool:
        """题干是否与身份一致（未被改动）。

        无 ``checksum`` 的身份（存量数据未 backfill 时）一律返回 ``False``
        —— 「无源即拒」的判定依据就在这里：宁可让调用方走补录流程，
        也不默认放行一道身份不明的题。
        """
        if not self.checksum:
            return False
        return self.checksum == compute_checksum(stem)

    # ── 序列化 ──
    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "origin": self.origin,
            "verified": self.verified,
            "syllabus_ref": self.syllabus_ref,
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "QuestionSource":
        """从 dict 还原；缺字段按默认值处理（不抛异常，便于解析历史数据）。"""
        d = data if isinstance(data, dict) else {}
        return cls(
            source_id=str(d.get("source_id", "") or ""),
            origin=str(d.get("origin", "") or ""),
            verified=bool(d.get("verified", False)),
            syllabus_ref=str(d.get("syllabus_ref", "") or ""),
            checksum=str(d.get("checksum", "") or ""),
        )


# ════════════════════════════════════════════════════════════════
# 题卡 Markdown 读写
# ════════════════════════════════════════════════════════════════

#: 题卡里的字段名（渲染与解析共用，避免两处字面量漂移）
SOURCE_ID_FIELD = "题源ID"

#: 校验和字段名（与 SOURCE_ID_FIELD 同权：任一出现即视为"声明过身份"）
CHECKSUM_FIELD = "题源校验和"

#: 题源ID 行（题库切片 / 错题记录统一使用）
SOURCE_ID_LINE_PREFIX = f"- **【{SOURCE_ID_FIELD}】**："

#: 题干段标记（渲染 / 提取 / 元数据区边界三方共用的单一事实源）。
#: 不带行首锚定 —— 标记被缩进时仍须命中（见 :data:`_STEM_START` 注释）。
_STEM_MARK = r"####\s*\d+\s*[.、]?\s*试题原题"

#: 题干提取：与 ``exam_composer._load_whitelist_cards`` 的解析口径**同一正则**
#: （单一事实源 —— 两处若各自维护，渲染侧算出的 checksum 会在解析侧校验失败）。
_STEM_RE = re.compile(_STEM_MARK + r"\s*\n(.*?)(?=\n-{3,}|\Z)", re.DOTALL)

#: 错题记录题干段标记（```text 围栏，error_logger 的卡片模板）
_MISTAKE_MARK = r"-\s*\*\*题干设问\*\*[：:]"

#: 错题记录的题干提取
_MISTAKE_STEM_RE = re.compile(_MISTAKE_MARK + r"\s*\n```text\n(.*?)\n```", re.DOTALL)

#: 题源ID 行解析（容忍全角/半角冒号与反引号包裹）
_SOURCE_ID_LINE_RE = re.compile(
    rf"-\s*\*\*【{SOURCE_ID_FIELD}】\*\*[：:]\s*`?([A-Za-z0-9\-]+)`?")

#: 校验和行（与题源ID 分开存，便于「ID 被改动」与「题干被改动」两类异常区分）
_CHECKSUM_LINE_RE = re.compile(
    rf"-\s*\*\*【{CHECKSUM_FIELD}】\*\*[：:]\s*`?([0-9a-f]{{8,64}})`?")

_CHECKSUM_LINE_PREFIX = f"- **【{CHECKSUM_FIELD}】**："


def extract_card_stem(card_text: str) -> str:
    """从题卡文本提取「试题原题」段（题库切片格式）。"""
    m = _STEM_RE.search(str(card_text or ""))
    return m.group(1).strip() if m else ""


def extract_mistake_stem(card_text: str) -> str:
    """从错题记录卡片提取题干（```text 围栏格式）。"""
    m = _MISTAKE_STEM_RE.search(str(card_text or ""))
    return m.group(1).strip() if m else ""


def find_source_id(card_text: str) -> str:
    """读取题卡里的题源ID（没有则返回空串）。"""
    m = _SOURCE_ID_LINE_RE.search(str(card_text or ""))
    return m.group(1) if m else ""


def find_checksum(card_text: str) -> str:
    """读取题卡里的题源校验和（没有则返回空串）。"""
    m = _CHECKSUM_LINE_RE.search(str(card_text or ""))
    return m.group(1) if m else ""


def source_from_card(card_text: str, *, origin: str, fallback_stem: str = "",
                     kind: str = "whitelist") -> QuestionSource:
    """从题卡读取身份；卡片没有任何身份行时**现场构建**（惰性 backfill，不落盘）。

    这是「无源即拒」的兜底：存量题卡没有身份行时，只要题干可提取，
    就能得到一个可校验的身份 —— 而不是让整张卡因缺字段被拒。

    但**声明过的身份必须自洽**（fail-closed）：只要卡片的**元数据区**
    （题干段之前，见 :func:`has_declared_identity`）出现过「题源ID」或
    「题源校验和」字段名（半声明、值不可解析也算声明），以下任一情形都会
    得到一个 ``verify() == False`` 的身份，调用方据此判为 ``source_tampered``
    并排除：

      * 校验和行与题干不符（题干被改动过）；
      * 校验和行与 ID 内嵌摘要前缀互不自洽（身份行被手工编辑过）；
      * ID 行缺失（或格式非法）但校验和行与题干不符 —— 删掉 ID 行不能豁免；
      * ID 行存在但解析不出摘要前缀（格式被改坏）；
      * 字段名在但值完全不可解析（如校验和写成 ``zzz``）—— 不得退化为存量卡。

    宽容分支（两处，都要求题干本身可信）：
      * 校验和行缺失但 ID 内嵌前缀与题干一致 —— 视为「可重新认证」，按当前题干补全摘要；
      * ID 行缺失但校验和行与题干一致 —— 按当前题干重建 ID。

    Args:
        kind: 卡片格式（``"whitelist"`` 题库切片 / ``"mistake"`` 错题记录），
            决定"元数据区"的边界标记。

    .. note:: **边界（刻意不做）**：两行都被删除的卡片与"从未补录的存量卡"
       在文本上不可区分 —— 无密钥方案无法阻止有意的"重新签发"。本闸门防的是
       **无意改动 / 张冠李戴**，不是伪造（见模块 docstring）。
    """
    text = str(card_text or "")
    zone = _identity_zone(text, kind)
    stem = fallback_stem or extract_card_stem(text) or extract_mistake_stem(text)
    src_id = find_source_id(zone)
    checksum = find_checksum(zone)
    verified = "[VERIFIED" in zone

    if not src_id:
        # 半声明：元数据区出现过身份字段名却无可用 ID 行。题干被改动过即拒
        # （删 ID 行 / 改坏校验和值都不能豁免）；题干未动则按当前题干重建 ID。
        if has_declared_identity(text, kind=kind) and checksum != compute_checksum(stem):
            return QuestionSource(source_id="", origin=str(origin), verified=verified,
                                  syllabus_ref="", checksum="")
        return QuestionSource.build(stem, origin, verified=verified)

    parsed = parse_source_id(src_id)
    if parsed is None:
        # ID 行存在但格式非法 → 身份不可信（checksum 留空 → verify 恒 False）
        return QuestionSource(source_id=src_id, origin=str(origin), verified=verified,
                              syllabus_ref="", checksum="")

    declared_origin, prefix = parsed
    actual = compute_checksum(stem)
    if checksum:
        # 完整声明：ID 内嵌前缀、校验和行、题干三者必须一致
        trustworthy = checksum.startswith(prefix) and checksum == actual
        return QuestionSource(source_id=src_id, origin=declared_origin, verified=verified,
                              syllabus_ref="", checksum=checksum if trustworthy else "")
    # 校验和行缺失：退化为 ID 内嵌 12 位前缀的弱校验，通过则按当前题干补全
    return QuestionSource(source_id=src_id, origin=declared_origin, verified=verified,
                          syllabus_ref="", checksum=actual if actual.startswith(prefix) else "")


# ════════════════════════════════════════════════════════════════
# 存量 backfill
# ════════════════════════════════════════════════════════════════

#: 题库切片分块锚点：渲染侧 / 解析侧 / backfill 三方**共用**（单一事实源）。
#: 解析侧（exam_composer）曾用字面量 ``"### 【题号"`` 切分 —— 与这里口径不同，
#: 会把 ``###  【题号 2】``（多空格）等变体当成同一张卡，误把后卡的 ID 行
#: 算进前卡。共用本常量后两侧同源。
#: 空白只允许**同行**的空格/制表符（``[ \t]*``）—— 此前的 ``\s*`` 会跨行，
#: 把 ``###\n【题号`` 也误切成新卡（[C3 复查 P9]）。
CARD_SPLIT_RE = re.compile(r"(?=^###[ \t]*【题号)", re.MULTILINE)

#: 卡片分块锚点（按格式分派）
_CARD_SPLIT = {
    "whitelist": CARD_SPLIT_RE,
    "mistake": re.compile(r"(?=^##[ \t]*📌)", re.MULTILINE),
}

#: ID 行插入锚点（在该行之后插入）
_INSERT_AFTER = {
    "whitelist": re.compile(r"^-\s*\*\*【题源出处】\*\*[：:].*$", re.MULTILINE),
    "mistake": re.compile(r"^-\s*\*\*错因分类\*\*[：:].*$", re.MULTILINE),
}

#: 题干段起始（元数据区边界）：注入锚点与**身份声明检测**都只在此行**之前**
#: 的元数据区进行。否则题干里若引用了同名字段行，补录侧会拒绝修复（把引用
#: 误当成真身份行）、解析侧会把好卡误判为"被改动"（[C3 复查 P4]）。
#: 刻意**不加行首锚定** —— 题干标记被缩进（``  #### 1. 试题原题``）时仍要命中，
#: 否则 zone 退化为全块、注入落进题干内部，好卡越补越坏（[C3 复查 P3]）。
_STEM_START = {
    "whitelist": re.compile(_STEM_MARK),
    "mistake": re.compile(_MISTAKE_MARK),
}


def _identity_zone(card_text: str, kind: str) -> str:
    """身份行**只应出现**的元数据区（题干段起点之前）。

    找不到题干标记时退化为全文 —— 此时无法圈定边界，由调用方另行防护
    （``backfill_markdown_text`` 对 m_start 未命中直接放弃注入）。
    """
    text = str(card_text or "")
    m = _STEM_START[kind].search(text)
    return text[:m.start()] if m else text


def has_declared_identity(card_text: str, *, kind: str = "whitelist") -> bool:
    """卡片是否**声明过**题源身份（元数据区出现任一身份字段名）。

    判据用**字段名**而非"值可解析"：行在但值被改坏（如校验和写成 ``zzz``）
    同样是"声明过" —— 必须交给校验逻辑判不可信，不得退化为"存量卡"放行
    （[C3 复查 P5] 此前 ``find_checksum`` 解析失败返回空串，``declared``
    随之判 False，改坏值 + 改题干即可静默绕过防篡改闸门）。
    """
    zone = _identity_zone(card_text, kind)
    return (f"【{SOURCE_ID_FIELD}】" in zone) or (f"【{CHECKSUM_FIELD}】" in zone)


def split_card_blocks(text: str) -> List[str]:
    """把题库切片文本切成 ``[头部, 卡1, 卡2, ...]``（每块含 ``### 【题号`` 前缀）。

    解析侧（exam_composer）与补录侧（backfill）必须用同一分块口径 ——
    否则"同一张卡"在两侧边界不同，身份校验会出假阳性。
    """
    return CARD_SPLIT_RE.split(str(text or ""))


def backfill_markdown_text(
    text: str,
    *,
    kind: str = "whitelist",
) -> Tuple[str, int]:
    """为 Markdown 里每张题卡补题源ID + 校验和两行（已声明过身份的跳过）。

    Args:
        text: 题卡 Markdown 全文。
        kind: ``"whitelist"``（题库切片）或 ``"mistake"``（错题记录）。

    Returns:
        ``(新文本, 补写卡片数)``。**幂等**：对已 backfill 的文本再跑返回原文本。

    Notes:
        本函数只落盘**身份**两行；``[VERIFIED]`` 之类的来源认证戳由渲染侧
        （``material_ingestion``）写在卡片里、由解析侧从文本读回 ——
        补录工具不伪造认证（此前签名的 ``verified`` 参数因不落盘而是死参数，
        已移除）。

        两类卡片原样保留、不计入补写数 ——
          * 题干提取失败（卡片结构异常）：宁可不补，也不给结构不明的卡片盖身份；
          * **元数据区已声明过身份**（含"只有 ID 行没有校验和行"的半声明卡、
            字段名在但值不可解析的卡）：不重复注入、也不重新盖章 —— 交给组卷侧
            fail-closed 判定并报 `source_tampered`，由人工修复；补录工具不得替它
            "洗白"。题干内引用身份行格式的文本不算声明（[C3 复查 P4]）。
    """
    origin = ORIGIN_WHITELIST if kind == "whitelist" else ORIGIN_MISTAKE
    split_re = _CARD_SPLIT[kind]
    insert_re = _INSERT_AFTER[kind]
    stem_fn = extract_card_stem if kind == "whitelist" else extract_mistake_stem

    parts = split_re.split(str(text))
    if len(parts) <= 1:
        return text, 0

    changed = 0
    for idx in range(1, len(parts)):
        block = parts[idx]
        stem = stem_fn(block)
        if not stem:
            continue  # 结构异常：不盖身份
        if has_declared_identity(block, kind=kind):
            # 已声明过身份（含"字段名在但值不可解析"）→ 不重复注入、也不"洗白"：
            # 半声明卡保持原样交给组卷侧 fail-closed 判定，由人工修复；
            # 否则追加注入会造成重复身份行，且把被改动的题干重新盖章。
            # 只看元数据区 —— 题干内引用身份行格式的好卡不在此列（[C3 复查 P4]）。
            continue
        m_start = _STEM_START[kind].search(block)
        if not m_start:
            continue  # 圈不出元数据区边界 → 放弃注入（宁可不补，不越补越坏）
        zone = block[:m_start.start()]
        anchor = insert_re.search(zone)
        if not anchor:
            continue
        src = QuestionSource.build(stem, origin)
        # 锚点正则用 ``$``（MULTILINE）匹配行尾、**不含**换行符，故插入文本
        # 自带前导 ``\n``、行间用 ``\n`` 连接，原行的换行符保持不动。
        insert_text = "\n" + "\n".join([
            f"{SOURCE_ID_LINE_PREFIX}`{src.source_id}`",
            f"{_CHECKSUM_LINE_PREFIX}`{src.checksum}`",
        ])
        parts[idx] = block[:anchor.end()] + insert_text + block[anchor.end():]
        changed += 1

    if not changed:
        return text, 0
    return "".join(parts), changed


def backfill_file(path: Path, *, kind: str = "whitelist",
                  dry_run: bool = False) -> int:
    """对单个题卡文件执行 backfill；返回补写卡片数（0 = 无需改动）。

    读取失败（文件不存在 / 非 UTF-8 / 权限不足）会**抛出** OSError /
    UnicodeDecodeError，由调用方决定记入 ``skipped`` 还是中断 ——
    绝不静默返回 0（那会把"读不了"伪装成"无需改动"，让 CLI 的退出码契约失效）。
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    new_text, changed = backfill_markdown_text(text, kind=kind)
    if changed and not dry_run:
        atomic_write_text(p, new_text)
    return changed


def backfill_workspace(root: Optional[Path] = None, *, dry_run: bool = False) -> Dict[str, Any]:
    """扫描工作区存量题卡并补 ID（题库切片 + 错题记录）。

    返回报告：``{"files": [...], "cards": N, "dry_run": bool, "skipped": [...]}``。
    只处理本项目生成的两种 Markdown 题卡格式；文件不可读时跳过并记入
    ``skipped``（不中断整批 —— 与 runner 的容错口径一致）。
    """
    base = Path(root) if root else _default_root()
    report: Dict[str, Any] = {"files": [], "cards": 0, "dry_run": bool(dry_run), "skipped": []}

    targets: List[Tuple[Path, str]] = []
    for subj_dir in sorted(base.glob("0*-*")):
        if not subj_dir.is_dir():
            continue
        for f in sorted((subj_dir / "参考资料").glob("题库切片_*.md")):
            targets.append((f, "whitelist"))
        # 错题目录：英语科目历史上叫「错题与长难句本」（与 error_logger 的口径一致）
        for dirname in ("错题本", "错题与长难句本"):
            for f in sorted((subj_dir / dirname).glob("错题记录_*.md")):
                targets.append((f, "mistake"))

    for f, kind in targets:
        try:
            changed = backfill_file(f, kind=kind, dry_run=dry_run)
        except Exception as e:  # noqa: BLE001 - 单个文件失败不拖垮整批
            report["skipped"].append({"file": str(f), "reason": f"{type(e).__name__}: {e}"})
            continue
        if changed:
            report["files"].append({"file": str(f), "kind": kind, "cards": changed})
            report["cards"] += changed
    return report


def _default_root() -> Path:
    """工作区根：与 ``tools/`` 同级的仓库根（兼容 ``KY_WORKSPACE_ROOT`` 覆盖）。"""
    import os
    env = os.environ.get("KY_WORKSPACE_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent


__all__ = [
    "ORIGIN_MISTAKE", "ORIGIN_WHITELIST", "ORIGIN_SYNTHETIC_LLM", "ORIGIN_PLACEHOLDER",
    "ALL_ORIGINS", "REAL_ORIGINS",
    "QuestionSource",
    "normalize_stem", "compute_checksum", "build_source_id", "parse_source_id",
    "extract_card_stem", "extract_mistake_stem", "find_source_id", "find_checksum",
    "source_from_card", "backfill_markdown_text", "backfill_file", "backfill_workspace",
    "split_card_blocks", "CARD_SPLIT_RE", "has_declared_identity",
    "SOURCE_ID_FIELD", "CHECKSUM_FIELD", "SOURCE_ID_LINE_PREFIX",
]
