"""题号识别；只在行首或空白边界识别标题，避免改写正文里的括号和小数。"""
import re
import unicodedata

_CN = {c: i for i, c in enumerate("零一二三四五六七八九")}
_MARKER = re.compile(
    r"(?:^|(?<=\s))(?:(?:第\s*(?P<cn>[0-9零一二三四五六七八九十百]+)\s*题\s*[:：.、]?)"
    r"|(?:[（(【\[]\s*(?P<br>\d+)\s*[）)】\]])"
    r"|(?:(?P<num>\d+)\s*[.．、:：)）](?!\d)))\s*", re.MULTILINE)


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


def parse_answers(text, valid_ids):
    text = unicodedata.normalize("NFKC", str(text)).strip()
    markers = list(_MARKER.finditer(text))
    answers = {}
    for index, match in enumerate(markers):
        number = _number(match.group("cn") or match.group("br") or match.group("num"))
        if number not in valid_ids:
            return text, {}, "作答题号不属于本卷，请核对后重新提交。"
        if number in answers:
            return text, {}, "同一题号出现多次，无法可靠区分正文小问与答案标题，请检查题号。"
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        answers[number] = text[match.end():end].strip()
    if not answers and len(valid_ids) > 1:
        return text, {}, "未能按题号定位作答；请使用 1.、（1）或第一题：等题号重新提交。"
    return text, answers, ""
