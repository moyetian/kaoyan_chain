# -*- coding: utf-8 -*-
"""
考研英语长难句搭积木解剖技能 (English Long-Sentence Dissector Skill)
功能：
  1. 句子主干核心提取（主+谓+宾/系+表）
  2. 各种从句层级标记（定语从句、状语从句、名词性从句）
  3. 伴随分词、独立主格、插入语精准剥离
  4. 考研核心生词、派生词与同义替换归纳
  5. 两步翻译法：搭积木直译 ➔ 符合汉语习惯的润色意译
"""

def build_dissection_prompt(sentence):
    """为大模型生成英语长难句专项拆解指令"""
    return f"""你是一位深谙考研英语阅读与长难句命题套路的专属名师。
请对以下给出的考研真题长难句执行【搭积木结构切分法】：

待分析句子：
"{sentence.strip()}"

请严格按照以下 5 个模块规范输出：

### 🧱 1. 【主干骨架抽取】
- 标出句子的最核心主干：**主语 + 谓语 + 宾语/表语**。

### 🌲 2. 【从句与修饰成分解构】
- **从句 1**：[类型：定从/状从/宾从/同位语从句] | 引导词与修饰对象
- **非谓语/插入语**：[分词短语/介词短语/插入结构及其语法作用]

### 🔍 3. 【核心考研词汇与同义替换】
- 列出本句涉及的考研高频核心词、熟词僻义或同义替换词对。

### 📝 4. 【两步翻译法】
- **第一步 (积木直译)**：顺译各积木成分，理清句意逻辑。
- **第二步 (考研润色意译)**：根据中文表达习惯，调整语序，输出优美规范的中文。
"""

def is_english_sentence(text):
    """检测输入是否主要由英文字符组成"""
    clean = text.strip()
    if not clean: return False
    ascii_count = sum(1 for ch in clean if ord(ch) < 128)
    return (ascii_count / len(clean)) > 0.6 and len(clean.split()) >= 4


def health_check() -> dict:
    """[B4] 结构化健康自检：``{"status": READY/DEGRADED/UNAVAILABLE, "reason": str}``。

    纯本地模板技能：用**真调一次最小用例**验证切分指令能构造出来（函数在但
    模板写坏同样会在这里暴露）。
    """
    try:
        prompt = build_dissection_prompt("This is a sample sentence for self-check.")
    except Exception as e:  # noqa: BLE001 - 自检异常必须收敛为可见状态
        return {"status": "UNAVAILABLE",
                "reason": f"长难句切分指令构造失败（{type(e).__name__}: {e}），/dissect 将不可用"}
    if not prompt or not str(prompt).strip():
        return {"status": "UNAVAILABLE", "reason": "长难句切分指令产出为空，/dissect 将不可用"}
    return {"status": "READY", "reason": "五步搭积木切分模板可用（纯本地逻辑，无外部依赖）"}


#: [B3 自描述契约] 桥接为 Agent 工具（skill_bridge.build_self_described_specs 消费）
TOOL_SPEC = {
    "name": "dissect_english_sentence",
    "description": (
        "生成考研英语长难句「五步搭积木」拆解模板：主干骨架抽取 → 从句与修饰成分"
        "解构 → 核心考研词汇与同义替换 → 两步翻译法（直译+润色意译）。传入待拆解"
        "句子后，请按模板的五个模块结构输出拆解结果。"),
    "parameters": {
        "type": "object",
        "properties": {
            "sentence": {"type": "string",
                         "description": "待拆解的英语长难句（考研真题原句）"},
        },
        "required": ["sentence"],
    },
    "level": "read_only",
}


def execute(args, ctx=None):
    """[B3] 桥接入口：返回长难句拆解模板（纯本地，忽略工作区上下文）。"""
    sentence = str((args or {}).get("sentence") or "").strip()
    if not sentence:
        return "Error: 缺少待拆解句子（sentence）"
    prompt = build_dissection_prompt(sentence)
    note = ""
    if not is_english_sentence(sentence):
        note = "\n（提示：检测到输入可能不是英语句子；如为误判请忽略本提示。）\n"
    return (f"【长难句搭积木拆解模板 · 请按以下五个模块输出拆解结果】\n{note}\n"
            f"{prompt}")
