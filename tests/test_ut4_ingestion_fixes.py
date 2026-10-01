# -*- coding: utf-8 -*-
"""UT4 三沙箱实测缺陷修复回归：真题入库 → 组卷数据链（中性化复现样本）.

覆盖（对应修复标记 INGEST-1..7）：
  1. 题型识别扩充：分析计算题 / A·B·X 型题分段识别，段落声明分值覆盖默认分值表
  2. 选择题归型 + 小数分值（每题 1.5 分 / 逐题（1.5 分））
  3. 尾部「## 参考答案」区按题号回填 standard_answer，答案碎片不进题干/选项
  4. B 型题「6-10. 备选答案」题组头归位为共享备选项
  5. 题号全局唯一化
  6. 考点字段兜底（题面学科关键词 → 通用占位）
  7. 组卷纵深防御：题卡题干段答案区块剥离 + 标准答案提取 + 卷面终检
  8. 不回归：综合题 (1)(2)(3) 子问保持一卡
样本为虚构回忆版内容（无真实院校 / 真实身份信息）。
"""
from tools.skills.material_ingestion import (
    MaterialIngestionPipeline, _per_question_score_from_section,
    _detect_wenke_type, _fmt_score,
)
from tools.skills import exam_composer as exam

# ── 样本一：西医综合回忆版（A/B/X 型选择题 + 逐题小数分值 + 尾部答案区） ──

_MED_TEXT = """# 2024 年考研临床医学综合能力（西医）真题回忆版

> 来源：考生考后回忆整理（非官方完整版），仅供练习参考。

## 一、A 型题（每题 1.5 分，共 90 分）

1. （1.5 分）神经细胞静息电位的形成主要取决于：
   A. Na+内流
   B. K+外流
   C. Ca2+内流
   D. Cl-内流

2. （1.5 分）糖酵解途径中催化不可逆反应的限速酶不包括：
   A. 己糖激酶
   B. 磷酸果糖激酶-1
   C. 丙酮酸激酶
   D. 乳酸脱氢酶

3. （1.5 分）急性炎症早期浸润的主要炎细胞是：
   A. 中性粒细胞
   B. 淋巴细胞
   C. 浆细胞
   D. 嗜酸性粒细胞

4. （1.5 分）原发性高血压最常受累的靶器官是：
   A. 心、脑、肾
   B. 肺、肝、脾
   C. 甲状腺、肾上腺
   D. 皮肤、关节

5. （1.5 分）休克早期（微循环缺血期）机体最重要的代偿机制是：
   A. 交感-肾上腺髓质系统兴奋
   B. 迷走神经兴奋
   C. 肾素-血管紧张素系统抑制
   D. 组织胺大量释放

## 二、B 型题（每题 1.5 分，共 15 分）

6-10. 备选答案：
   A. 缺铁性贫血
   B. 巨幼细胞性贫血
   C. 再生障碍性贫血
   D. 溶血性贫血

6. （1.5 分）维生素 B12 缺乏可导致
7. （1.5 分）骨髓造血功能衰竭可导致

## 三、X 型题（每题 2 分，共 20 分）

11. （2 分）关于心力衰竭的治疗，下列正确的有：
   A. 限制钠盐摄入
   B. 使用利尿剂减轻容量负荷
   C. 所有患者均应使用 β 受体阻滞剂
   D. 血管紧张素转换酶抑制剂可改善预后

12. （2 分）下列关于急性阑尾炎的描述，正确的有：
   A. 转移性右下腹痛是典型表现
   B. 麦氏点压痛是重要体征
   C. 白细胞计数通常升高
   D. 一经诊断均应立即手术，无例外

## 参考答案（回忆版）

1. B  2. D  3. A  4. A  5. A  6. B  7. C
11. ABD  12. ABC
"""

# ── 样本二：信号类自命题回忆版（分析计算题误判 10 分简答 + 子问一卡回归） ──

_COMM_TEXT = """# XX理工大学 801 信号与系统 2024 年真题回忆版

> 考生回忆整理，用于自测与题型训练。

## 一、名词解释（每题 5 分，共 20 分）

1. 单位冲激函数 δ(t)
2. 线性时不变系统（LTI 系统）
3. 奈奎斯特采样定理
4. 系统的频响函数 H(jω)

## 二、简答题（每题 10 分，共 30 分）

1. 简述信号 f(t) 与冲激函数 δ(t) 卷积的物理意义，并说明卷积满足哪些运算律。
2. 说明傅里叶变换与时域卷积定理的内容，并举一个在滤波中的应用。
3. 说明拉普拉斯变换的收敛域与系统因果性、稳定性的关系。

## 三、分析计算题（每题 15 分，共 45 分）

1. 已知 f(t) = e^(-2t)u(t)，h(t) = u(t) - u(t-1)，求零状态响应 y(t) = f(t) * h(t)，写出分段表达式。
2. 求信号 f(t) = e^(-3|t|) 的傅里叶变换 F(jω)，并画出幅度谱简图。
3. 描述某 LTI 系统的微分方程为 y''(t) + 3y'(t) + 2y(t) = f'(t) + f(t)，求系统函数 H(s) 与单位冲激响应 h(t)，并判断系统是否稳定。

## 四、综合题（每题 20 分，共 40 分）

1. 对带限信号 f(t)（最高频率 fm = 5kHz）进行理想抽样：
   (1) 求奈奎斯特抽样率；(2) 若抽样率取 fs = 8kHz，画出抽样后信号频谱示意图；(3) 说明无失真恢复原信号所需的滤波器特性。
2. 已知离散序列 f(k) = {1, 2, 3}（k = 0, 1, 2），h(k) = δ(k) + 2δ(k-1)，用列表法或解析法求卷积和 y(k) = f(k) * h(k)，写出 y(k) 的全部非零值。
"""


def _pipe():
    p = MaterialIngestionPipeline()
    p._force_python = True  # 固定纯 Python 路径，排除 Rust 扩展有无的环境差异
    return p


def _med_chunks():
    return _pipe().chunk_text(_MED_TEXT, "真题回忆_2024")


def _comm_chunks():
    return _pipe().chunk_text(_COMM_TEXT, "真题回忆_2024")


def _find(chunks, prefix):
    """按题干前缀取题（避免 stem[:N] 截断键的空格/长度歧义）。"""
    hits = [c for c in chunks if c.stem.startswith(prefix)]
    assert hits, f"未找到题干前缀: {prefix}"
    return hits[0]


# ───────────── INGEST-1/2：题型识别 + 小数分值 ─────────────

def test_ut4_section_score_declaration_supports_decimal():
    """分段声明分值支持小数：每题 1.5 分此前 (\\d+) 只抓到 "1" → 整条失效。"""
    assert _per_question_score_from_section("一、A 型题（每题 1.5 分，共 90 分）") == 1.5
    assert _per_question_score_from_section("三、分析计算题（每题 15 分，共 45 分）") == 15
    assert _per_question_score_from_section("一、名词解释（每题 5 分，共 20 分）") == 5
    assert _per_question_score_from_section("四、论述题（共 30 分）") == 0


def test_ut4_wenke_type_rules_extended():
    """理工题型段词表：分析计算/综合题/证明题命中，组合段「综合计算题」保持阴性。"""
    assert _detect_wenke_type("三、分析计算题")[0] == "analysis_calc"
    assert _detect_wenke_type("三、计算分析题")[0] == "analysis_calc"
    assert _detect_wenke_type("四、综合题")[0] == "comprehensive"
    assert _detect_wenke_type("四、证明题")[0] == "proof"
    # 既有回归钉住：组合段名不判文科题型（走 essay 兜底 + 分段声明分值）
    assert _detect_wenke_type("三、综合计算题") == (None, 0)


def test_ut4_med_abx_sections_all_choice_with_decimal_scores():
    """A/B/X 型题 9 题全部归选择题，逐题小数分值正确（此前全判大题、1.5 标 5）。"""
    chunks = _med_chunks()
    assert len(chunks) == 9, [c.stem[:10] for c in chunks]
    assert all(c.q_type == "choice" for c in chunks), \
        [c.stem[:10] for c in chunks if c.q_type != "choice"]
    scores = [c.score for c in chunks]
    assert scores == [1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 2, 2], scores
    # 选择题选项完整
    assert len(chunks[0].options) == 4
    assert chunks[0].options[0].startswith("A.")


def test_ut4_comm_analysis_calc_not_short_answer():
    """「分析计算题（每题 15 分）」此前整段继承简答题 10 分 —— 现按段名识别与计分。"""
    chunks = _comm_chunks()
    calc = _find(chunks, "已知 f(t")
    assert calc.q_type == "analysis_calc"
    assert calc.score == 15
    comp = _find(chunks, "对带限信")
    assert comp.q_type == "comprehensive"
    assert comp.score == 20
    # 名词解释 / 简答段不受影响
    assert _find(chunks, "单位冲激").q_type == "term"
    assert _find(chunks, "单位冲激").score == 5
    assert _find(chunks, "简述信号").q_type == "short"
    assert _find(chunks, "简述信号").score == 10


def test_ut4_fmt_score_trims_integer_tail():
    assert _fmt_score(1.5) == "1.5"
    assert _fmt_score(2.0) == "2"
    assert _fmt_score(15) == "15"


# ───────────── INGEST-3：答案区按题号回填，碎片不入题干/选项 ─────────────

def test_ut4_med_answer_section_backfilled_by_number():
    """尾部「## 参考答案（回忆版）1. B 2. D…」按题号写入各题答案（含多选 ABD）。"""
    chunks = _med_chunks()
    assert _find(chunks, "神经细胞").answer == "B"
    assert _find(chunks, "糖酵解途").answer == "D"
    assert _find(chunks, "急性炎症").answer == "A"
    assert _find(chunks, "维生素").answer == "B"
    assert _find(chunks, "骨髓造血").answer == "C"
    assert _find(chunks, "关于心力").answer == "ABD"
    assert _find(chunks, "下列关于").answer == "ABC"


def test_ut4_med_no_answer_fragments_in_stem_or_options():
    """答案行碎片（「B. 2. D 3. A …」）不得混进任何题干或选项（实测污染题 12）。"""
    chunks = _med_chunks()
    for c in chunks:
        blob = c.stem + "\n".join(c.options) + c.answer
        assert "2. D" not in blob, c.stem[:12]
        assert "参考答案" not in blob, c.stem[:12]
        assert "回忆版" not in blob, c.stem[:12]
    # 末题（X 型）选项数量不被答案碎片撑多
    assert len(chunks[-1].options) == 4


# ───────────── INGEST-4：B 型题组共享备选项归位 ─────────────

def test_ut4_med_shared_options_attached_to_b_group():
    """「6-10. 备选答案」挂到 B 型题组作为备选项；不得错挂为上一题标准答案、
    更不得被编成 Rubric 步骤。"""
    chunks = _med_chunks()
    # B 型题两题均挂共享备选项（贫血组 A-D）
    for prefix in ("维生素", "骨髓造血"):
        opts = _find(chunks, prefix).options
        assert len(opts) == 4, (prefix, opts)
        assert opts[0].startswith("A. 缺铁性贫血"), (prefix, opts)
        assert opts[-1].startswith("D. 溶血性贫血"), (prefix, opts)
    # 上一题（A 型末题）的答案 / Rubric 不再被备选答案污染
    shock = _find(chunks, "休克早期")
    assert "缺铁性贫血" not in shock.answer
    assert not any("缺铁性贫血" in r for r in shock.rubric)
    assert not shock.rubric, shock.rubric


# ───────────── INGEST-5：题号全局唯一 ─────────────

def test_ut4_numbers_globally_unique():
    for chunks in (_med_chunks(), _comm_chunks()):
        nums = [c.number for c in chunks]
        assert nums == sorted(nums) and len(set(nums)) == len(nums), nums
    # comm 三段各自从 1 编号（原缺陷：3 个【题号 1】并存）→ 重编为 1..12
    assert [c.number for c in _comm_chunks()] == list(range(1, 13))


# ───────────── INGEST-6：考点字段兜底 ─────────────

def test_ut4_fallback_points_subject_hint():
    """考点兜底：题面学科特征词 → 学科名；无法判定才通用占位；不占 LLM 槽。"""
    pipe = _pipe()
    chunks = _med_chunks()
    assert _find(chunks, "神经细胞").points == []  # 兜底只作用于展示，不写 chunk
    # llm_enrich=False：测试禁网禁真实 API（否则探测到本机真实 key 会打真实计费 LLM）
    def card(prefix):
        return pipe.format_question_card(_find(chunks, prefix), llm_enrich=False)
    assert "`生理学`" in card("神经细胞")
    assert "`生物化学`" in card("糖酵解途")
    assert "`病理学`" in card("急性炎症")
    assert "`内科学`" in card("关于心力")
    assert "`外科学`" in card("下列关于")
    # 无法判定 → 通用占位
    assert "核心综合考点" in card("维生素")
    # 命中特征词的题卡不再千卡同文
    all_cards = [pipe.format_question_card(c, llm_enrich=False) for c in chunks]
    point_values = {ln for t in all_cards for ln in t.splitlines()
                    if "【考查考点】" in ln}
    assert len(point_values) > 1


def test_ut4_fallback_points_signal_subject():
    chunks = _comm_chunks()
    cards = [_pipe().format_question_card(c, llm_enrich=False) for c in chunks]
    joined = "\n".join(cards)
    assert "`信号与系统`" in joined
    assert "【考查考点】`核心综合考点`" not in joined or "核心综合考点" not in joined.split("名词解释")[0]


# ───────────── INGEST-8：子问不切碎（comm 沙箱验证过的行为保持） ─────────────

def test_ut4_composite_subquestions_stay_one_card():
    chunks = _comm_chunks()
    comp = [c for c in chunks if c.stem.startswith("对带限信")]
    assert len(comp) == 1
    stem = comp[0].stem
    assert "(1) 求奈奎斯特抽样率" in stem
    assert "(2) 若抽样率取" in stem
    assert "(3) 说明无失真恢复" in stem


# ───────────── INGEST-7：组卷纵深防御（exam_composer） ─────────────

_SLICE_WITH_ANSWER = """# 题库切片

### 【题号 1】单项选择题（满分: 1.5 分）
- **【题源出处】**：`真题回忆_2024`
- **【考查考点】**：`生理学`
#### 1. 试题原题
神经细胞静息电位的形成主要取决于：

- **A. Na+内流**
- **B. K+外流**

#### 2. 标准答案
> B

---

### 【题号 2】综合应用与解答题（满分: 10 分）
- **【题源出处】**：`真题回忆_2024`
- **【考查考点】**：`核心综合考点`
#### 1. 试题原题
示例综合题：请说明示例原理并给出推导。

#### 3. 步骤级采分点标注 (Rubric)
| 采分步骤 | 赋分要求 | 步骤推导重点与易错点 |
|---|---|---|
| `[+5分] 准确` | 说明示例原理。 | 规范步骤书写，禁止盲目跳步 |

---
"""


def test_ut7_split_answer_from_stem_extracts_and_strips():
    stem, std = exam._split_answer_from_stem(
        "示例题干：静息电位的形成取决于？\n\n#### 2. 标准答案\n> B\n\n#### 3. 步骤级采分点标注 (Rubric)\n| a | b |")
    assert "示例题干" in stem and "标准答案" not in stem and "Rubric" not in stem
    assert std == "B"


def test_ut7_split_answer_from_stem_negative_no_mark():
    stem, std = exam._split_answer_from_stem("示例题干：请用 C# 写出示例算法。")
    assert stem == "示例题干：请用 C# 写出示例算法。"
    assert std == ""


def test_ut7_whitelist_card_answer_stripped_from_paper_text(tmp_path, monkeypatch):
    """题卡题干段混入「标准答案 / 采分点」子小节时：卷面题干剥离该块，
    标准答案进密钥字段（自动判分可用）；防篡改校验基线不受影响。"""
    monkeypatch.setattr(exam, "ROOT", tmp_path)
    ref = tmp_path / "04-专业课" / "参考资料"
    ref.mkdir(parents=True)
    (ref / "题库切片_真题回忆_2024.md").write_text(_SLICE_WITH_ANSWER, encoding="utf-8")
    cards = exam._load_whitelist_cards("pro", need=2)
    assert len(cards) == 2
    c1, c2 = cards
    # 答案区块不进卷面题干
    assert "标准答案" not in c1["question"] and "Rubric" not in c1["question"]
    assert "K+外流" in c1["question"]
    # 标准答案进入密钥字段
    assert c1["standard_answer"] == "B"
    # 无标准答案小节的卡保持空（转人工复核契约）
    assert c2["standard_answer"] == ""
    assert "示例原理" in c2["question"]
    # 防篡改校验基线 = 原始提取口径（未被剥离破坏）
    assert not c1["source_tampered"] and not c2["source_tampered"]


def test_ut7_strip_answer_sections_negative_keeps_text():
    text = "示例题干：参见 #1 号文献。C# 语言示例。"
    assert exam._strip_answer_sections(text) == text
