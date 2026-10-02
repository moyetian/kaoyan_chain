# -*- coding: utf-8 -*-
"""
Agent Loop 异步执行 Worker，防止 GUI 主线程阻塞
"""

import sys
from pathlib import Path

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from typing import Optional
from PySide6.QtCore import QThread, Signal

ROOT = resolve_workspace_root(__file__)
TOOLS = ROOT / "tools"
for p in (str(ROOT), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)


class AgentWorker(QThread):
    finished_signal = Signal(str)
    #: 流式片段（每小段一次，避免逐字符刷爆 GUI 主线程）
    chunk_signal = Signal(str)
    #: 中间思考过程与工具调用事件（推送到 GUI 聊天框，无需查看外部终端黑框）
    step_signal = Signal(str)
    #: [K7-U1] 本 worker 真实执行过 Agent 会话（AgentRunner 构造成功、run 启动）。
    #: 供 MainWindow 关窗时触发一次 SessionEnd 钩子（日终复盘）；纯本地口令
    #: （「查漏」等，走 _try_local_command 提前返回）不触发。
    session_ran_signal = Signal()

    #: [B3b] 进程内共享的 GUI 会话 id：GUI 每条消息都新建一个 AgentWorker，
    #: 若各自新建会话则「每问一句产生一个新 .jsonl」。改由类级变量在首次
    #: 发消息时生成一次，此后所有 worker 复用同一个会话文件。
    _shared_session_id: Optional[str] = None

    def _resolve_session_id(self) -> Optional[str]:
        """取/建进程内共享的 GUI 会话 id；任何失败返回 None（回落既有行为）。

        只生成 id（``SessionLog`` 懒创建，不碰磁盘）；真正的日志文件在首次
        有效 run 时由 AgentRunner 写入。
        """
        if AgentWorker._shared_session_id is None:
            try:
                try:
                    from agent.session_log import SessionLog
                except ImportError:
                    from tools.agent.session_log import SessionLog
                AgentWorker._shared_session_id = SessionLog(
                    workspace_root=self.workspace_root).session_id
            except Exception:
                AgentWorker._shared_session_id = ""
        return AgentWorker._shared_session_id or None

    def __init__(self, config: dict, user_input: str, timeout: float = None,
                 workspace_root=None):
        super().__init__()
        self.config = config or {}
        # [缺陷修复·工作区双根] 显式接收调用方工作区（MainWindow.workspace_root）；
        # 未传时才回落模块级 ROOT。杜绝 exe 下"GUI 写 A、agent 读 B"的分裂。
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else ROOT
        self.user_input = user_input
        self._session_id = self._resolve_session_id()
        if timeout is None:
            try:
                self.timeout = float(self.config.get("request_timeout") or 120.0)
            except (TypeError, ValueError):
                self.timeout = 120.0
        else:
            self.timeout = float(timeout)
        self._is_cancelled = False
        # [A3b 修复·GUI 审批通道] 桌面端里有人在场：Level 4（网络）/ Level 5
        # （破坏性）此前走 headless 默认策略被静默拒绝，用户只看到
        # 「PermissionDenied: 操作被拦截」。这里在**主线程**构造 GUI 弹窗通道
        # （Qt 对象必须归属主线程），run() 里注入给 AgentRunner。
        # 构造失败（无 PySide6 / 无 QApplication / 任何异常）一律降级为 None，
        # 回落到既有默认通道，绝不因此让任务崩溃。
        self._approval_channel = self._build_approval_channel()

    def _build_approval_channel(self):
        """构造 GUI 审批通道；任何异常返回 None（回落默认通道）。"""
        try:
            try:
                from gui.approval_bridge import GuiApproval, resolve_approval_timeout
            except ImportError:
                from tools.gui.approval_bridge import GuiApproval, resolve_approval_timeout
            return GuiApproval(timeout=resolve_approval_timeout(self.config))
        except Exception:
            return None

    def cancel(self):
        """中止任务"""
        self._is_cancelled = True

    def _emit_step(self, text: str) -> None:
        """把私教动作/思考事件实时发送到 GUI 界面。"""
        if self._is_cancelled or not text:
            return
        try:
            self.step_signal.emit(str(text))
        except RuntimeError:
            return

    # 纯本地口令 → 本地处理器映射（不依赖 LLM，上游异常时仍可用）
    # 值可以是 ("fn", 可调用名) 或 ("action", tui别名)
    _LOCAL_COMMAND_ALIASES = {
        "查漏": ("fn", "weakness"),
        "查漏补缺": ("fn", "weakness"),
        "薄弱点": ("fn", "weakness"),
        "扫描薄弱点": ("fn", "weakness"),
        "更新看板": ("action", "build"),
        "重编译看板": ("action", "build"),
        "刷新看板": ("action", "build"),
        "看板更新": ("action", "build"),
        # [P1 修复·三端一致] 仅在单纯输入「交作业」时展示提交指引；带有答案文本时交 LLM 判卷
        "交作业": ("fn", "homework"),
        "对答案": ("fn", "homework"),
    }

    def _safe_timeout_text(self) -> str:
        """把 self.timeout 安全格式化为显示用的整秒文本（None/非法值回落 60）。"""
        try:
            return str(int(float(self.timeout)))
        except (TypeError, ValueError):
            return "60"

    def _try_local_command(self, text: str):
        """尝试把口令交给本地执行器；命中返回结果文本，未命中返回 None。"""
        spec = self._LOCAL_COMMAND_ALIASES.get(text)
        if not spec:
            return None
        kind, target = spec
        import contextlib, io
        try:
            if kind == "fn" and target == "weakness":
                from ky_cli import build_weakness_scan_report
                return build_weakness_scan_report()
            if kind == "fn" and target == "homework":
                from ky_cli import build_homework_menu
                return build_homework_menu()
            if kind == "action":
                from tui_navigator import execute_action
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    execute_action(target, interactive=False)
                out = buf.getvalue().strip()
                return out or f"[本地执行完毕] {text}"
        except Exception as e:
            return f"[本地执行异常] {text}: {e}"
        return None

    def _emit_chunk(self, text: str) -> None:
        """把流式片段转发到 GUI 主线程（QThread 内直接 emit 信号是线程安全的）。"""
        if self._is_cancelled or not text:
            return
        try:
            self.chunk_signal.emit(str(text))
        except RuntimeError:
            return

    def run(self):
        if self._is_cancelled:
            self.finished_signal.emit("[已取消]: 用户主动取消了任务。")
            return

        text = (self.user_input or "").strip()
        _checkin_map = {
            "数学报到": "math", "学数学": "math", "切换数学": "math",
            "英语报到": "eng", "学英语": "eng", "切换英语": "eng",
            "政治报到": "pol", "学政治": "pol", "切换政治": "pol",
            "专业课报到": "pro", "学专业课": "pro", "切换专业课": "pro",
        }
        _subj_names = {
            "math": "数学", "eng": "英语", "pol": "思想政治理论", "pro": "专业课"
        }
        if text in _checkin_map:
            subj = _checkin_map[text]
            subj_name = _subj_names.get(subj, "考研科目")
            cfg = dict(self.config) if self.config else {}
            try:
                from ky_cli import build_subject_checkin_brief, load_config, save_config
                # [P26 修复·局部字典覆盖真实配置] 此前直接 save_config(cfg)，
                # 而 cfg 可能只是调用方传入的局部字典（例如
                # {"api_key","model"}），会把磁盘上的 study_plan / webhooks /
                # base_url / 各科目标分等全部键**整份覆盖**掉 —— 学员辛苦填的
                # 备考方案就此丢失，且现场只留 3 个键、毫无报错。
                # 现改为：以磁盘配置为基底合并，只更新本函数真正负责的
                # active_subject，其余键原样保留。
                merged = dict(load_config() or {})
                merged.update(cfg)
                cfg = merged
            except Exception:
                merged = cfg

            # [缺陷修复·GUI 缺数学闸门] 备考方案为「不考数学」时，旧实现把
            # "数学报到" 直接改写成导学 prompt 发给 LLM —— LLM 复述磁盘上的
            # "不考数学"文件，输出自相矛盾（用户实测）。现与 CLI 同源判定
            # （is_math_disabled），不考数学时本地直接拒绝并给出修改指引。
            if subj == "math":
                _math_off = False
                try:
                    try:
                        from cli.shared import is_math_disabled
                    except ImportError:  # pragma: no cover
                        from tools.cli.shared import is_math_disabled
                    _math_off = is_math_disabled(cfg)
                except Exception:
                    _math_off = False
                if _math_off:
                    self.finished_signal.emit(
                        "\n[!] 当前备考方案为「不考数学」，无需向数学私教报到，"
                        "今日也不安排数学任务。\n"
                        "    若专业实际要求考数学，请点击顶部「设置」→「考研信息配置」"
                        "更新考试科目，或在终端运行 `ky subject` 重新选择。\n"
                    )
                    return

            try:
                merged["active_subject"] = subj
                save_config(merged)
                cfg = merged
                self.config["active_subject"] = subj
            except Exception:
                pass

            # [缺陷修复·报到后今日任务 0/0] 报到时确保该科「今日任务」文件
            # 已生成（缺失/过期才写，当日已有则保留），任务面板随即可见。
            task_info = {}
            try:
                try:
                    from study_planner import ensure_subject_today_task
                except ImportError:  # pragma: no cover
                    from tools.study_planner import ensure_subject_today_task
                task_info = ensure_subject_today_task(
                    cfg, subj, workspace_root=self.workspace_root)
            except Exception:
                task_info = {}

            api_key = (cfg.get("api_key") or "").strip()
            if not api_key:
                # 未配置 API 时给出本地大纲播报与明确的配置向导提示
                try:
                    from ky_cli import build_subject_checkin_brief
                    brief = build_subject_checkin_brief(cfg, subj)
                except Exception:
                    brief = f"已切换至【{subj_name}】科目。"
                task_line = ""
                if task_info.get("status") in ("created", "overwritten", "exists", "refreshed"):
                    task_line = (f"\n📋 今日{_subj_names.get(subj, '')}任务清单已就绪"
                                 f"（共 {task_info.get('task_count', 0)} 项），"
                                 f"可在「今日任务」页查看并逐项打卡。")
                guide = (
                    f"{brief}{task_line}\n\n"
                    f"──────────────────────────────────────────────────\n"
                    f"💡 [智能私教未激活]: 当前未检测到大模型 API 密钥。\n"
                    f"点击窗口顶部的「设置」按钮配置 DeepSeek / Qwen 等 API Key，"
                    f"即可开启专属 AI 私教的大模型深度辅导、真题拆解与答疑互动！"
                )
                self.finished_signal.emit(guide)
                return

            # 已配置 API 时，构造导学 prompt，由 AgentRunner 联动大模型开始实时生成
            task_hint = ""
            if task_info.get("status") in ("created", "overwritten", "exists", "refreshed"):
                task_hint = (
                    f"\n系统已为该科目生成今日任务清单（{task_info.get('path')}，"
                    f"共 {task_info.get('task_count', 0)} 项任务，已写入 _状态/今日任务.md），"
                    f"请先读取该任务文件，按清单向学员汇报今日攻坚路线图。"
                )
            self.user_input = (
                f"学员已向【{subj_name}】专属私教报到。请检查该科目的考试大纲与今日任务，"
                f"结合学员在《AGENTS.md》中记录的该科目核心薄弱点，给出今日辅导开场白、核心考点点拨，"
                f"并派发出今日的第 1 道精选自测题（请给出题目并引导学员作答，勿直接贴出答案）。"
                f"{task_hint}"
            )

        # 视觉答卷批改口令 (/img 或 /ocr)
        if text.startswith(("/img", "/ocr")):
            cmd_body = text[4:].strip()
            img_path = ""
            extra_prompt = ""
            if cmd_body:
                if cmd_body.startswith(('"', "'")):
                    q = cmd_body[0]
                    end_idx = cmd_body.find(q, 1)
                    if end_idx != -1:
                        img_path = cmd_body[1:end_idx].strip()
                        extra_prompt = cmd_body[end_idx + 1:].strip()
                    else:
                        parts = cmd_body.split(maxsplit=1)
                        img_path = parts[0].strip('"').strip("'")
                        extra_prompt = parts[1] if len(parts) > 1 else ""
                else:
                    parts = cmd_body.split(maxsplit=1)
                    img_path = parts[0].strip('"').strip("'")
                    extra_prompt = parts[1] if len(parts) > 1 else ""

            p_img = Path(img_path) if img_path else None
            if not p_img or not p_img.exists():
                self.finished_signal.emit(f"[!] 图片文件不存在或路径无效: {img_path}\n请点击「📷 图片」按钮重新选择。")
                return

            self._emit_step(f"📸 正在调起多模态视觉私教引擎分析图片: {p_img.name} ...")
            try:
                try:
                    from tools.skills import vision_solver
                except ImportError:
                    from skills import vision_solver
                reply = vision_solver.solve_image_with_model(
                    str(p_img), extra_prompt, self.config, stream=False
                )
                self.finished_signal.emit(reply or "[视觉批改完成，无更多文字输出]")
                return
            except Exception as e:
                self.finished_signal.emit(f"[×] 视觉解答批改异常: {e}")
                return

        # 考研文件/真题讲义挂载口令 (/file)
        if text.startswith("/file"):
            cmd_body = text[5:].strip()
            file_path = ""
            extra_prompt = ""
            if cmd_body:
                if cmd_body.startswith(('"', "'")):
                    q = cmd_body[0]
                    end_idx = cmd_body.find(q, 1)
                    if end_idx != -1:
                        file_path = cmd_body[1:end_idx].strip()
                        extra_prompt = cmd_body[end_idx + 1:].strip()
                    else:
                        parts = cmd_body.split(maxsplit=1)
                        file_path = parts[0].strip('"').strip("'")
                        extra_prompt = parts[1] if len(parts) > 1 else ""
                else:
                    parts = cmd_body.split(maxsplit=1)
                    file_path = parts[0].strip('"').strip("'")
                    extra_prompt = parts[1] if len(parts) > 1 else ""

            p_file = Path(file_path) if file_path else None
            if not p_file or not p_file.exists():
                self.finished_signal.emit(f"[!] 资料文件不存在或路径无效: {file_path}\n请点击「📎 文件」按钮重新选择。")
                return

            self._emit_step(f"📄 正在读取考研资料: {p_file.name} ...")
            content_preview = ""
            try:
                if p_file.suffix.lower() in (".md", ".txt", ".json"):
                    content_preview = p_file.read_text(encoding="utf-8", errors="ignore")[:3500]
                elif p_file.suffix.lower() == ".docx":
                    try:
                        import zipfile
                        import xml.etree.ElementTree as ET
                        with zipfile.ZipFile(p_file) as z:
                            xml_data = z.read("word/document.xml")
                        tree = ET.fromstring(xml_data)
                        texts = [elem.text for elem in tree.iter() if elem.text and elem.tag.endswith("}t")]
                        content_preview = "".join(texts)[:3500]
                    except Exception:
                        content_preview = f"[Word 文档: {p_file.name}, 路径: {file_path}]"
                elif p_file.suffix.lower() == ".pdf":
                    try:
                        try:
                            from tools.skills.material_ingestion import extract_text_from_pdf
                        except ImportError:
                            from skills.material_ingestion import extract_text_from_pdf
                        content_preview = extract_text_from_pdf(p_file)[:3500]
                    except Exception:
                        try:
                            try:
                                from tools.skills.pdf_extractor import extract_pdf_pages
                            except ImportError:
                                from skills.pdf_extractor import extract_pdf_pages
                            res = extract_pdf_pages(p_file, max_pages=6)
                            if res.get("success"):
                                content_preview = "\n".join(p.get("text", "") for p in res.get("pages", []) if p.get("text"))[:3500]
                            else:
                                content_preview = f"[PDF 文件: {p_file.name}, 路径: {file_path}]"
                        except Exception:
                            content_preview = f"[PDF 文件: {p_file.name}, 路径: {file_path}]"
                else:
                    content_preview = f"[考研文档: {p_file.name}, 路径: {file_path}]"
            except Exception as e:
                content_preview = f"[文件读取异常: {e}]"

            self.user_input = (
                f"学员挂载了一份考研文件【{p_file.name}】（路径: `{file_path}`）。\n"
                f"学员需求: {extra_prompt or '请精读该资料，结合考研官方考纲指出重点考点与复习建议。'}\n\n"
                f"【文件内容节选】:\n```\n{content_preview}\n```"
            )

        # 尝试纯本地口令（如「查漏」「更新看板」）
        local_reply = self._try_local_command(text)
        if local_reply is not None:
            self.finished_signal.emit(local_reply)
            return

        runner = None
        try:
            try:
                from agent.loop import AgentRunner
            except ImportError:
                from tools.agent.loop import AgentRunner
            runner = AgentRunner(
                config=self.config,
                workspace_root=self.workspace_root,
                # [A3a/G13 修复] 此前传 "acceptEdits"：PermissionManager 不认识
                # 这个 Claude Code 系命名，静默回退成 `ask`，再叠加 GUI 的
                # `interactive=False` → Level 1+ 写操作**全部被拒**（桌面端
                # 完全写不了文件）。GUI 默认与 CLI 一致使用最小权限 `ask`；
                # 用户可在设置中显式切换更宽松模式，Level 1+ 默认进入审批通道。
                permission_mode="ask",
                # [A3b 修复] Level 4-5 走 GUI 弹窗（批准 / 本会话信任 / 拒绝），
                # 而不是 headless 默认的静默拒绝。
                approval_channel=self._approval_channel,
                max_steps=8,
                request_timeout=self.timeout,
                stream_callback=self._emit_chunk,
                step_callback=self._emit_step,
                quiet=True,
                # [B3b] 同一 GUI 会话的所有消息复用同一个 .jsonl（跨消息保持
                # 上下文可追溯），而不是每问一句新建一个会话文件。
                session_id=self._session_id,
            )
            # [K7-U1] 真实 Agent 会话已开始：通知主窗口（关窗时据此触发一次
            # SessionEnd）。放在 run 之前 —— 即便 run 中途被关窗取消，会话
            # 也算发生过。emit 失败（窗口已销毁等）静默降级，绝不阻断对话。
            try:
                self.session_ran_signal.emit()
            except RuntimeError:
                pass
            reply = runner.run(self.user_input, interactive=False)
            if self._is_cancelled:
                self.finished_signal.emit("[已取消]: 任务已中止。")
            elif not reply or not str(reply).strip():
                self.finished_signal.emit(
                    "\n[Agent 未返回有效回复] 私教服务可能暂时不可用（如上游 LLM 返回 503 / 限流 / 对话接口 502），"
                    "请稍后重试。如果多次失败，可在 CLI 执行 `ky doctor` 检查 API 连通性"
                    "（doctor 现会对对话接口做真实探活，而不仅看模型列表）。"
                )
            else:
                self.finished_signal.emit(reply)
        except Exception as e:
            # 超时会有明确提示，而不是静默转圈
            self.finished_signal.emit(
                f"[Agent 执行异常]: {e}\n"
                f"（若为超时，说明上游在 {self._safe_timeout_text()} 秒内无响应，"
                f"请稍后重试或检查网络/代理）"
            )
        finally:
            # [B3b] 收尾：写 session_end 事件并关闭日志句柄（close 幂等、内部
            # 不抛）。同一 GUI 会话跨多条消息会产生多条 session_end —— 可接受
            # （每条消息一个 runner 的生命周期），真正的会话上下文由共享
            # session_id 的同一份 JSONL 保留。
            if runner is not None:
                runner.close()
