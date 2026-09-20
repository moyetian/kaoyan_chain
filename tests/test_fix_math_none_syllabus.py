# -*- coding: utf-8 -*-
"""R2-A1 / R2-B4 / (b) 回归测试：数二禁区清单对齐 + 「不考数学」的幂等清洗。

三件事（同一处代码 `tools/syllabus_manager.py::apply_syllabus_selection` 的数学分支）：

1. **(b) 单边缺项**：`anti_out_of_syllabus`（会注入 `01-数学/AGENTS.md` 的
   「超纲与题源禁区」段）与同一文件里 `content`（写入 `01-数学/考试大纲.md` 的
   正文）**各自维护了一份禁区清单**。此前 `math2` 的 anti 漏了「斯托克斯公式」、
   `math3` 的 anti 漏了「傅里叶级数」，而正文有 —— 守卫比考纲松 / 两份表各写一半。
   本文件把这张表固化成 `MATH_BANNED_ALIGNMENT`，双向比对，防止再次单边漂移。

2. **R2-A1 幂等**：旧实现只匹配**无括注**形态
   `- 严防超出所考科目大纲的偏题怪题；`，而模板行首次写入后即带括注 →
   第二次起永远匹配不上；且 `MATH_NONE_INFO`/`MATH_CUSTOM_INFO` 没有
   `anti_out_of_syllabus` 键 → 既不注入也**不清洗**，模板里硬编码的数二禁区
   永久残留（不考数学的文科考生拿到「本人不考数学」却仍被警告「数二严禁三重积分」）。
   现改为「保留锚点前缀 + `[^\\n]*` 吃整行 + 按当前科目重写」，
   于是任意切换序列都幂等收敛。

3. **R2-B4 存量文件**：仓库里 `01-数学/考试大纲.md` / `01-数学/AGENTS.md`
   是受控文件，长期停留在「数学二 (302)」而根 `AGENTS.md` 写「不考数学」。
   用修好的代码重跑即可清理（本测试用 tmp 工作区验证清理逻辑本身）。
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from syllabus_manager import (  # noqa: E402
    MATH_SYLLABI,
    apply_syllabus_selection,
)

#: 仓库真实模板路径。仅用于「真实文件也能被清洗」这一条独立断言 ——
#: 其余用例一律用下方**内置模板**，避免测试结果随仓库工作区状态漂移
#: （该文件是可被 `ky subject` / 向导重写的受控文件）。
TEMPLATE_MATH_AGENTS = ROOT / "01-数学" / "AGENTS.md"

#: 禁区锚点：`apply_syllabus_selection` 靠它定位并整行重写。
ANCHOR = "- 严防超出所考科目大纲的偏题怪题"

#: 内置模板：结构与仓库 `01-数学/AGENTS.md` 一致，且带「数二残留」，
#: 使下面的清洗断言永远非空跑。
CANONICAL_TEMPLATE = """# AGENTS.md —— 数学私教系统协议模板

> 本文件是数学学科专属私教协议。AI 在辅导数学时必须严格读取并遵循。

## 0. 身份与辅导目标
- **考试科目**：`数学二 (302)`
- **目标分数**：`110+ 分` (摸底: 60分)
- **每日投入**：`3.0 小时`
- **核心薄弱点**：`无`
- **核心教材与白名单**：`None` (严禁虚构未有资料！)

## 1. 超纲与题源禁区（不可违背的铁律）
- 严防超出所考科目大纲的偏题怪题（【数二严禁超纲禁区】：严禁考查三重积分、第一/二型曲线曲面积分、格林公式、高斯公式、斯托克斯公式、无穷级数、傅里叶级数、向量代数与空间解析几何、欧拉方程、伯努利方程、概率论全科目！）；
- 严禁在本地未放置对应资料时虚构题目出处（严禁自称来自李林880、张宇1000等）；
- 严禁 AI 随性自编缺少严谨答案的题目；若提供巩固题，必须如实标明为【私教自拟变式】。

## 2. 交互指令
- `/math` 数学报到
"""

#: 四个统考条目的「anti_out_of_syllabus ↔ content 正文」禁区清单，按**规范词**比对。
#: anti 常把线面积分合并写作「第一/二型曲线曲面积分」，正文则写「线面积分」，
#: 故两个探针表分开维护；规范词集合必须一致（已知分歧除外）。
ANTI_PROBE = {
    "math2": {
        "三重积分": "三重积分",
        "曲线积分": "曲线曲面",           # anti 合并写作「第一/二型曲线曲面积分」
        "曲面积分": "曲线曲面",
        "格林公式": "格林公式",
        "高斯公式": "高斯公式",
        "斯托克斯公式": "斯托克斯公式",   # (b)：anti 此前漏此项
        "无穷级数": "无穷级数",
        "傅里叶级数": "傅里叶级数",
        "空间解析几何": "空间解析几何",
        "概率论": "概率论",
        "欧拉方程": "欧拉方程",
        "伯努利方程": "伯努利方程",
    },
    "math3": {
        "空间解析几何": "空间解析几何",
        "三重积分": "三重积分",
        "曲线积分": "曲线曲面",           # 同上，anti 合并写法
        "曲面积分": "曲线曲面",
        "物理应用": "物理应用",
        "傅里叶级数": "傅里叶级数",       # (b)：anti 此前漏此项
        "伯努利方程": "伯努利方程",
        "欧拉方程": "欧拉方程",
        "假设检验": "假设检验",
    },
}

CONTENT_PROBE = {
    "math2": {
        "三重积分": "三重积分",
        "曲线积分": "曲线积分",
        "曲面积分": "曲面积分",
        "格林公式": "格林公式",
        "高斯公式": "高斯公式",
        "斯托克斯公式": "斯托克斯公式",
        "无穷级数": "无穷级数",
        "傅里叶级数": "傅里叶级数",
        "空间解析几何": "空间解析几何",
        "概率论": "概率论",
        "欧拉方程": "欧拉方程",
        "伯努利方程": "伯努利",           # 正文作「伯努利微分方程」
    },
    "math3": {
        "空间解析几何": "空间解析几何",
        "三重积分": "三重积分",
        "曲线积分": "线面积分",           # 正文作「不考三重积分与线面积分」
        "曲面积分": "曲面积分",
        "物理应用": "物理应用",
        "傅里叶级数": "傅里叶级数",
    },
}

#: anti 有、正文未逐字列出的已知分歧（需产品决策，**不得静默增删**）。
#: 新增分歧会让 `test_anti_and_content_are_mutually_aligned` 失败，必须在此登记或修文。
KNOWN_ANTI_ONLY = {
    "math3": {"伯努利方程", "欧拉方程", "假设检验"},
}


@pytest.fixture
def ws(tmp_path):
    """造一个最小工作区：只放 01-数学/AGENTS.md（其余父目录由原子写自动创建）。"""
    d = tmp_path / "ws"
    (d / "01-数学").mkdir(parents=True)
    (d / "01-数学" / "AGENTS.md").write_text(CANONICAL_TEMPLATE, encoding="utf-8")
    return d


def _apply(ws, math_key):
    apply_syllabus_selection(
        math_key=math_key,
        eng_key="eng1",
        pro_type="408",
        pro_name="408 计算机学科专业基础",
        auto_write=True,
        workspace_root=ws,
    )


def _agents(ws):
    return (ws / "01-数学" / "AGENTS.md").read_text(encoding="utf-8")


def _outline_head(ws):
    return (ws / "01-数学" / "考试大纲.md").read_text(encoding="utf-8").splitlines()[0]


def _anchor_lines(ws):
    return [l for l in _agents(ws).splitlines() if ANCHOR in l]


def test_canonical_template_is_non_vacuous():
    """前置：内置模板必须真的含数二残留，否则下面的清洗断言就是空跑。"""
    assert "数学二" in CANONICAL_TEMPLATE
    assert "三重积分" in CANONICAL_TEMPLATE
    assert len([l for l in CANONICAL_TEMPLATE.splitlines() if ANCHOR in l]) == 1


def test_real_repo_template_can_be_cleaned(tmp_path):
    """R2-B4：用仓库里那份**真实** `01-数学/AGENTS.md` 跑一遍清洗逻辑。

    该文件是受控文件，可能处于「数学二 (302)」残留态；无论当前是哪种状态，
    切成「不考数学」后都不允许再出现数二痕迹（因此本用例不会因为
    该文件被刷新成「本人不考数学」而误报失败）。
    """
    if not TEMPLATE_MATH_AGENTS.exists():
        pytest.skip(f"仓库模板不存在: {TEMPLATE_MATH_AGENTS}")

    d = tmp_path / "ws"
    (d / "01-数学").mkdir(parents=True)
    (d / "01-数学" / "AGENTS.md").write_text(
        TEMPLATE_MATH_AGENTS.read_text(encoding="utf-8"), encoding="utf-8"
    )

    _apply(d, "none")

    text = _agents(d)
    for word in ("数学二", "数二", "三重积分", "斯托克斯公式"):
        assert word not in text, f"真实模板经「不考数学」清洗后仍残留: {word}"
    assert "`不考数学`" in text
    assert _outline_head(d) == "# 01-数学 · 本人不考数学"


def test_none_clears_math2_banlist(ws):
    """R2-A1：切成「不考数学」后，两文件不得再残留任何数二痕迹。"""
    _apply(ws, "none")

    assert _outline_head(ws) == "# 01-数学 · 本人不考数学"
    assert "`不考数学`" in _agents(ws)

    text = _agents(ws)
    for word in ("数学二", "数二", "三重积分", "斯托克斯公式", "格林公式", "概率论"):
        assert word not in text, f"「不考数学」后 01-数学/AGENTS.md 仍残留: {word}"

    outline = (ws / "01-数学" / "考试大纲.md").read_text(encoding="utf-8")
    for word in ("数学二", "三重积分", "斯托克斯公式", "概率论"):
        assert word not in outline, f"「不考数学」后 考试大纲.md 仍残留: {word}"

    lines = _anchor_lines(ws)
    assert len(lines) == 1, f"禁区锚点行应恰好 1 行，实际 {len(lines)} 行"
    assert "无数学超纲禁区" in lines[0]


def test_switch_is_idempotent(ws):
    """R2-A1 核心：math2 → none → math2 → none → custom → math2 反复切换必须收敛。"""
    expect = {
        "math2": ("# 01-数学 · 全国统考【数学二 (302)】官方核心考试大纲", True),
        "none": ("# 01-数学 · 本人不考数学", False),
        "custom": ("# 01-数学 · 院校自主命题数学", False),
    }

    for key in ("math2", "none", "math2", "none", "custom", "math2", "none", "none", "math2"):
        _apply(ws, key)
        head, want_math2 = expect[key]
        assert _outline_head(ws) == head, f"{key} 切换后大纲首行不符"
        text = _agents(ws)
        assert ("数学二" in text) is want_math2, f"{key} 切换后 AGENTS.md 数学二残留状态不符"
        assert (("三重积分" in text) is want_math2), f"{key} 切换后 AGENTS.md 数二禁区状态不符"
        lines = _anchor_lines(ws)
        assert len(lines) == 1, f"{key} 切换后锚点行数异常: {len(lines)}"
        if key == "none":
            assert "无数学超纲禁区" in lines[0]
        elif key == "custom":
            assert "院校自主命题数学" in lines[0]
        else:
            assert "斯托克斯公式" in lines[0], "切回 math2 后禁区清单应恢复（含斯托克斯公式）"


def test_math2_anti_lists_stokes():
    """(b)：数二 anti 必须列「斯托克斯公式」（此前只在正文里有）。"""
    anti = MATH_SYLLABI["math2"]["anti_out_of_syllabus"]
    assert "斯托克斯公式" in anti
    assert "斯托克斯公式" in MATH_SYLLABI["math2"]["content"]


def test_anti_and_content_are_mutually_aligned():
    """(b) 双向对齐：anti 与正文的禁区规范词集合必须一致（已知分歧除外）。

    少数已知分歧（anti 有、正文未逐字列出）登记在 `KNOWN_ANTI_ONLY`；
    除此之外任何单边漂移都会让本测试失败。
    """
    for key in ANTI_PROBE:
        info = MATH_SYLLABI[key]
        anti = info["anti_out_of_syllabus"]
        content = info["content"]

        anti_words = set(ANTI_PROBE[key])
        content_words = set(CONTENT_PROBE[key])
        known = KNOWN_ANTI_ONLY.get(key, set())

        # 方向一：正文列了 → anti 必须列（anti 不得比考纲松）
        missing_in_anti = content_words - anti_words
        assert not missing_in_anti, f"{key} 正文列为禁区但 anti 未列: {sorted(missing_in_anti)}"
        # 方向二：anti 列了 → 正文必须列（anti 不得比考纲严到凭空多出，除非已登记）
        extra_in_anti = anti_words - content_words - known
        assert not extra_in_anti, (
            f"{key} anti 列了正文没有的禁区词: {sorted(extra_in_anti)}"
            f"（若确属真禁区，请补进正文；否则登记进 KNOWN_ANTI_ONLY）"
        )

        for word, probe in ANTI_PROBE[key].items():
            assert probe in anti, f"{key} 的 anti 文案里找不到「{word}」的探针「{probe}」"
        for word, probe in CONTENT_PROBE[key].items():
            assert probe in content, f"{key} 的正文里找不到「{word}」的探针「{probe}」"


def test_known_anti_only_entries_are_still_declared():
    """已知分歧必须仍然存在于 anti 里 —— 若被顺手删掉，本测试提醒同步更新登记表。"""
    for key, words in KNOWN_ANTI_ONLY.items():
        anti = MATH_SYLLABI[key]["anti_out_of_syllabus"]
        for word in words:
            assert word in anti, f"{key} 的 anti 已不含 {word}，请同步清理 KNOWN_ANTI_ONLY"


def test_anchor_survives_repeated_none(ws):
    """幂等机制本身：反复写 none 不得把锚点行吞掉（旧实现的失败形态）。"""
    for _ in range(3):
        _apply(ws, "none")
        assert len(_anchor_lines(ws)) == 1
    # 且仍能切回 math2（锚点还在 → 还能被整行重写）
    _apply(ws, "math2")
    line = _anchor_lines(ws)[0]
    assert "斯托克斯公式" in line
    assert not re.search(r"（（", line), "禁区行出现双重括注，说明重写未吃掉整行"
