"""题号识别；行首/空白边界严格识别，仅「第N题」「【N】」允许句中出现。

[缺陷修复·单行连写作答漏判] 考生习惯把多题答案连写在同一行
（``…程序正当。第2题：…。第3题：…``）。旧实现的题号标记只认**行首或空白
边界**，句中出现的题号一个都匹配不到 → 第 2/3 题整题判为「未提交本题答案」，
而换行分隔的同款作答全部正常（阳性对照，证明是判据缺陷）。
现对**语义唯一**的两种标记放宽边界：
  * 「第N题」——完整词形，答案正文里几乎不可能出现；
  * 「【N】」「[N]」——正文列举极少用这种括号。
而「（N）」「N.」「N、」与答案正文里的数字列表无法区分
（``三种情形：1.…2.…``），必须继续严守行首/空白边界，否则会把正文切成新题。
"""
import re
import unicodedata

_CN = {c: i for i, c in enumerate("零一二三四五六七八九")}

# 严格边界：行首或空白（原行为）
_BOUNDARY_STRICT = r"(?:^|(?<=\s))"
# 放宽边界：行首、空白，或紧跟在句读/收尾标点之后（句中连写的题号）
_BOUNDARY_RELAXED = r"(?:^|(?<=[\s。．.,，、;；:：!！?？…）)\]】\"']))"

_CN_Q = r"(?:第\s*(?P<cn>[0-9零一二三四五六七八九十百]+)\s*题\s*[:：.、]?)"
_BRACKET_CN = r"(?:[【\[]\s*(?P<brc>\d+)\s*[】\]])"
_BRACKET = r"(?:[（(]\s*(?P<br>\d+)\s*[）)])"
_PLAIN = r"(?:(?P<num>\d+)\s*[.．、:：)）](?!\d))"

_MARKER_RELAXED = re.compile(
    _BOUNDARY_RELAXED + r"(?:" + _CN_Q + r"|" + _BRACKET_CN + r")\s*", re.MULTILINE)
_MARKER_STRICT = re.compile(
    _BOUNDARY_STRICT + r"(?:" + _BRACKET + r"|" + _PLAIN + r")\s*", re.MULTILINE)

# (正则, 该正则可能产出的题号捕获组名) —— 各正则只定义自己的组，
# 取号前必须按组名存在性挑选，否则跨正则取组会 IndexError。
_MARKER_PATTERNS = (
    (_MARKER_RELAXED, ("cn", "brc")),
    (_MARKER_STRICT, ("br", "num")),
)


def _number(value):
    if value.isdigit():
        return int(value)
    if "百" in value:
        left, right = value.split("百", 1)
        return _CN.get(left, 1) * 100 + (_number(right.lstrip("零")) if right.lstrip("零") else 0)
    if "十" in value:
        left, right = value.split("十", 1)
        return _CN.get(left, 1) * 10 + _CN.get(right, 0)
    return _CN.get(value, -1)


def _iter_markers(text):
    """按出现位置合并两条边界策略的题号标记。

    返回 ``[(start, end, 题号), ...]``（按 start 升序）。两条策略命中区间
    重叠时保留先出现者，避免同一处题号被切成两段。
    """
    found = []
    for pat, names in _MARKER_PATTERNS:
        for m in pat.finditer(text):
            raw = next((g for g in (m.group(n) for n in names) if g), "")
            found.append((m.start(), m.end(), _number(raw)))
    found.sort(key=lambda item: (item[0], -item[1]))
    merged = []
    for start, end, num in found:
        if merged and start < merged[-1][1]:
            continue
        merged.append((start, end, num))
    return merged


def parse_answers(text, valid_ids):
    text = unicodedata.normalize("NFKC", str(text)).strip()
    markers = _iter_markers(text)
    answers = {}
    for index, (start, end, number) in enumerate(markers):
        if number not in valid_ids:
            return text, {}, "作答题号不属于本卷，请核对后重新提交。"
        if number in answers:
            return text, {}, "同一题号出现多次，无法可靠区分正文小问与答案标题，请检查题号。"
        stop = markers[index + 1][0] if index + 1 < len(markers) else len(text)
        answers[number] = text[end:stop].strip()
    if not answers and len(valid_ids) > 1:
        return text, {}, "未能按题号定位作答；请使用 1.、（1）或第一题：等题号重新提交。"
    return text, answers, ""
