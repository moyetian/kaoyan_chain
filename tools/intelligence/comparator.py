# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 双校考研招考横向对比引擎 (School Comparator)

核心功能：
  1. 支持两所目标高校在同学科方向下的全维度横向对标 (ky compare <高校1> <高校2> [专业])
  2. 涵盖初试科目差异（如 408 统考 vs 自命题）、办学层次、历年复试线走势、一志愿保护机制对比
  3. 智能对比提炼两校相对竞争优势与避坑差异
  4. 支持终端格式化对比大盘与 Markdown 深度研报导出
"""

import json
import hashlib
import logging
import re
import threading
import time
from typing import Dict, Any, Optional
from pathlib import Path

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root

from .models import UniversityEntity
from .registry import get_registry, resolve_university
from .chsi_connector import CHSIConnector
from .subject_catalog import (
    format_subject_items,
    is_nursing_major,
    nursing_308_subjects,
    profile_subject_items,
)

_LOG = logging.getLogger(__name__)

# [根因修复·导出文件名非法字符] 落盘前统一走项目的 safe_filename（清洗 Windows
# 非法字符 \ / : * ? " < > | 与控制字符），替代此前只 replace 斜杠的做法。
# [G5 修复·except 内 import] PermissionDeniedError 提到模块顶部统一双路径导入，
# 供下方只读模式兜底使用（不再在 except 体内 import）。
try:
    from ky_io import safe_filename, atomic_write_text, PermissionDeniedError  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import safe_filename, atomic_write_text, PermissionDeniedError  # noqa: E402

ROOT = resolve_workspace_root(__file__)


# [P3 修复·D11] 研报幂等指纹：同一对比任务经不同入口（CLI / TUI / GUI / REPL）
# 反复执行时，旧实现每次都以新文件名落盘，实测同一任务产出 4 份内容重复的研报，
# 造成目录冗余与"到底该信哪一份"的困扰。现按「正文指纹」判重：
# 剥离生成时间等易变行与表格分隔行后取 SHA-256，指纹一致即视为同一份研报。
_VOLATILE_LINE_RE = re.compile(
    r"(生成时间|导出时间|报告时间|时间戳|生成日期|归档时间|timestamp|generated\s*at)",
    re.IGNORECASE,
)
_TABLE_SEP_RE = re.compile(r"^\|?[\s:\-|]+\|?$")

#: 「初试科目证据不足」统一提示（含可操作引导）。
#: [UT2 修复] 此前只说"无法判定"，考生看到后不知道下一步干什么；现补两句
#: 可执行动作：先用取证命令拿官方招生目录，或配置大模型 API 走在线核验。
#: 注意：不引导「把专业目录放入 04-专业课/参考资料/」——compare 的画像链路
#: （人工库 / 在线研究 / 本地降级）并不读取该目录，照写会构成无效引导。
_INSUFFICIENT_SUBJECT_EVIDENCE = (
    "当前证据不足，无法判定两校初试科目是否相似；请核验同年度、同专业及方向的招生目录。"
    "可运行 `ky admission <院校> <专业>` 或 `ky scout <院校> <专业>` 取证官方招生目录后重新对标；"
    "或在配置大模型 API 后重新对标，获取在线核验的科目对比。"
)

#: [F4 修复·408 过度断言] hedge 词：科目记录含这些词说明「408」只是可能性而非确认。
_408_HEDGE_RE = re.compile(r"或|待核验|可能")


def _confirmed_408(subjects) -> bool:
    """科目记录中是否存在**已确认**的 408（排除「408 或院校自命题」类 hedge 表述）。

    [F4 修复·408 过度断言] 画像里常见「(408)计算机学科专业基础或院校自命题」
    这类待核验记录，旧实现用裸子串 ``"408" in m_str`` 判定，把它当成确认并
    对两校输出「均统一采用国家统考 408」的虚假断言（A-P5/B-P5 实测复现）。
    逐条按与 :func:`_analyze_differences` 相同的拼接口径判定：含 408 且不含
    「或 / 待核验 / 可能」才算确认。
    """
    for item in subjects or []:
        code = item.get("code")
        name = item.get("name", "")
        txt = f"({code}){name}" if code else str(name or "")
        if "408" in txt and not _408_HEDGE_RE.search(txt):
            return True
    return False

# [UT4 修复·WEB-3] 省级行政区名单（不含直辖市）：用于识别「仅省级粒度」的
# region 文本（如兜底启发式产出的「河南」），以便与市级粒度（「河南新乡」）
# 区分。直辖市（北京/天津/上海/重庆）省市同体、粒度天然一致，不在列即无需标注。
_PROVINCE_LEVEL_REGIONS = frozenset({
    "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建",
    "江西", "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州",
    "云南", "陕西", "甘肃", "青海", "台湾", "内蒙古", "广西", "西藏", "宁夏",
    "新疆", "香港", "澳门",
})


def normalize_region_granularity(region) -> str:
    """compare 双栏并排时的地区粒度归一（UT4 实测「河南新乡」vs「河南」参差）。

    规则：市级有则原样显示；仅省级（且非直辖市）显式标注「市级待核验」，
    与项目诚实兜底风格一致；未知/待核验文本原样透传。只影响显示层文案。
    """
    text = str(region or "").strip()
    if not text or "待核验" in text:
        return text
    if text in _PROVINCE_LEVEL_REGIONS:
        return f"{text}（市级待核验）"
    return text


def _apply_requested_subject_hint(profile: Dict[str, Any], major_keyword: str) -> Dict[str, Any]:
    """Preserve an explicit 105400/308 candidate subject selection.

    Online research responses are free-form and occasionally return the
    generic ``301/8xx`` template even when the requested major is nursing.
    The caller's explicit major selection is stronger than that fallback, but
    it is labelled as candidate configuration rather than official evidence.
    """
    if not isinstance(profile, dict) or not is_nursing_major(major_keyword):
        return profile
    current = profile_subject_items(profile)
    if any(item.get("code") == "308" for item in current):
        return profile
    updated = dict(profile)
    subjects = nursing_308_subjects()
    updated["exam_subjects"] = subjects
    updated["subject_codes"] = [item["code"] for item in subjects]
    updated["subject_status"] = "candidate_config"
    updated["subject_source"] = "考生档案记录（需以当年招生目录核验）"
    updated["majors"] = [f"({item['code']}){item['name']}" for item in subjects]
    base = str(updated.get("catalog_source") or "").strip()
    updated["catalog_source"] = (
        f"{base}；科目字段来自考生档案，待当年招生目录核验"
        if base else "[CANDIDATE_CONFIG 考生档案记录，待当年招生目录核验]"
    )
    return updated


def report_fingerprint(text) -> str:
    """计算研报正文指纹（忽略时间类易变行与表格分隔行）。"""
    keep = []
    for line in str(text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if _VOLATILE_LINE_RE.search(s):
            continue
        if _TABLE_SEP_RE.match(s):
            continue
        keep.append(s)
    return hashlib.sha256("\n".join(keep).encode("utf-8")).hexdigest()


def find_duplicate_report(directory: Path, report_text: str, prefix: str = "双校考情对比_") -> Optional[Path]:
    """在目录内查找与本份研报指纹一致的既有文件（用于幂等复用）。"""
    fp_new = report_fingerprint(report_text)
    try:
        candidates = sorted(Path(directory).glob(f"{prefix}*.md"))
    except Exception:
        return None
    for cand in candidates:
        try:
            if report_fingerprint(cand.read_text(encoding="utf-8", errors="ignore")) == fp_new:
                return cand
        except Exception:
            continue
    return None


# [W12 P0-2] 单校在线研究的墙钟预算（秒）。两校并行共享同一预算——
# 弱网下 60s 内必有结果（未完成的学校回落本地降级并标注），
# 此前单校研究预算 240s（agentic_research budget_s）且无熔断，
# CLI 实测 120s 被 shell 杀掉（弱网检索慢 + 无界等待）。
# compare(timeout=...) 可覆盖；timeout<=0 表示不熔断（等待全量研究）。
_DEFAULT_COMPARE_TIMEOUT = 60.0


class SchoolComparator:
    """双校招考横向对比分析器"""

    def __init__(self):
        self.registry = get_registry()
        self.chsi = CHSIConnector()

    def compare(
        self,
        school1_query: str,
        school2_query: str,
        major_keyword: str = "计算机",
        save_report: bool = False,
        api_config: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        quick: bool = False
    ) -> Dict[str, Any]:
        """
        对比两所高校在目标专业方向下的关键指标

        :param timeout: [W12 P0-2] 单校在线研究墙钟预算（秒）。None 用默认 60s；
                        <=0 不熔断（全量等待）。超时学校回落本地降级并标注。
        :param quick: [W12 P0-2] 离线模式——跳过在线研究，两校直接取本地降级画像
                      （弱网/演示场景秒级返回；数据源属性如实标注本地库）。
        """
        entity1 = resolve_university(school1_query)
        entity2 = resolve_university(school2_query)

        name1 = entity1.name if entity1 else school1_query
        name2 = entity2.name if entity2 else school2_query

        # 尝试从内置权威数据库提取深度招考指标 (若有)
        # [W11 两校并行] 原串行：两校各 200s 预算 → 最坏 400s 墙钟（真机实测
        # 80.7s 完成）。并行后总墙钟 ≈ 单校最坏，实测应压到 ~40-50s。
        # 安全性：递归守卫 _RESEARCH_DEPTH 是 threading.local——两校各自新线程
        # 均从 depth=0 起，天然隔离；SchoolComparator 实例无运行期可变状态
        # （registry/chsi 仅 __init__ 赋值，方法体不写 self）。
        # [W12 P0-2] 再叠加超时熔断（daemon 线程 + 共享 deadline），
        # 保证弱网下有界返回。
        if quick:
            info1 = self._fallback_profile(name1, major_keyword)
            info2 = self._fallback_profile(name2, major_keyword)
        else:
            info1, info2 = self._get_two_profiles(
                name1, entity1, name2, entity2, major_keyword, api_config,
                timeout=timeout)

        # [UT4 修复·WEB-3] 地区粒度归一：本地兜底/在线画像的 region 粒度参差
        #（实测「河南新乡」vs「河南」并排同表），在此统一为「市级有则显示市，
        # 仅省则显式标注」，下游终端表/Markdown 研报/差异分析三处消费点一并生效。
        for _info in (info1, info2):
            if isinstance(_info, dict):
                _info["region"] = normalize_region_granularity(_info.get("region"))

        # 自动对比分析
        diff_analysis = self._analyze_differences(name1, info1, name2, info2, major_keyword)

        # 格式化输出
        terminal_report = self._format_terminal_table(name1, info1, name2, info2, diff_analysis, major_keyword)
        markdown_report = self._format_markdown_report(name1, info1, name2, info2, diff_analysis, major_keyword)

        saved_path = None
        reused_existing = False
        if save_report:
            out_dir = ROOT / "04-专业课"
            out_dir.mkdir(parents=True, exist_ok=True)
            # [根因修复·导出文件名非法字符] 旧实现只把 "/" 和 "\" 换成 "_"，
            # 未处理 Windows 其余非法字符 ( : * ? " < > | ) 与控制字符：一旦专业名
            # 里含这些字符（如 "085400: 电子信息"），open() 会直接抛 OSError，
            # 导致批量对标或自命题对标时进程崩溃退出。现统一清洗。
            safe_major = safe_filename(major_keyword)
            out_file = out_dir / f"双校对标_{name1}_VS_{name2}_{safe_major}.md"

            # [幂等与消重] 若已存在同名研报且内容指纹一致，直接复用，不重复写盘
            # （避免刷变动时间戳与触发文件监控器）。若内容发生变化则覆盖写。
            fp_new = report_fingerprint(markdown_report)
            if out_file.exists():
                try:
                    fp_old = report_fingerprint(out_file.read_text(encoding="utf-8", errors="ignore"))
                    if fp_old == fp_new:
                        saved_path = str(out_file)
                        reused_existing = True
                except Exception:
                    pass

            if not reused_existing:
                # [B6 修复·safe 绕过] 原裸 write_text 不经 guard_write，
                # --permission=safe 下仍落盘。atomic_write_text 自带守卫；
                # 只读模式下拒绝落盘但保留报告文本（调用方展示终端版）。
                try:
                    atomic_write_text(out_file, markdown_report)
                    saved_path = str(out_file)
                except Exception as _save_exc:
                    if isinstance(_save_exc, PermissionDeniedError):
                        markdown_report += (
                            "\n\n> 🔒 当前为严格只读模式，研报未落盘，"
                            "仅展示本次对比结果。\n")
                    else:
                        raise

        return {
            "school1": name1,
            "school2": name2,
            "name1": name1,
            "name2": name2,
            "info1": info1,
            "info2": info2,
            "profile1": info1,
            "profile2": info2,
            "major": major_keyword,
            "analysis": diff_analysis,
            "differences": diff_analysis,
            "terminal_report": terminal_report,
            "markdown_report": markdown_report,
            "saved_path": saved_path,
            "reused_existing": reused_existing,
            "dedup_note": (
                "同一对比任务研报已存在（内容指纹一致），本次未重复落盘，直接复用既有归档路径。"
                if reused_existing else ""
            ),
        }

    def _get_two_profiles(
        self,
        name1: str,
        entity1: Optional[UniversityEntity],
        name2: str,
        entity2: Optional[UniversityEntity],
        major_keyword: str,
        api_config: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ):
        """[W11] 并行研究两校画像；[W12 P0-2] 叠加超时熔断。

        Returns: ``(info1, info2)``，与 ``_get_school_profile`` 逐键同形
        （降级路径复用 ``dynamic_fallback_profile``，来源标注诚实）。

        线程模型：daemon 线程 + 共享 deadline 的 ``join``。
        - 不用 ThreadPoolExecutor：其上下文管理器退出时会等待全部线程，
          ``future.result(timeout)`` 抛超时后进程仍会被未完成的检索线程拖住。
        - daemon 线程超时后结果丢弃、不阻止进程退出；已完成的学校结果照用。
        - ``timeout<=0`` 表示不熔断（等待全量研究）。
        """
        budget = _DEFAULT_COMPARE_TIMEOUT if timeout is None else float(timeout)
        slots: Dict[int, tuple] = {}

        def _worker(idx: int, school_name: str, entity) -> None:
            try:
                slots[idx] = ("ok", self._get_school_profile(
                    school_name, entity, major_keyword, api_config))
            except Exception as exc:  # pragma: no cover - 防御性
                _LOG.warning("并行研究单校失败（%s）：%s，回落本地降级",
                             school_name, exc)
                slots[idx] = ("err", exc)

        threads = []
        for idx, (nm, ent) in enumerate(((name1, entity1), (name2, entity2))):
            t = threading.Thread(target=_worker, args=(idx, nm, ent),
                                 name=f"ky-compare-{idx}", daemon=True)
            threads.append(t)
            t.start()

        if budget > 0:
            deadline = time.monotonic() + budget
            for t in threads:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                t.join(timeout=remaining)
        else:
            for t in threads:
                t.join()

        out = []
        for idx, nm in enumerate((name1, name2)):
            status, _payload = slots.get(idx, ("timeout", None))
            if status == "ok":
                out.append(_payload)
            elif status == "err":
                out.append(self._fallback_profile(nm, major_keyword))
            else:
                out.append(self._fallback_profile(
                    nm, major_keyword,
                    reason=f"本轮在线研究超时（{budget:.0f}s 预算内未完成），未经在线核验"))
        return out[0], out[1]

    @staticmethod
    def _fallback_profile(school_name: str, major_keyword: str,
                          reason: str = "") -> Dict[str, Any]:
        """本地降级画像（与在线研究同源引擎）。

        :param reason: 非空时附加到 ``catalog_source``（如超时降级说明），
                       保证报告读者能区分「本地库实录」与「在线核验」。
        """
        try:
            from tools.intelligence.agentic_research import get_research_engine
            prof = get_research_engine().dynamic_fallback_profile(
                school_name, major_keyword)
        except Exception as exc2:  # pragma: no cover - 极端兜底
            _LOG.warning("本地降级亦失败（%s）：%s", school_name, exc2)
            prof = {"name": school_name,
                    "catalog_source": "[FALLBACK 研究未完成]",
                    "score_trend": "参照国家线与校自划线"}
        prof = _apply_requested_subject_hint(prof, major_keyword)
        if reason and isinstance(prof, dict):
            _base = str(prof.get("catalog_source") or "").strip()
            prof["catalog_source"] = f"{_base}（{reason}）" if _base else reason
        return prof

    def _get_school_profile(
        self,
        school_name: str,
        entity: Optional[UniversityEntity],
        major_keyword: str,
        api_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """提取高校综合考情画像"""
        try:
            from skills.school_scout import TARGET_SCHOOLS_DB
        except ImportError:
            try:
                from tools.skills.school_scout import TARGET_SCHOOLS_DB
            except ImportError:
                TARGET_SCHOOLS_DB = {}

        # 检查是否命中内置 30+ 权威数据库
        db_item = TARGET_SCHOOLS_DB.get(school_name, {})
        dept_info = None
        if db_item and "pro_departments" in db_item:
            for k, v in db_item["pro_departments"].items():
                major_code = str(v.get("major_code") or "") if isinstance(v, dict) else ""
                if (major_keyword in k or k in major_keyword or
                        (major_code and major_code in major_keyword)):
                    dept_info = v
                    break

        if dept_info:
            level = " / ".join(entity.level) if entity else "全国研招单位"
            region = entity.region if entity else "全国"
            chsi_code = entity.chsi_code if entity else "待查"
            official = entity.official_domain if entity else ""
            graduate = entity.graduate_domain if entity else ""
            majors = dept_info.get("majors", [])
            exam_subjects = dept_info.get("exam_subjects") or dept_info.get("subjects") or []
            subject_status = dept_info.get("subject_status", "catalog_record")
            subject_source = dept_info.get("subject_source", "院校画像库记录")
            # [诚信修复] 该分支数据来自 school_db.py 的**人工整理考情专栏**（8 校），
            # 其中的复试线/报录比/一志愿保护/口碑属人工评述，并非官方原文，
            # 旧标签「OFFICIAL_VERIFIED 院校专栏实录」属来源夸大，现如实改标。
            # [护理 308 链路] 考生档案记录（candidate_config）不得贴「人工整理
            # 考情专栏」标签——该标签暗示已人工核验过。
            catalog_source = (
                "[CANDIDATE_CONFIG 考生档案科目记录，待当年招生目录核验]"
                if subject_status == "candidate_config"
                else "[CURATED_NOTES 人工整理考情专栏]"
            )
            score_trend = dept_info.get("score_trend", "参照国家线与校自划线")
            ratio = dept_info.get("ratio_quota", "以官方最终报录公示为准")
            protect = dept_info.get("protect_first", "遵循教育部统一录取规范")
            reputation = "；".join(dept_info.get("reputation", []))
            pitfalls = "；".join(dept_info.get("pitfalls", []))

            return {
                "name": school_name,
                "code": chsi_code,
                "level": level,
                "region": region,
                "official": official,
                "graduate": graduate,
                "majors": majors,
                "exam_subjects": exam_subjects,
                "subject_codes": [str(item.get("code")) for item in exam_subjects
                                  if isinstance(item, dict) and item.get("code")],
                "subject_status": subject_status,
                "subject_source": subject_source,
                "major_code": dept_info.get("major_code", ""),
                "degree_type": dept_info.get("degree_type", ""),
                "catalog_source": catalog_source,
                "score_trend": score_trend,
                "ratio": ratio,
                "protect": protect,
                "reputation": reputation,
                "pitfalls": pitfalls
            }

        # 未在内置 TARGET_SCHOOLS_DB 命中的高校/专业，调用 Agentic 深度研究引擎获取真实画像（绝不使用离线虚假数据）
        # [多角色实测·卡死修复] 每校在线研究限 200s 预算：两校串行最坏约 6.7 分钟，
        # 超预算自动走本地降级（原无预算实测 600s+ 卡死）。
        from tools.intelligence.agentic_research import research_university_profile
        profile = research_university_profile(school_name, major_keyword, api_config=api_config,
                                              budget_s=200.0)
        return _apply_requested_subject_hint(profile, major_keyword)

    def _analyze_differences(
        self,
        name1: str,
        info1: Dict[str, Any],
        name2: str,
        info2: Dict[str, Any],
        major: str
    ) -> Dict[str, Any]:
        """提炼两校竞争差异与决策建议"""
        # 1. 科目差异。优先读取结构化 exam_subjects；旧画像仍可通过
        # majors 兼容。这样 105400 护理不会再落入 301/8xx 泛化模板。
        subjects1 = profile_subject_items(info1)
        subjects2 = profile_subject_items(info2)
        codes1 = [item["code"] for item in subjects1 if item.get("code")]
        codes2 = [item["code"] for item in subjects2 if item.get("code")]
        m1_str = " ".join(
            f"({item.get('code')}){item.get('name')}" if item.get("code") else item.get("name", "")
            for item in subjects1
        )
        m2_str = " ".join(
            f"({item.get('code')}){item.get('name')}" if item.get("code") else item.get("name", "")
            for item in subjects2
        )
        # [F4 修复·408 过度断言] 仅「含 408 且无 hedge 词」才算确认；裸子串会把
        # 「(408)…或院校自命题」误判为确认并输出「均统一采用」（A-P5/B-P5 复现）。
        confirmed1 = _confirmed_408(subjects1)
        confirmed2 = _confirmed_408(subjects2)
        structured_statuses = {"candidate_config", "catalog_record", "verified"}
        structured_ready = bool(subjects1 and subjects2) and all(
            str(info.get("subject_status", "")).strip() in structured_statuses
            for info in (info1, info2)
        )
        if structured_ready:
            display1 = format_subject_items(subjects1) or "待核验"
            display2 = format_subject_items(subjects2) or "待核验"
            if codes1 == codes2 and display1 == display2:
                subject_diff = (
                    f"两校当前记录的初试科目一致：{display1}；"
                    "科目复习通用度较高，但仍需以当年招生目录核验。"
                )
            else:
                subject_diff = (
                    f"【{name1}】初试科目：{display1} ｜ "
                    f"【{name2}】初试科目：{display2}；科目代码存在差异，"
                    "请分别按当年目录准备。"
                )
        elif confirmed1 and "408" not in m2_str:
            subject_diff = f"【{name1}】采用全国统考 408，【{name2}】包含专业自主命题或待核验"
        elif confirmed2 and "408" not in m1_str:
            subject_diff = f"【{name2}】采用全国统考 408，【{name1}】包含专业自主命题或待核验"
        elif confirmed1 and confirmed2:
            subject_diff = "两校主流专硕/学硕均统一采用国家统考 408（复习通用度极高）"
        elif "408" in m1_str or "408" in m2_str:
            # [F4 修复] 任一侧仅「提及」408（hedge）时不得断言统一统考，如实提示核验。
            subject_diff = (
                "两校初试科目记录含 408 相关表述，但存在「或院校自命题」等待核验成分，"
                "不得据此断言统一统考 408；请以当年招生目录核验。"
            )
        else:
            # 只有画像确实携带已核验的科目来源时，才敢逐条列出初试科目。
            # [LOCAL_DB_VERIFIED] 表示科目来自本地全国高校库（研招网 408 逐校核验数据
            # 与人工核验条目），同样是可追溯来源，故与联网核验同级。
            verified = all(("OFFICIAL_VERIFIED" in str(info.get("catalog_source", "")) or
                            "RESEARCH_VERIFIED" in str(info.get("catalog_source", "")) or
                            "CHSI_VERIFIED" in str(info.get("catalog_source", "")) or
                            "LOCAL_DB_VERIFIED" in str(info.get("catalog_source", "")) or
                            "CURATED_NOTES" in str(info.get("catalog_source", "")))
                           for info in (info1, info2))
            if verified:
                s1_subjs = "、".join(info1.get("majors", []))
                s2_subjs = "、".join(info2.get("majors", []))
                if s1_subjs and s2_subjs:
                    subject_diff = f"【{name1}】初试科目：{s1_subjs} ｜ 【{name2}】初试科目：{s2_subjs}"
                else:
                    # 来源已核验但科目列表为空 → 只能说"本库未收录"，不得断言"符合指导标准"
                    subject_diff = _INSUFFICIENT_SUBJECT_EVIDENCE
            else:
                subject_diff = _INSUFFICIENT_SUBJECT_EVIDENCE

        # 2. 地区与资源（[B3] 防御性取值：LLM 画像可能缺键）
        region_diff = (f"【{name1}】位于 {info1.get('region', '待核验')} ｜ "
                       f"【{name2}】位于 {info2.get('region', '待核验')}")

        # 3. 决策建议
        # [P0 修复] 建议此前无条件推荐「优先参考两校统考 408 对应方向」，
        # 对自命题考生（如 814 信号与系统）有误导性；按学员档案动态调整措辞。
        try:
            _cfg = json.loads((ROOT / "ky_config.json").read_text(encoding="utf-8"))
            _pro_name_cfg = ((_cfg.get("study_plan") or {}).get("pro_name") or "").strip()
        except Exception:
            _pro_name_cfg = ""
        # 显式传入的专业是本次对比的事实边界，不能被工作区配置中的另一位
        # 考生画像覆盖；只有调用方未传专业时才回退到 ky_config.json。
        pro_name = str(major or "").strip() or _pro_name_cfg
        if "408" in pro_name:
            _exam_tip = "若求备战通用性与规避自命题风险，可优先参考两校统考 408 对应方向"
        else:
            _exam_tip = f"学员专业课为「{pro_name or '院校自命题'}」，请分别核验两校该科目大纲与参考书差异"
        # 一志愿保护机制若未核验，不得写成"可结合两校保护机制做取舍"（等于暗示已有结论）
        _prot_verified = all("未核验" not in str(info.get("protect", "")) for info in (info1, info2))
        if _prot_verified:
            _prot_tip = (f"若看重一志愿公平性，可结合两校保护机制（{name1}: {info1.get('protect', '未核验')} ｜ "
                         f"{name2}: {info2.get('protect', '未核验')}）做终极取舍。")
        else:
            _prot_tip = (f"两校一志愿保护机制尚未核验（{name1}: {info1.get('protect', '未核验')} ｜ "
                         f"{name2}: {info2.get('protect', '未核验')}），"
                         "建议查阅两校近三年复试录取细则与拟录取名单后再自行判断。")
        recommendation = f"{_exam_tip}；{_prot_tip}"

        return {
            "subject_diff": subject_diff,
            "subject_codes1": codes1,
            "subject_codes2": codes2,
            "subject_names1": [item.get("name", "") for item in subjects1],
            "subject_names2": [item.get("name", "") for item in subjects2],
            "subject_status1": info1.get("subject_status", "unverified"),
            "subject_status2": info2.get("subject_status", "unverified"),
            "region_diff": region_diff,
            "recommendation": recommendation
        }

    def _format_terminal_table(
        self,
        name1: str,
        info1: Dict[str, Any],
        name2: str,
        info2: Dict[str, Any],
        analysis: Dict[str, Any],
        major: str
    ) -> str:
        """生成彩色终端对比大盘卡片"""
        # [根因修复·窄终端表格不可读] 旧实现把列宽写死 12/34/34（总宽 86 列）：
        # 终端窄于 86 列时整表被终端硬换行，"表中一行"被拆成多行显示，学员无法
        # 对照阅读（终端不提供横向滚动）。现按终端实际宽度自适应收缩两个数据列，
        # 保证「一行一指标」在任何宽度下都成立；宽终端下仍保持原 86 列版式。
        col1_w = 12
        try:
            import shutil as _shutil
            term_w = _shutil.get_terminal_size(fallback=(100, 24)).columns or 100
        except Exception:
            term_w = 100
        # 总宽 = col1_w + " | " + col2_w + " | " + col3_w
        avail = max(48, term_w - 2) - col1_w - 6
        col2_w = col3_w = max(14, avail // 2)
        table_w = col1_w + 6 + col2_w + col3_w

        def _dw(s: str) -> int:
            """估算终端显示宽度：中日韩全角字符按 2 列计"""
            return sum(2 if ord(ch) > 127 else 1 for ch in s)

        def _pad(s: str, width: int) -> str:
            """按显示宽度右侧补空格"""
            gap = width - _dw(s)
            return s + " " * max(0, gap)

        def _col(text: str, width: int) -> str:
            """[P0 修复] 按显示宽度截断并对齐：旧实现按字符数 ljust/截断，
            中文单元格占位失准导致列错位与难看的半截省略号"""
            s = str(text or "")
            if _dw(s) <= width:
                return _pad(s, width)
            out, w = "", 0
            for ch in s:
                cw = 2 if ord(ch) > 127 else 1
                if w + cw > width - 3:
                    break
                out += ch
                w += cw
            return _pad(out + "...", width)

        subject_display1 = format_subject_items(profile_subject_items(info1)) or "待核验"
        subject_display2 = format_subject_items(profile_subject_items(info2)) or "待核验"
        lines = [
            f"\n=== ⚔️ 目标高校招考深度横向对比大盘 · 【{name1}】 VS 【{name2}】 ({major}) ===",
            "-" * table_w,
            f"{_pad('对比维度', col1_w)} | {_col(name1, col2_w)} | {_col(name2, col3_w)}",
            "-" * table_w,
            f"{_pad('教育部代码', col1_w)} | {_col(info1.get('code', '待查'), col2_w)} | {_col(info2.get('code', '待查'), col3_w)}",
            f"{_pad('所在城市', col1_w)} | {_col(info1.get('region', '待核验'), col2_w)} | {_col(info2.get('region', '待核验'), col3_w)}",
            f"{_pad('办学层次', col1_w)} | {_col(info1.get('level', '待核验'), col2_w)} | {_col(info2.get('level', '待核验'), col3_w)}",
            f"{_pad('数据源属性', col1_w)} | {_col(info1.get('catalog_source', ''), col2_w)} | {_col(info2.get('catalog_source', ''), col3_w)}",
            f"{_pad('初试科目', col1_w)} | {_col(subject_display1, col2_w)} | {_col(subject_display2, col3_w)}",
            f"{_pad('复试线走向', col1_w)} | {_col(info1.get('score_trend', '待核验'), col2_w)} | {_col(info2.get('score_trend', '待核验'), col3_w)}",
            f"{_pad('一志愿保护', col1_w)} | {_col(info1.get('protect', '未核验'), col2_w)} | {_col(info2.get('protect', '未核验'), col3_w)}",
            "-" * table_w,
            f"💡 【初试差异】: {analysis['subject_diff']}",
            f"💡 【地区分布】: {analysis['region_diff']}",
            f"🎯 【私教择校建议】: {analysis['recommendation']}",
            "=" * table_w + "\n"
        ]
        return "\n".join(lines)

    def _format_markdown_report(
        self,
        name1: str,
        info1: Dict[str, Any],
        name2: str,
        info2: Dict[str, Any],
        analysis: Dict[str, Any],
        major: str
    ) -> str:
        """生成 Markdown 深度对比研报"""
        subject_display1 = format_subject_items(profile_subject_items(info1)) or "待核验"
        subject_display2 = format_subject_items(profile_subject_items(info2)) or "待核验"
        lines = [
            f"# ⚔️ 考研目标院校横向对比研报 · {name1} VS {name2} ({major})",
            f"> 深度对标办学层次、自划线特征、初试统考/自命题科目、近三年复试线、一志愿保护机制与备考风险",
            f"> ⚠️ 数据说明：复试线走势、报录比等来自本项目内置经验基准库（非实时抓取核验），仅作量级参考，务必以两校研究生院官方公示为准。",
            "",
            "## 📊 1. 关键招考指标横向对标矩阵",
            "| 招考对比维度 | " + name1 + " | " + name2 + " |",
            "|---|---|---|",
            f"| **教育部代码** | `{info1.get('code', '待查')}` | `{info2.get('code', '待查')}` |",
            f"| **所在地区** | {info1.get('region', '待核验')} | {info2.get('region', '待核验')} |",
            f"| **办学层次** | {info1.get('level', '待核验')} | {info2.get('level', '待核验')} |",
            f"| **专业库来源** | `{info1.get('catalog_source', '')}` | `{info2.get('catalog_source', '')}` |",
            f"| **初试科目（结构化）** | {subject_display1} | {subject_display2} |",
            f"| **复试分数线走势** | {info1.get('score_trend', '待核验')} | {info2.get('score_trend', '待核验')} |",
            f"| **招生规模与报录** | {info1.get('ratio', '待核验')} | {info2.get('ratio', '待核验')} |",
            f"| **一志愿保护机制** | {info1.get('protect', '未核验')} | {info2.get('protect', '未核验')} |",
            f"| **研究生院官网** | [{name1}研招]({info1.get('graduate', '')}) | [{name2}研招]({info2.get('graduate', '')}) |",
            "",
            "## 📝 2. 专业方向与初试科目对比",
            f"### 【{name1}】({major})",
        ]
        for m in (info1.get("majors") or ["待核验"]):
            lines.append(f"- {m}")
        lines.append(f"\n### 【{name2}】({major})")
        for m in (info2.get("majors") or ["待核验"]):
            lines.append(f"- {m}")

        lines.extend([
            "",
            "## 💡 3. 私教深度研判与择校处方",
            f"- **科目与复习通用性**：{analysis['subject_diff']}",
            f"- **就业区位与发展空间**：{analysis['region_diff']}",
            f"- **选校综合权衡**：{analysis['recommendation']}",
            "",
            "## ⚠️ 4. 双方核心避坑红黑榜",
            f"- **{name1} 警示**：{info1.get('pitfalls', '')}",
            f"- **{name2} 警示**：{info2.get('pitfalls', '')}",
            "",
            "---",
            f"> 💡 **KaoYan Intelligence 对比提示**：可根据自身当前数学与专业课摸底分数，在终端中让私教为你量身推荐更稳妥的冲刺院校。"
        ])
        return "\n".join(lines)


# 全局单例
_default_comparator = None

def get_school_comparator() -> SchoolComparator:
    global _default_comparator
    if _default_comparator is None:
        _default_comparator = SchoolComparator()
    return _default_comparator
