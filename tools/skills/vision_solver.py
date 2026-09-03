# -*- coding: utf-8 -*-
"""
考研专属视觉解题与手写批改技能 (Vision & OCR Solver Skill)
功能：
  1. 支持图片输入 (拖拽图片路径、指定路径、Base64、系统剪贴板)
  2. 数学公式 LaTeX 识别与高精度题面提取 (RapidOCR / EasyOCR / Tesseract)
  3. 手写推导草稿逐行批改、采分点赋分、错因五分类判定
  4. 支持多模态大模型 (Qwen-VL, GLM-4V, GPT-4o, Claude) 与本地 OCR 自动降级与双引擎融合
"""

import os
import sys
import base64
import json
import mimetypes
import urllib.request
import urllib.error
from pathlib import Path

# 确保父目录与 tools 在 sys.path 中
tools_dir = Path(__file__).resolve().parent.parent
if str(tools_dir) not in sys.path:
    sys.path.insert(0, str(tools_dir))
if str(tools_dir.parent) not in sys.path:
    sys.path.insert(0, str(tools_dir.parent))

try:
    import ky_cli
except ImportError:
    try:
        from tools import ky_cli
    except ImportError:
        ky_cli = None

def encode_image_to_base64(image_path):
    """读取本地图片并转为 Base64 与 Data URL"""
    p = Path(image_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"未找到图片文件: {image_path}")

    mime, _ = mimetypes.guess_type(str(p))
    if not mime or not mime.startswith("image/"):
        suffix = p.suffix.lower()
        if suffix in (".jpg", ".jpeg"): mime = "image/jpeg"
        elif suffix == ".png": mime = "image/png"
        elif suffix == ".webp": mime = "image/webp"
        elif suffix == ".bmp": mime = "image/bmp"
        else: mime = "image/jpeg"

    with open(p, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime};base64,{encoded}", mime, p.name

def build_vision_prompt(user_instruction="", subject="math", pre_extracted_text=""):
    """构建专业的考研阅卷与手写批改 Prompt"""
    default_prompt = (
        "你是一位深谙中国研究生入学考试命题规范与阅卷标准的专属总教练。\n"
        "请仔细审视图片中的题目与学员手写解答草稿，按以下严格规范进行识别与批改：\n\n"
        "### 1. 【题面提取与公式还原】\n"
        "- 完整准确识别图片中的题干内容，所有数学公式、符号必须转换为规范标准的 LaTeX 语法（用 $ 或 $$ 包裹）。\n\n"
        "### 2. 【分步判定与采分点拆解】\n"
        "- 逐行审视学员的手写推导步骤；\n"
        "- 明确标出每一个关键推导步骤的得分情况（如 `[+2分: 辅助函数构造正确]`，或 `[-2分: 积分限代入计算失误]`）；\n"
        "- 指出跳步、逻辑不严密或书写不规范的地方。\n\n"
        "### 3. 【错因五分类精准定性】\n"
        "- 若有丢分，必须明确归类为以下五种之一：【概念漏洞】/【审题偏差】/【公式记错】/【计算失误】/【书写丢分】。\n\n"
        "### 4. 【标准答案与解题处方】\n"
        "- 给出该题规范、简洁的标准正解推导示范，并提出针对性的复习改进处方。"
    )

    extra_parts = []
    if pre_extracted_text:
        extra_parts.append(f"【系统本地 OCR 预识别题干信息供参考】：\n```text\n{pre_extracted_text}\n```")
    if user_instruction:
        extra_parts.append(f"【学员补充说明或具体疑问】：\n{user_instruction}")

    if extra_parts:
        return f"{default_prompt}\n\n" + "\n\n".join(extra_parts)
    return default_prompt

def extract_text_with_local_ocr(image_path):
    """
    尝试使用本地极速 OCR 引擎 (RapidOCR / EasyOCR / Tesseract) 提取图片中的题干与公式
    纯本地离线运行，免外部 API，数学公式与中英文识别率高
    """
    p = Path(image_path).expanduser().resolve()
    if not p.exists():
        return None

    # 1. 优先使用 RapidOCR (ONNX 运行时，轻量快速，国内中文与公式效果佳)
    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        res, _ = engine(str(p))
        if res:
            lines = [item[1] for item in res if item and len(item) > 1 and item[1].strip()]
            if lines:
                return "\n".join(lines)
    except Exception:
        pass

    # 2. 尝试 EasyOCR
    try:
        import easyocr
        reader = easyocr.Reader(['ch_sim', 'en'], gpu=False)
        res = reader.readtext(str(p), detail=0)
        if res:
            return "\n".join(res)
    except Exception:
        pass

    # 3. 尝试 pytesseract
    try:
        import pytesseract
        from PIL import Image
        text = pytesseract.image_to_string(Image.open(str(p)), lang="chi_sim+eng")
        if text.strip():
            return text.strip()
    except Exception:
        pass

    return None

def call_text_llm(messages, config, stream=True):
    """通用文本大模型调用 (支持流式与非流式)"""
    base_url = config.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
    url = f"{base_url}/chat/completions"
    api_key = config.get("api_key", "").strip()
    model = config.get("model", "deepseek-chat")

    if not api_key:
        msg = (
            "⚠️ 尚未配置大模型 API Key！\n\n"
            "请在终端运行 `ky config` 选择 `[1] 配置主大模型 API 与密钥`，\n"
            "或设置环境变量 `DEEPSEEK_API_KEY`。"
        )
        if stream:
            print(f"\n{msg}\n")
        return msg

    if stream and ky_cli and hasattr(ky_cli, "stream_chat"):
        return ky_cli.stream_chat(messages, config)

    # 非流式 HTTP POST
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Kaoyan-Vision-Solver/1.0"
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": config.get("temperature", 0.3),
        "stream": False
    }

    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return res["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        return f"[主模型 API 请求错误 {e.code}]: {err_msg}"
    except Exception as e:
        return f"[主模型连接异常]: {e}"

def solve_image_with_model(image_path, user_prompt="", config=None, stream=True):
    """
    使用多模态视觉模型对图片进行识别、解答与批改。
    双引擎融合：
      - 若配置了视觉模型 (如 GLM-4V, Qwen-VL, GPT-4o)，直接发送图片与提取文本；
      - 若配置纯文本模型 (如 deepseek-chat)，自动调起本地 RapidOCR 提取公式与题干，再由主模型按步骤评分标准批改！
    """
    if config is None:
        config = {}

    p = Path(image_path).expanduser().resolve()
    if not p.exists():
        err = f"未找到图片文件: {image_path}"
        if stream: print(f"\n[!] 错误: {err}\n")
        return err

    vision_model = config.get("vision_model")
    has_vision_model = bool(vision_model)

    # 1. 始终优先尝试本地 RapidOCR 进行物理文本与公式提取
    ocr_text = extract_text_with_local_ocr(p)

    # ── 场景 A: 存在专属多模态视觉大模型 ──
    if has_vision_model:
        model = vision_model
        base_url = (config.get("vision_base_url") or config.get("base_url", "https://api.deepseek.com/v1")).rstrip("/")
        api_key = (config.get("vision_api_key") or config.get("api_key", "")).strip()

        if not api_key:
            return "⚠️ 未配置视觉模型 API Key！请在终端运行 `ky config` 进行配置。"

        try:
            data_url, mime, filename = encode_image_to_base64(p)
            prompt_text = build_vision_prompt(user_prompt, config.get("active_subject", "math"), pre_extracted_text=ocr_text or "")

            url = f"{base_url}/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "Kaoyan-Vision-Solver/1.0"
            }
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
                                "detail": "high"
                            }
                        }
                    ]
                }
            ]

            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.2,
                "stream": stream
            }

            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")

            if not stream:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    return res["choices"][0]["message"]["content"]

            # 流式读取
            full_text = []
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                sys.stdout.write(content)
                                sys.stdout.flush()
                                full_text.append(content)
                    except Exception:
                        continue
            print()
            return "".join(full_text)

        except Exception as e:
            if stream:
                print(f"\n[多模态视觉 API 调用受阻: {e}，正在自动降级为本地 RapidOCR + 主模型解题...]\n")

    # ── 场景 B: 纯文本模型或视觉模型降级 ➔ 本地 RapidOCR 提取 + 主模型批改 ──
    if ocr_text and len(ocr_text.strip()) > 2:
        if stream:
            print(f"\n[\033[92m⚡ 考研视觉引擎已调起本地 RapidOCR 高精度识别题干与手写公式\033[0m]")
            print(f"\033[2m识别提取内容:\n{ocr_text[:200]}...\033[0m\n")
            print(f"[\033[96m考研私教正在按真题评分细则进行逐行核验与采分点批改...\033[0m]\n")

        ocr_prompt = (
            f"你是一位深谙中国研究生入学考试命题规范与阅卷标准的专属总教练。\n"
            f"以下是系统从学员上传的试卷/草稿图片（{p.name}）中高精度提取出的题目与手写步骤：\n\n"
            f"```text\n{ocr_text}\n```\n\n"
        )
        if user_prompt:
            ocr_prompt += f"学员补充疑问或做题困惑：\n{user_prompt}\n\n"

        ocr_prompt += (
            "请严格按照考研阅卷人标准进行批改与精讲：\n"
            "1. 【题面与题型定性】：规范化还原 LaTeX 公式 ($...$ 或 $$...$$)，点明属于哪一章节核心考点；\n"
            "2. 【步骤推导与采分点】：标出各推导步骤得分情况 (`[+2分]` 或 `[-1分]`)，指出跳步或逻辑漏洞；\n"
            "3. 【错因五分类】：若有失误，明确判定为概念漏洞/审题偏差/公式记错/计算失误/书写丢分；\n"
            "4. 【标准答案与解题处方】：给出规范满分标准解答，并提供针对性提分建议。"
        )

        sys_prompt = "你是一位深谙中国研究生入学考试命题规范与阅卷标准的考研专属总教练。"
        if ky_cli and hasattr(ky_cli, "build_system_prompt"):
            sys_prompt = ky_cli.build_system_prompt(config.get("active_subject", "math"))

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": ocr_prompt}
        ]
        return call_text_llm(messages, config, stream=stream)

    # ── 场景 C: 本地 OCR 未能提取到文本且无视觉模型 ──
    tip_md = (
        "### ⚠️ 图片内容未能清晰解析\n\n"
        "当前主模型为纯文本模型，且本地 OCR 未能从该图片中提取到足够的文字信息（可能因图片模糊、手写字体极浅或为纯几何图形）。\n\n"
        "**💡 推荐解决方案（任选其一，立即生效）：**\n"
        "1. **配置多模态视觉模型 (强力推荐)**：在终端输入 `/config` ➔ 选择 `[2] 配置多模态视觉大模型`，"
        "推荐接入 **智谱清言 GLM-4V-Flash (免费免审核)** 或 **阿里通义千问 Qwen2-VL**，即可直接看图答题与批改草稿！\n"
        "2. **文字/LaTeX 输入**：直接在输入框粘贴题目文字或手写公式，私教将立即按步骤采分点评分！"
    )
    if stream:
        print(f"\n{tip_md}\n")
    return tip_md
