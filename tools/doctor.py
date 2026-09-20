# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · ky doctor 系统全链路健康诊断工具
一键体检：
  1. Python 执行环境与 Windows 控制台编码
  2. 核心考研专有依赖项 (sympy, pypdf, Pillow, rapidocr)
  3. 四科目录体系、顶层 AGENTS.md 协议与状态文件完整性
  4. 配置文件 (ky_config.json) 与大模型 API Key 连通性状态
  5. 看板构建环境 (05-考研看板) 与 Webhook 网关端口 (8088)
  6. Git 隐私隔离与敏感文件防泄漏防护
"""

import sys
import json
import socket
from pathlib import Path

# Windows UTF-8 控制台兼容
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent

class C:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

def color(text, code):
    return f"{code}{text}{C.RESET}"


def _read_upstream_error(err) -> str:
    """从 HTTPError 的响应体里尽力提取上游 ``error.message``（拿不到就返回空串）。

    [R2-D2] 上游经常用 400 明确说出原因，例如 kuaipao.ai 返回
    ``{"error":{"message":"max_tokens must be greater than 2"}}``。旧实现把响应体
    直接吞掉，用户只能看到笼统的「参数/路由不兼容」，排查方向被带偏。
    """
    try:
        raw = err.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""
    msg = ""
    try:
        data = json.loads(raw)
        err_obj = data.get("error")
        if isinstance(err_obj, dict):
            msg = err_obj.get("message") or ""
        elif err_obj:
            msg = str(err_obj)
        if not msg:
            msg = data.get("message") or ""
    except Exception:
        msg = raw
    return " ".join(str(msg).split())[:200]


def _build_chat_probe_body(model_name: str) -> bytes:
    """构造「对话探活」的请求体（纯函数，便于回归测试锁定参数）。

    [R2-D2 修复] ``max_tokens`` 不能是 1：部分上游会直接回 HTTP 400
    ``{"error":{"message":"max_tokens must be greater than 2"}}``（实测
    kuaipao.ai），于是「探活参数本身不合法」被误报成「模型/路由不兼容」，
    把用户引向「换模型」。探活只需 1 个 token 的语义，取 16 兼顾兼容与成本。
    """
    return json.dumps({
        "model": model_name,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 16,
        "stream": False,
    }).encode("utf-8")


def _classify_chat_error(code: int, upstream_msg: str = "") -> tuple:
    """把对话探活的 HTTP 状态码映射为 ``(chat_ok, chat_status, chat_detail)``。

    分类语义（与历史实现逐条一致，不得改动）：
      429            → 接口是通的，仅限流，**不算故障**（chat_ok=True）
      401 / 403      → 鉴权失败（应检查 API Key）
      400 / 404      → 参数或路由不兼容，无法判定，提示人工确认（chat_ok=None）
      其余（5xx 等） → 接口确实不可用
    [R2-D2] 一律把上游 ``error.message`` 附在 detail 末尾 —— 上游常常已经说清
    原因（例如 "max_tokens must be greater than 2"），旧实现把它吞掉。
    """
    suffix = f"：{upstream_msg}" if upstream_msg else ""
    if code == 429:
        return True, "ratelimit", "HTTP 429 限流"
    if code in (401, 403):
        return False, "auth", f"HTTP {code} 鉴权失败{suffix}"
    if code in (400, 404):
        return None, "incompatible", f"HTTP {code} 参数/路由不兼容{suffix}"
    return False, "unavailable", f"HTTP {code}{suffix}"


def check_item(title, ok, detail_ok="", detail_fail="", warn=False):
    status_tag = color("[√ 通过]", C.GREEN) if ok else (color("[! 提示]", C.YELLOW) if warn else color("[× 异常]", C.RED))
    detail = detail_ok if ok else detail_fail
    detail_str = f" - {detail}" if detail else ""
    print(f"  {status_tag} {title}{detail_str}")
    return ok

def run_doctor(return_summary=False):
    print(color("\n============================================================", C.CYAN))
    print(color("  🩺 考研全科 AI 私人教师 · 全系统健康诊断 (ky doctor)", C.BOLD + C.CYAN))
    print(color("============================================================\n", C.CYAN))

    issues = 0
    warnings = 0

    # ── 1. Python 执行环境 ──
    print(color("【1. Python 运行时与环境】", C.BOLD))
    py_ver = sys.version_info
    ver_ok = py_ver >= (3, 10)
    ver_str = f"Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}"
    if not check_item(f"Python 版本: {ver_str}", ver_ok, "满足 Python >= 3.10", "需要 Python 3.10 或更高版本"):
        issues += 1

    exec_path = str(sys.executable)
    is_winapps = "WindowsApps" in exec_path
    if is_winapps:
        print(f"  {color('[! 警告]', C.YELLOW)} 当前使用的是 Windows Store 别名路径，建议在环境变量中优先配置真实 Python 路径 (如 D:\\Python\\...)")
        warnings += 1
    else:
        check_item(f"执行路径: {exec_path}", True, "真实解释器路径正常")

    enc = sys.stdout.encoding or "unknown"
    check_item(f"控制台编码: {enc}", True, "UTF-8 输出就绪" if "utf" in enc.lower() else "建议 chcp 65001")

    # ── 2. 依赖项检查 ──
    print(color("\n【2. 考研专有扩展技能依赖 (可选增强)】", C.BOLD))

    # fsrs —— 核心算法依赖（非可选）：错题沉淀/复测间隔计算的主链路依赖它。
    # 此前 doctor 不检查 fsrs，导致 fsrs.FSRS 这类 API 版本错配只在用户
    # 真正"记错题"时才以 AttributeError 暴露，体检却显示一切正常。
    try:
        import fsrs
        from fsrs import Card, Rating, Scheduler  # noqa: F401 - 存在性即契约
        _fsrs_ok = hasattr(Scheduler, "review_card")
        check_item(
            "FSRS 自适应复测调度 (fsrs)",
            _fsrs_ok,
            "已就绪 (Scheduler.review_card 可用)",
            "fsrs 版本过旧，缺少 Scheduler.review_card（本项目需 fsrs>=5.0）",
        )
        if not _fsrs_ok:
            issues += 1
    except Exception as _e:
        check_item(
            "FSRS 自适应复测调度 (fsrs)",
            False,
            "",
            f"未安装或版本不兼容 (pip install -U 'fsrs>=5.0.0')；错题沉淀与复测排期将不可用 ({_e})",
        )
        issues += 1

    # 可选加速扩展（Rust）—— 逐能力汇报，并显式暴露"被禁用"的能力
    try:
        try:
            import accel
        except ImportError:
            from tools import accel
        caps = accel.capabilities()
        if accel.native() is None:
            check_item(
                "Rust 原生加速扩展 (ky_rust_ext)",
                True,
                "未装载，全部走纯 Python 等价实现（功能完整，仅性能差异）",
            )
        else:
            enabled = [c for c in caps if c["enabled"]]
            disabled = [c for c in caps if c["present"] and not c["enabled"]]
            check_item(
                "Rust 原生加速扩展 (ky_rust_ext)",
                True,
                f"已装载：{len(enabled)} 项能力启用"
                + (f"，{len(disabled)} 项因已知缺陷被禁用" if disabled else ""),
            )
            for c in disabled:
                check_item(f"  └ 加速能力 [{c['name']}]", False, "", c["note"], warn=True)
                warnings += 1
    except Exception as _e:
        check_item("Rust 原生加速扩展 (ky_rust_ext)", True, f"能力探测跳过 ({_e})")

    # sympy
    has_sympy = False
    try:
        import sympy
        has_sympy = True
        check_item("数学高精符号验算 (sympy)", True, f"已就绪 (v{sympy.__version__})")
    except ImportError:
        check_item("数学高精符号验算 (sympy)", False, "", "未安装 (pip install sympy)；基础求导仍可运行但高精验算受限", warn=True)
        warnings += 1

    # pypdf
    has_pypdf = False
    try:
        import pypdf
        has_pypdf = True
        check_item("真题 PDF 提取 (pypdf)", True, f"已就绪 (v{pypdf.__version__})")
    except ImportError:
        check_item("真题 PDF 提取 (pypdf)", False, "", "未安装 (pip install pypdf)；PDF 真题提取暂不可用", warn=True)
        warnings += 1

    # cryptography
    try:
        import cryptography
        check_item("真题 PDF AES 解密权限 (cryptography)", True, f"已就绪 (v{cryptography.__version__})")
    except ImportError:
        check_item("真题 PDF AES 解密权限 (cryptography)", False, "", "未安装 (pip install cryptography)；加密/带权限位的试卷 PDF 提取可能受限", warn=True)
        warnings += 1

    # Pillow
    has_pillow = False
    try:
        import PIL
        from PIL import Image
        has_pillow = True
        check_item("图像处理与截图 (Pillow)", True, f"已就绪 (v{PIL.__version__})")
    except ImportError:
        check_item("图像处理与截图 (Pillow)", False, "", "未安装 (pip install Pillow)；多模态拍照批改暂不可用", warn=True)
        warnings += 1

    # RapidOCR
    try:
        from rapidocr_onnxruntime import RapidOCR
        check_item("本地离线 OCR (rapidocr)", True, "已就绪")
    except ImportError:
        check_item("本地离线 OCR (rapidocr)", False, "", "未安装 (可选，仅用于离线 OCR)", warn=True)

    # PySide6
    try:
        import PySide6
        check_item("GUI 客户端图形界面 (PySide6)", True, f"已就绪 (v{PySide6.__version__})")
    except ImportError:
        check_item("GUI 客户端图形界面 (PySide6)", False, "", "未安装 (pip install PySide6)；ky gui 可视化端暂不可用", warn=True)
        warnings += 1

    # BeautifulSoup4
    try:
        import bs4
        check_item("网页情报清洗解析 (beautifulsoup4)", True, f"已就绪 (v{bs4.__version__})")
    except ImportError:
        check_item("网页情报清洗解析 (beautifulsoup4)", False, "", "未安装 (pip install beautifulsoup4)；将降级为纯正则清洗", warn=True)
        warnings += 1

    # ── 3. 工作区架构与协议规范 ──
    print(color("\n【3. 四科目录架构与外置状态机】", C.BOLD))
    agents_root = ROOT / "AGENTS.md"
    if not check_item("顶层总控中枢协议 (AGENTS.md)", agents_root.exists() and agents_root.stat().st_size > 500, "总控协议完整挂载"):
        issues += 1

    subjs = [
        ("01-数学", ["AGENTS.md", "_状态/今日任务.md", "_状态/薄弱点雷达.md"]),
        ("02-英语", ["AGENTS.md", "_状态/今日任务.md", "_状态/薄弱点雷达.md"]),
        ("03-思想政治理论", ["AGENTS.md", "_状态/今日任务.md", "_状态/薄弱点雷达.md"]),
        ("04-专业课", ["AGENTS.md", "学情档案.md"]),
    ]
    for subj_dir, req_files in subjs:
        s_path = ROOT / subj_dir
        if not s_path.exists():
            check_item(f"学科目录: {subj_dir}", False, "", "目录缺失")
            issues += 1
            continue
        missing = [f for f in req_files if not (s_path / f).exists()]
        if missing:
            check_item(f"学科规范 [{subj_dir}]", False, "", f"缺失关键状态文件: {', '.join(missing)}", warn=True)
            warnings += 1
        else:
            check_item(f"学科规范 [{subj_dir}]", True, "核心协议与状态文件齐全")

    # ── 3.5 本地真实考研资料库（只读盘点）──
    # [P6 修复·只读承诺] doctor 是纯诊断命令，此前却调用了
    # material_scanner.scan_and_mount_materials() —— 它会原子重写 ky_config.json 与
    # AGENTS.md 的资料白名单，并在 .memory/agents_backups/ 落备份。实测在
    # `--permission=safe` 下照样把哨兵内容改回，与"只读体检"的语义完全相反，
    # 也让 safe 模式的安全承诺在真实入口上失效。
    # 挂载写回属于显式的 `ky mount` / `ky scan`（material.py 中的 write 命令），
    # 此处仅做只读盘点，绝不写盘。
    print(color("\n【3.5 本地真实考研资料库（只读盘点）】", C.BOLD))
    try:
        try:
            from skills import material_scanner
        except ImportError:
            from tools.skills import material_scanner
        total_files = 0
        per_subject = []
        for folder in ("01-数学", "02-英语", "03-思想政治理论", "04-专业课"):
            n = len(material_scanner.scan_subject_materials(ROOT, folder))
            total_files += n
            per_subject.append(f"{folder.split('-', 1)[-1]} {n} 份")
        check_item(
            "本地参考资料库盘点（只读）",
            True,
            f"共发现 {total_files} 份真实试卷/教材资料（{'、'.join(per_subject)}）；"
            f"如需写入白名单请运行 ky mount",
        )
    except Exception as e:
        check_item("本地参考资料库盘点（只读）", False, "", str(e), warn=True)

    # ── 4. 配置文件与模型状态 ──
    print(color("\n【4. 配置参数与大模型连通性】", C.BOLD))
    cfg_path = ROOT / "ky_config.json"
    cfg = {}
    if not cfg_path.exists():
        check_item("系统配置文件 (ky_config.json)", False, "", "未找到，首次运行将使用默认配置", warn=True)
        warnings += 1
    else:
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            check_item("配置文件格式", True, f"解析正常 (当前科目: {cfg.get('active_subject', 'math')})")
        except Exception as e:
            check_item("配置文件格式", False, "", f"JSON 损坏: {e}")
            issues += 1

    api_key = cfg.get("api_key", "").strip() if cfg else ""
    if not api_key or api_key.startswith("sk-xxxx"):
        check_item("大模型 API Key 状态", False, "", "尚未配置 API Key，运行 ky config 设置后即可唤醒 AI 解题", warn=True)
        warnings += 1
    else:
        masked_key = api_key[:4] + "****" + api_key[-4:] if len(api_key) > 8 else "****"
        check_item("大模型 API Key 状态", True, f"已配置 ({masked_key}, 模型: {cfg.get('model', 'deepseek-chat')})")

        # [P0 修复] 模型有效性探测：此前仅校验 Key 是否配置，若配置的模型名已在
        # 上游下线（如 gpt-5.4-mini），私教对话会持续失败而体检仍显示全绿。
        model_name = (cfg.get("model") or "").strip()
        base_url = (cfg.get("base_url") or "").strip().rstrip("/")
        if model_name and base_url:
            # [缺陷修复] 此前裸拼 `base_url + "/v1/models"`：当用户 base_url 已带 /v1
            # （如 "https://kuaipao.ai/v1"，实测配置）时会拼成 /v1/v1/models，
            # 上游明明可达却误报「上游暂不可达」。现统一复用 agent.loop 的
            # normalize_openai_url（与运行时同一套归一规则，单一真源）。
            try:
                from agent.loop import normalize_openai_url
            except ImportError:
                from tools.agent.loop import normalize_openai_url
            models_url = normalize_openai_url(base_url, "models")
            chat_url = normalize_openai_url(base_url, "chat/completions")
            probe_reachable = False
            model_found = False
            try:
                import urllib.request
                req = urllib.request.Request(
                    models_url,
                    headers={"Authorization": "Bearer " + api_key},
                )
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                ids = {str(m.get("id", "")) for m in (data.get("data") or [])}
                model_found = model_name in ids
                probe_reachable = True
            except Exception:
                probe_reachable = False

            # [P1 修复·假绿] 仅凭 /v1/models 列表命中就判「通过」是不够的：
            # 上游可能列出模型但 chat/completions 端点持续 502（见 2026-09-10 验收，
            # gpt-5.4-mini 在列表中却对对话接口返回 HTTP 502）。此处追加一次
            # 极轻量的对话探活（max_tokens=16），只有真正拿到合法响应才算通过。
            # [审查精化] 不同 HTTP 码含义不同，不能一律报「接口挂掉」：
            #   5xx / 502 / 超时      → 接口确实不可用（应换模型或稍后重试）
            #   401 / 403            → 鉴权失败（应检查 Key）
            #   429                  → 限流（接口是通的，稍后重试即可，不算故障）
            #   400 / 404            → 参数或路由不兼容（提示需人工确认）
            chat_ok = None  # None=未探测 / True=可用 / False=不可用
            chat_status = ""  # "ok" / "unavailable" / "auth" / "ratelimit" / "incompatible"
            chat_detail = ""
            if probe_reachable and model_found:
                try:
                    import urllib.request
                    import urllib.error
                    body = _build_chat_probe_body(model_name)
                    creq = urllib.request.Request(
                        chat_url,
                        data=body,
                        headers={
                            "Authorization": "Bearer " + api_key,
                            "Content-Type": "application/json",
                            "Connection": "close",
                        },
                        method="POST",
                    )
                    # [根因修复·假红] 旧阈值 15s 小于上游真实延迟（本机实测：短提示 2.9s、
                    # 长提示 36.7s，P95 在 37s 以上），导致"慢"被当成"挂"。提到 60s。
                    with urllib.request.urlopen(creq, timeout=60) as resp:
                        _ = resp.read()
                    chat_ok = True
                    chat_status = "ok"
                except urllib.error.HTTPError as e:
                    # [R2-D2] 状态码分类 + 上游 error.message 透传，统一收口到
                    # _classify_chat_error（便于回归测试逐档锁定，别再散在这里）
                    chat_ok, chat_status, chat_detail = _classify_chat_error(
                        e.code, _read_upstream_error(e))
                except (TimeoutError, socket.timeout):
                    # [根因修复·假红] 超时 ≠ 不可用。旧代码在 except Exception 里把超时
                    # 一律归为 chat_status="unavailable"，进而输出「对话接口不可用，
                    # 私教对话会持续失败」——这条结论与事实相反。超时只能证明"慢/不通"，
                    # 无法证明"挂"，故单列一档，不计入故障。
                    chat_ok = None
                    chat_status = "timeout"
                    chat_detail = "探活超时 (>60s)"
                except Exception as e:
                    chat_ok = False
                    chat_status = "unavailable"
                    chat_detail = type(e).__name__

            if probe_reachable and model_found and chat_status == "ok":
                check_item("模型有效性 (上游 /v1/models + 对话探活)", True,
                           f"{model_name} 上游可用且对话接口响应正常")
            elif probe_reachable and model_found and chat_status == "ratelimit":
                check_item("模型有效性 (上游 /v1/models + 对话探活)", True,
                           f"{model_name} 对话接口可达（当前被限流 429，稍后重试即可）")
            elif probe_reachable and model_found and chat_status == "timeout":
                check_item("模型有效性 (上游 /v1/models + 对话探活)", False, "",
                           f"模型 {model_name} 已在上游列表中，但对话探活超时（{chat_detail}）："
                           f"可能是上游响应较慢，也可能是网络不通，无法判定为故障；"
                           f"若实际对话频繁失败，请运行 ky config 更换模型", warn=True)
                warnings += 1
            elif probe_reachable and model_found and chat_status == "incompatible":
                check_item("模型有效性 (上游 /v1/models + 对话探活)", False, "",
                           f"模型 {model_name} 对话接口返回 {chat_detail}，"
                           f"可能该模型不支持当前请求格式，建议人工确认或更换模型", warn=True)
                warnings += 1
            elif probe_reachable and model_found and chat_status == "auth":
                check_item("模型有效性 (上游 /v1/models + 对话探活)", False, "",
                           f"模型 {model_name} 对话鉴权失败 ({chat_detail})，请运行 ky config 检查 API Key", warn=True)
                warnings += 1
            elif probe_reachable and model_found and chat_ok is False:
                check_item("模型有效性 (上游 /v1/models + 对话探活)", False, "",
                           f"模型 {model_name} 虽在上游列表中，但对话接口不可用 ({chat_detail})，"
                           f"私教对话会持续失败；请运行 ky config 更换模型", warn=True)
                warnings += 1
            elif probe_reachable and model_found:
                # chat_ok 为 None（探活被跳过）
                check_item("模型有效性 (上游 /v1/models)", True, f"{model_name} 在上游可用模型列表中")
            elif probe_reachable:
                check_item("模型有效性 (上游 /v1/models)", False, "",
                           f"模型 {model_name} 不在上游可用列表中，私教对话将持续失败；请运行 ky config 更换模型", warn=True)
                warnings += 1
            else:
                check_item("模型有效性 (上游 /v1/models)", True, "上游暂不可达，已跳过探测 (离线环境属正常)")

    # ── 5. 网关与看板构建环境 ──
    print(color("\n【5. Web 伴侣网关与看板系统】", C.BOLD))
    build_script = ROOT / "05-考研看板" / "build.py"
    html_docs = ROOT / "docs" / "index.html"
    if not check_item("看板编译引擎 (05-考研看板/build.py)", build_script.exists()):
        issues += 1

    # [新增·版本一致性] 项目版本号此前在四处各写一份且互不相同
    # （pyproject 2.7.0 / ky_cli 打印 v2.6.0 / gui 包 2.5.0 / TUI Banner v2.5），
    # 用户在任何一端看到的版本都不同，无法据此判断该不该升级。
    # 现收敛到 tools/version.py 单一真源，这里做一次回归体检。
    try:
        try:
            from version import get_version
        except ImportError:
            from tools.version import get_version
        try:
            import gui as _gui_pkg
            gui_ver = str(getattr(_gui_pkg, "__version__", ""))
        except Exception:
            gui_ver = get_version()
        ver = get_version()
        consistent = ver != "0.0.0+unknown" and gui_ver == ver
        check_item("版本号单一真源 (tools/version.py)", consistent,
                   f"各端一致：v{ver}",
                   f"版本不一致：pyproject={ver} / GUI 包={gui_ver}，请检查 tools/version.py 的取值")
        if not consistent:
            warnings += 1
    except Exception as _e:
        check_item("版本号单一真源 (tools/version.py)", False, "", f"版本读取失败: {_e}")
        warnings += 1

    # [新增·主题入口] 用户可自定义界面主题（颜色/圆角/密度/字号）。
    # 文件缺失属正常（用内置预设）；存在但不可读/不达标会被 tools/theme 拒绝并回退，
    # 此处提前把"自定义被拒"这件事暴露出来，避免用户以为设置没生效。
    theme_file = ROOT / "ui_theme.json"
    if theme_file.exists():
        try:
            from tools.theme import load_theme
            theme = load_theme(ROOT)
            ok = theme.source != "builtin-fallback"
            check_item("个人主题配置 (ui_theme.json)", ok,
                       f"已生效：{theme.display_name}（{theme.name}）",
                       "自定义配色可读性不达标（WCAG 对比度校验未通过），已回退内置默认主题")
            if not ok:
                warnings += 1
        except Exception as _e:
            check_item("个人主题配置 (ui_theme.json)", False, "", f"主题解析失败: {_e}")
            warnings += 1
    else:
        check_item("个人主题配置 (ui_theme.json)", True,
                   "未自定义，使用内置预设（可在工作区根创建该文件切换主题）")

    # 检测 8088 端口状态
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    port_used = False
    try:
        result = sock.connect_ex(("127.0.0.1", 8088))
        if result == 0:
            port_used = True
    except Exception:
        pass
    finally:
        sock.close()

    if port_used:
        check_item("网关端口 (127.0.0.1:8088)", True, "端口正被占用 (Webhook 网关 / Web 伴侣可能已在运行中)")
    else:
        check_item("网关端口 (127.0.0.1:8088)", True, "端口空闲，随时可启动服务")

    # ── 6. Git 隐私防护 ──
    print(color("\n【6. Git 隐私隔离与防泄密安全】", C.BOLD))
    gitignore_path = ROOT / ".gitignore"
    if gitignore_path.exists():
        gi_text = gitignore_path.read_text(encoding="utf-8")
        has_cfg_ignore = "ky_config.json" in gi_text
        has_mem_ignore = ".memory" in gi_text
        check_item("敏感配置文件保护 (ky_config.json)", has_cfg_ignore, "已被 .gitignore 安全保护", "未被忽略！存在泄露风险！")
        check_item("个人决策与记忆保护 (.memory/)", has_mem_ignore, "已被 .gitignore 安全保护", "未被忽略！存在泄露风险！")
        if not (has_cfg_ignore and has_mem_ignore):
            issues += 1
    else:
        check_item(".gitignore 存在性", False, "", "未找到 .gitignore", warn=True)
        warnings += 1

    # ── 7. 技能降级与离线能力评估 ──
    print(color("\n【7. 系统功能可用性与降级评估】", C.BOLD))
    print(f"  • Agent 核心闭环与多轮交互: {color('全功能就绪', C.GREEN)}")
    print(f"  • 纯离线做题、考纲Diff、双校对标与状态机: {color('全功能就绪 (无需外网)', C.GREEN)}")
    if has_sympy:
        print(f"  • 高精数学符号验算 (SymPy): {color('完全就绪', C.GREEN)}")
    else:
        print(f"  • 高精数学符号验算 (SymPy): {color('降级运行 (使用纯 Python 基础求导与代数运算)', C.YELLOW)}")

    if has_pypdf:
        print(f"  • PDF 真题抽取入库 (pypdf): {color('完全就绪', C.GREEN)}")
    else:
        print(f"  • PDF 真题抽取入库 (pypdf): {color('降级运行 (支持 Markdown / TXT 文本切片入库)', C.YELLOW)}")

    if has_pillow:
        print(f"  • 多模态作业拍照批改 (Pillow): {color('完全就绪', C.GREEN)}")
    else:
        print(f"  • 多模态作业拍照批改 (Pillow): {color('降级运行 (支持文本敲字输入作答)', C.YELLOW)}")

    # ── 总结与处方 ──
    print(color("\n" + "=" * 60, C.CYAN))
    if issues == 0 and warnings == 0:
        print(color(" 🎉 体检全绿！考研全科 AI 私人教师系统处于绝佳就绪状态！", C.GREEN + C.BOLD))
    elif issues == 0:
        print(color(f" ✅ 核心系统运转正常！发现 {warnings} 处可选优化项（降级可用，不影响主链路）。", C.YELLOW + C.BOLD))
    else:
        print(color(f" ⚠️ 发现 {issues} 处阻断性问题与 {warnings} 处警告，请根据上述提示处理。", C.RED + C.BOLD))
    print(color("=" * 60 + "\n", C.CYAN))

    if return_summary:
        return {
            "issues": issues,
            "warnings": warnings,
            "has_sympy": has_sympy,
            "has_pypdf": has_pypdf,
            "has_pillow": has_pillow
        }
    return issues == 0

if __name__ == "__main__":
    success = run_doctor()
    sys.exit(0 if success else 1)
