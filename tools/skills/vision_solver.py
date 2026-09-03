# -*- coding: utf-8 -*-
"""
考研专属视觉解题与手写批改技能 (Vision & OCR Solver Skill)
功能：
  1. 支持图片输入 (拖拽图片路径、指定路径、Base64)
  2. 数学公式 LaTeX 识别与高精度题面提取
  3. 手写推导草稿逐行批改、采分点赋分、错因五分类判定
  4. 支持多模态大模型 (Qwen-VL, GLM-4V, GPT-4o, Claude, DeepSeek-VL) 与本地 OCR 降级
"""

import os
import sys
import base64
import json
import mimetypes
import urllib.request
import urllib.error
from pathlib import Path

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

def build_vision_prompt(user_instruction="", subject="math"):
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
        "- 给出该题规范、简洁的标准正解推导示范，并提出下一阶段的针对性改进处方。"
    )

    if user_instruction:
        return f"{default_prompt}\n\n学员补充说明或具体疑问：\n{user_instruction}"
    return default_prompt

def extract_text_with_local_ocr(image_path):
    """
    尝试使用本地极速 OCR 引擎 (RapidOCR / EasyOCR / Tesseract) 提取图片中的题干与公式
    纯本地离线运行，免外部 API，数学公式与中英文识别率极高
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

def solve_image_with_model(image_path, user_prompt="", config=None, stream=True):
    """
    使用多模态视觉模型对图片进行识别、解答与批改
    支持流式输出打印。若当前配置为主流纯文本模型 (如 deepseek-chat)，
    将自动调起本地 RapidOCR 提取题干与公式，再由大模型按考研真题阅卷标准逐行批改！
    """
    if config is None:
        config = {}

    p = Path(image_path).expanduser().resolve()
    if not p.exists():
        print(f"\n[!] 错误: 未找到图片文件 {image_path}\n")
        return ""

    # 判断使用的模型与接口
    vision_model = config.get("vision_model")
    model = vision_model or config.get("model", "deepseek-chat")
    base_url = (config.get("vision_base_url") or config.get("base_url", "https://api.deepseek.com/v1")).rstrip("/")
    api_key = (config.get("vision_api_key") or config.get("api_key", "")).strip()

    # 判断是否为纯文本模型 (例如官方 deepseek-chat 不支持图像输入)
    is_pure_text_model = ("deepseek" in model.lower() and "vl" not in model.lower()) or ("text" in model.lower())

    # ── 场景 A: 纯文本模型 (如 DeepSeek-Chat) ➔ 自动触发本地 OCR 提取 ──
    if is_pure_text_model and not vision_model:
        ocr_text = extract_text_with_local_ocr(p)
        if ocr_text:
            print(f"\n[\033[92m⚡ 考研视觉引擎已调起本地 RapidOCR 识别题干与手写公式\033[0m]")
            print(f"\033[2m识别提取内容预览:\n{ocr_text[:180]}...\033[0m\n")
            print(f"[\033[96m考研私教正在按真题评分细则进行逐行核验与采分点批改...\033[0m]\n")

            # 构建结构化 OCR 批改 Prompt
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

            # 调用大语言模型进行解答
            from tools import ky_cli
            messages = [
                {"role": "system", "content": ky_cli.build_system_prompt(config.get("active_subject", "math"))},
                {"role": "user", "content": ocr_prompt}
            ]
            return ky_cli.stream_chat(messages, config)
        else:
            # 本地未安装 OCR 库且当前是纯文本模型时的友好指引
            print("""
\033[93m╭────────────────────────────────────────────────────────────────────────╮\033[0m
\033[93m│\033[0m \033[1m⚠️ 【视觉看图大模型与本地 OCR 提示】\033[0m                                   \033[93m│\033[0m
\033[93m│\033[0m 您当前配置的主模型为「deepseek-chat」，该模型为纯文本模型，不支持看图。\033[93m│\033[0m
\033[93m│\033[0m                                                                        \033[93m│\033[0m
\033[93m│\033[0m \033[1m💡 推荐解决方案（任选其一，立即生效）：\033[0m                                 \033[93m│\033[0m
\033[93m│\033[0m  1. \033[92m配置多模态视觉模型\033[0m：输入 /config ➔ 选择视觉模型 (GLM-4V/Qwen-VL)   \033[93m│\033[0m
\033[93m│\033[0m  2. \033[92m开启本地离线 OCR\033[0m：运行 pip install rapidocr_onnxruntime 离线识别   \033[93m│\033[0m
\033[93m│\033[0m  3. \033[92m浏览器伴侣输入\033[0m：输入 /view 打开伴侣网页，直接复制粘贴题目文本。    \033[93m│\033[0m
\033[93m╰────────────────────────────────────────────────────────────────────────╯\033[0m
""")
            return ""

    # ── 场景 B: 配置了多模态视觉大模型 (如 Qwen-VL, GLM-4V, GPT-4o) ──
    data_url, mime, filename = encode_image_to_base64(p)
    prompt_text = build_vision_prompt(user_prompt, config.get("active_subject", "math"))

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
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return res["choices"][0]["message"]["content"]
        except Exception as e:
            return f"[视觉识别请求失败]: {e}"

    # 流式读取
    full_text = []
    try:
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
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        print(f"\n[视觉 API 错误 {e.code}]: {err_msg}\n")
        print("提示：若当前服务商不支持图片多模态，可在 /config 中配置专用的多模态模型 (如 GLM-4V, Qwen2-VL 等)。")
        return ""
    except Exception as e:
        print(f"\n[网络异常]: {e}\n")
        return ""
