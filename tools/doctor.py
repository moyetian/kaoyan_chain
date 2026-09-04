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
import os
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

    # ── 5. 网关与看板构建环境 ──
    print(color("\n【5. Web 伴侣网关与看板系统】", C.BOLD))
    build_script = ROOT / "05-考研看板" / "build.py"
    html_docs = ROOT / "docs" / "index.html"
    if not check_item("看板编译引擎 (05-考研看板/build.py)", build_script.exists()):
        issues += 1
    if not check_item("自测看板主页 (docs/index.html)", html_docs.exists() and html_docs.stat().st_size > 1000):
        warnings += 1

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

    # ── 总结与处方 ──
    print(color("\n" + "=" * 60, C.CYAN))
    if issues == 0 and warnings == 0:
        print(color(" 🎉 体检全绿！考研全科 AI 私人教师系统处于绝佳就绪状态！", C.GREEN + C.BOLD))
    elif issues == 0:
        print(color(f" ✅ 核心系统运转正常！发现 {warnings} 处可选优化项（不影响基础使用）。", C.YELLOW + C.BOLD))
    else:
        print(color(f" ⚠️ 发现 {issues} 处阻断性问题与 {warnings} 处警告，请根据上述提示处理。", C.RED + C.BOLD))
    print(color("=" * 60 + "\n", C.CYAN))

    if return_summary:
        return {"issues": issues, "warnings": warnings}
    return issues == 0

if __name__ == "__main__":
    success = run_doctor()
    sys.exit(0 if success else 1)
