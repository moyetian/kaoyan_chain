# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 文档与页面结构化抽取器 (Document & Page Extractor)

职责：
  1. 解析官方通知 HTML，清洗正文并提取标题、发布日期
  2. 提取拟招生人数、初试科目、自命题代码与复试分数线关键招考事实
  3. 智能发现并解析 PDF 附件（如招生专业目录.pdf、大纲.pdf）
"""

import re
import html
from typing import Dict, Any, List, Optional
from .models import EvidenceObject, current_exam_year
from .evidence_engine import build_evidence

try:  # 加速协同层（装载探测 + 能力协商 + 已知缺陷黑名单）
    import accel as _accel
except ImportError:  # pragma: no cover - 兼容 tools.accel 包式导入
    from tools import accel as _accel

try:
    import ky_rust_ext as _rust
    _HAS_RUST_EXT = True
except ImportError:
    _rust = None
    _HAS_RUST_EXT = False


# ── 拟招生人数抽取模式（HTML 与 PDF 共用，避免两处正则各写一份而逐渐分叉）──
# 覆盖官方公告中的常见表述（实测 11 种表述，修复前仅命中 5 种）：
#   拟招收硕士研究生 60 人 / 拟招收 60 人 / 拟招生 60 人 / 拟招生人数 60 人
#   计划招生 60 人 / 招生计划 60 人 / 招生规模 60 人 / 招生人数 60 人
#   预计招收 60 人 / 招收 60 人 / 全日制拟招 60 人
#   拟录取 60 人 / 招生名额 60 名
# 有意不匹配「报考人数 60 人」「复试 60 人」这类非招生计划口径的数字。
_QUOTA_HEAD = (
    r"(?:"
    r"拟\s*(?:招|录)(?:收|生|录|取|募)?|"          # 拟招/拟招收/拟招生/拟录取
    r"计划\s*招(?:收|生|录)?|"                      # 计划招生
    r"预计\s*招(?:收|生|录)?|"                      # 预计招收
    r"招收|"                                        # 招收 60 人
    r"招(?:生|收|录)\s*(?:人数|计划|规模|名额)|"      # 招生人数/招生计划/招生规模/招生名额
    r"招生\s*(?:人数|计划|规模|名额)"
    r")"
)
_QUOTA_TAIL = (
    r"\s*(?:全日制|非全日制)?\s*"
    r"(?:硕士)?(?:研究生|学位)?\s*"
    r"(?:人数|计划|规模|名额)?\s*"
    r"[:：为约共]?\s*"
    r"(\d{1,5})\s*[人名]"
)
QUOTA_PATTERN = re.compile(_QUOTA_HEAD + _QUOTA_TAIL)

# ── 初试科目抽取模式（与 rust_ext/src/extractor.rs 同语义）──
# 带三位代码的科目：`(302)数学(二)`、`302数学二`、`（814）通信原理`
# 用 (?<!\d)/(?!\d) 排除 `2026年`、`085400` 这类更长数字串的误命中。
_SUBJECT_CODED = re.compile(
    r"[（(]?(?<!\d)(\d{3})(?!\d)[）)]?\s*([\u4e00-\u9fa5]{2,12}(?:[（(][^）)]{1,6}[）)])?)"
)
# 未带代码的裸科目名（作为补充）
_SUBJECT_PLAIN = re.compile(
    r"(思想政治理论|英语[一二]|数学[一二三]|计算机学科专业基础|管理类综合能力|经济类综合能力)"
)



class DocumentExtractor:
    """招考文档与网页内容抽取器"""
    def extract_from_html(
        self,
        html_text: str,
        page_url: str,
        school_name: str,
        target_year: Optional[int] = None,
        source_type: str = "graduate_school",
        ssl_verified: bool = True,
        access_status: str = "OK"
    ) -> List[EvidenceObject]:
        """从官方通知 HTML 提取关键事实并转化为证据对象"""
        evidences: List[EvidenceObject] = []
        if not html_text:
            return evidences

        target_year = target_year or current_exam_year()
        # 1. 抽取标题
        title = self._extract_title(html_text)
        pub_date = self._extract_pub_date(html_text)
        
        # 判断年份
        detected_year = target_year
        year_match = re.search(r"(202[4-9])\s*年?", title)
        if year_match:
            detected_year = int(year_match.group(1))

        # 2. 招生简章 / 专业目录公告本身作为一条证据
        if any(kw in title for kw in ["招生简章", "专业目录", "招考方案", "简章"]):
            ev_notice = build_evidence(
                field_name="官方硕士招生简章与通告",
                value={"title": title, "url": page_url, "published_date": pub_date},
                unit="篇",
                exam_year=detected_year,
                source_type=source_type,
                source_name=f"{school_name} 官方公告",
                source_url=page_url,
                published_at=pub_date,
                target_year=target_year,
                ssl_verified=ssl_verified,
                # 引文锚点用 <title> 内的**原始片段**：title 变量经过实体反转义与
                # 后缀清洗，未必仍是原文子串，直接拿它当引文会造成误判。
                quote=self._title_span(html_text),
                source_text=[html_text, html.unescape(html_text)],
            )
            evidences.append(ev_notice)

        # 3. 提取拟招生人数
        quota_match = QUOTA_PATTERN.search(html_text)
            
        if quota_match:
            quota = int(quota_match.group(1))
            ev_quota = build_evidence(
                field_name="拟招生人数",
                value=quota,
                unit="人",
                exam_year=detected_year,
                source_type=source_type,
                source_name=f"{school_name} 官方通告",
                source_url=page_url,
                published_at=pub_date,
                target_year=target_year,
                ssl_verified=ssl_verified,
                quote=quota_match.group(0),
                source_text=[html_text, html.unescape(html_text)],
            )
            evidences.append(ev_quota)

        # 4. 提取初试科目信息 (如 408, 101, 204 等)
        subjects = self._extract_subjects(html_text)
        if subjects:
            ev_sub = build_evidence(
                field_name="初试科目配置",
                value=subjects,
                unit="门",
                exam_year=detected_year,
                source_type=source_type,
                source_name=f"{school_name} 官方大纲/目录",
                source_url=page_url,
                published_at=pub_date,
                target_year=target_year,
                ssl_verified=ssl_verified,
                quote=(subjects[0] if subjects else None),
                source_text=[html_text, html.unescape(html_text)],
            )
            evidences.append(ev_sub)

        # 5. 发现 PDF 附件
        pdf_links = self._extract_pdf_links(html_text, page_url)
        if pdf_links:
            ev_pdf = build_evidence(
                field_name="官方PDF招生目录附件",
                value=pdf_links,
                unit="个",
                exam_year=detected_year,
                source_type=source_type,
                source_name=f"{school_name} 官方附件",
                source_url=page_url,
                published_at=pub_date,
                target_year=target_year,
                ssl_verified=ssl_verified,
                # 本条的 value 是「链接 + 去标签文本 + 绝对化 URL」的重组结构，
                # 不存在可直接逐字命中的连续片段，故不启用引文闸门（避免误判）。
            )
            evidences.append(ev_pdf)

        return evidences

    def _title_span(self, html_text: str) -> Optional[str]:
        """返回 <title> 标签内的**原始**文本片段，仅用于引文溯源锚点。

        与 :meth:`_extract_title` 的区别：后者会做 HTML 实体反转义与后缀清洗
        （结果可能已不再是原文子串），不能直接当作引文使用，否则引文闸门会误判。
        """
        if not html_text:
            return None
        match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else None

    def _extract_title(self, html_text: str) -> str:
        """提取页面标题 (Rust 加速 + Python 降级)"""
        if _HAS_RUST_EXT and not getattr(self, "_force_python", False):
            try:
                return _rust.extract_title(html_text)
            except Exception:
                pass
        match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
        if match:
            raw_title = match.group(1).strip()
            # 移除常见后缀
            cleaned = re.sub(r"[-_]\s*.*?(?:研究生院|招生网|大学官网)$", "", raw_title)
            return html.unescape(cleaned).strip()
        return "高校研招官方通知"

    def _extract_pub_date(self, html_text: str) -> Optional[str]:
        """提取发布时间"""
        # 常见日期格式：2026-09-04 或 2026年09月04日
        match = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", html_text)
        if match:
            y, m, d = match.groups()
            return f"{y}-{int(m):02d}-{int(d):02d}"
        return None

    def _extract_subjects(self, html_text: str) -> List[str]:
        """提取初试统考与自命题科目 (Rust 加速 + Python 降级)。

        [正确性修复] 旧实现用 7 条独立正则，最后一条 ``(\\(\\d{3}\\)[\\u4e00-\\u9fa5]+)``
        会把 ``(204)英语(二)`` 截断为 ``(204)英语``；该错误条目同样占用 4 个名额，
        导致**真正的自命题科目**（如 ``(814)通信原理``）被挤出结果 —— 对自命题
        考生影响严重。现改为「按三位代码归并 + 同代码取最长名称 + 首次出现顺序」，
        并与 Rust 侧 :file:`rust_ext/src/extractor.rs` 保持同一语义。

        加速策略：Rust 扩展可用时走 Rust 快路径（历史上曾因截断缺陷被 accel
        黑名单禁用，2026-09-14 修复并经双路径一致性回归后解除），否则走纯
        Python 实现；两条路径语义一致（按三位代码归并 + 同代码取最长名称）。
        """
        rust = _accel.prefer("extract_subjects")
        if rust is not None:
            try:
                subjects = rust.extract_subjects(html_text)
                if subjects:
                    return subjects
            except Exception:
                pass

        if not html_text:
            return []

        subjects: List[str] = []
        best: Dict[str, str] = {}      # code -> 最长名称
        order: List[str] = []
        for m in _SUBJECT_CODED.finditer(html_text):
            code, name = m.group(1), m.group(2)
            if not code or not name:
                continue
            if code not in best:
                order.append(code)
                best[code] = name
            elif len(name) > len(best[code]):
                best[code] = name

        for code in order:
            item = f"({code}){best[code]}"
            if item not in subjects:
                subjects.append(item)
            if len(subjects) >= 4:
                return subjects

        # 未带代码的裸科目名（如“英语二”）作为补充
        for m in _SUBJECT_PLAIN.finditer(html_text):
            name = m.group(1)
            if name not in subjects:
                subjects.append(name)
            if len(subjects) >= 4:
                break
        return subjects

    def _extract_pdf_links(self, html_text: str, base_url: str) -> List[Dict[str, str]]:
        """提取页面中的 PDF 下载链接与说明 (Rust 加速 + Python 降级)"""
        if _HAS_RUST_EXT and not getattr(self, "_force_python", False):
            try:
                rust_links = _rust.extract_pdf_links(html_text, base_url)
                if rust_links:
                    return rust_links
            except Exception:
                pass
        results = []
        matches = re.findall(r"<a[^>]+href=[\"']([^\"']+\.pdf)[\"'][^>]*>(.*?)</a>", html_text, re.IGNORECASE)
        for link, text in matches[:5]:
            clean_text = re.sub(r"<[^>]+>", "", text).strip() or "招生专业目录/自命题大纲 PDF"
            full_url = link
            if not link.startswith("http"):
                import urllib.parse
                full_url = urllib.parse.urljoin(base_url, link)
            results.append({"name": clean_text, "url": full_url})
        return results

    def extract_from_pdf(
        self,
        pdf_path_or_bytes: Any,
        school_name: str,
        source_url: str = "",
        target_year: Optional[int] = None,
        major_keyword: Optional[str] = None,
        ssl_verified: bool = True
    ) -> List[EvidenceObject]:
        """
        从招生专业目录或大纲 PDF 纯文本中抽取初试科目组合、专业方向与拟招计划

        :param ssl_verified: PDF 下载过程是否通过完整 SSL 证书链核验。
                             [P0 修复] 经 SSL 降级通道获取的 PDF 必须传 False，
                             否则抽出的招生人数/科目会以 VERIFIED 级别进入研报。
        """
        target_year = target_year or current_exam_year()
        from pathlib import Path
        text = ""
        
        # 1. 尝试使用 pypdf 提取
        try:
            import io
            import pypdf
            source_input = io.BytesIO(pdf_path_or_bytes) if isinstance(pdf_path_or_bytes, bytes) else str(pdf_path_or_bytes)
            reader = pypdf.PdfReader(source_input)
            extracted_pages = []
            for page in reader.pages[:25]:
                t = page.extract_text()
                if t:
                    extracted_pages.append(t)
            text = "\n".join(extracted_pages)
        except Exception:
            # 2. 降级容错：直接从文件字节串或纯文本中抽取
            if isinstance(pdf_path_or_bytes, bytes):
                raw = pdf_path_or_bytes
                matches = re.findall(rb"\((.*?)\)\s*Tj", raw)
                if matches:
                    text = " ".join([m.decode("utf-8", errors="ignore") for m in matches if len(m) > 2])
                else:
                    text = raw.decode("utf-8", errors="ignore")
            elif isinstance(pdf_path_or_bytes, (str, Path)):
                p = Path(pdf_path_or_bytes)
                if p.exists():
                    raw = p.read_bytes()
                    matches = re.findall(rb"\((.*?)\)\s*Tj", raw)
                    if matches:
                        text = " ".join([m.decode("utf-8", errors="ignore") for m in matches if len(m) > 2])
                    else:
                        text = raw.decode("utf-8", errors="ignore")
                elif isinstance(pdf_path_or_bytes, str) and ("\n" in pdf_path_or_bytes or " " in pdf_path_or_bytes):
                    text = pdf_path_or_bytes

        if not text:
            return []

        evidences = []

        # 抽取专业代码与名称 (例如 085404 计算机技术)
        # 用 finditer 而非 findall：需要 group(0) 的逐字匹配跨度作为引文锚点
        majors_found = list(re.finditer(r"(\d{6})\s*([^\d\s\n,，。]{2,15})", text))
        if majors_found:
            filtered = []
            anchor = None
            for _m in majors_found:
                code, name = _m.group(1), _m.group(2)
                if not major_keyword or (major_keyword in code or major_keyword in name):
                    filtered.append(f"{code} {name}")
                    if anchor is None:
                        anchor = _m.group(0)
            if filtered:
                ev_majors = build_evidence(
                    field_name="PDF招生目录专业清单",
                    value=filtered[:8],
                    unit="个",
                    exam_year=target_year,
                    source_type="graduate_school",
                    source_name=f"{school_name} 官方招生简章/专业目录 (PDF文件)",
                    source_url=source_url,
                    target_year=target_year,
                    ssl_verified=ssl_verified,
                    quote=anchor,
                    source_text=[text],
                )
                evidences.append(ev_majors)

        # 抽取初试科目 (408、政治、英语等)
        subjects = self._extract_subjects(text)
        if subjects:
            ev_sub = build_evidence(
                field_name="PDF大纲/目录初试科目",
                value=subjects,
                unit="门",
                exam_year=target_year,
                source_type="graduate_school",
                source_name=f"{school_name} 官方初试大纲 (PDF文件)",
                source_url=source_url,
                target_year=target_year,
                ssl_verified=ssl_verified,
                quote=(subjects[0] if subjects else None),
                source_text=[text],
            )
            evidences.append(ev_sub)

        # 抽取拟招生计划
        quota_match = QUOTA_PATTERN.search(text)
        if quota_match:
            quota = int(quota_match.group(1))
            ev_quota = build_evidence(
                field_name="PDF拟招生计划人数",
                value=quota,
                unit="人",
                exam_year=target_year,
                source_type="graduate_school",
                source_name=f"{school_name} 官方招生简章 (PDF文件)",
                source_url=source_url,
                target_year=target_year,
                ssl_verified=ssl_verified,
                quote=quota_match.group(0),
                source_text=[text],
            )
            evidences.append(ev_quota)

        return evidences


# 模块级便捷函数
def _extract_title(html_text: str) -> str:
    return DocumentExtractor()._extract_title(html_text)


def _extract_subjects(html_text: str) -> List[str]:
    return DocumentExtractor()._extract_subjects(html_text)

