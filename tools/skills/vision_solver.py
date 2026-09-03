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

def solve_image_with_model(image_path, user_prompt="", config=None, stream=True):
    """
    使用多模态视觉模型对图片进行识别、解答与批改
    支持流式输出打印
    """
    if config is None:
        config = {}

    data_url, mime, filename = encode_image_to_base64(image_path)
    prompt_text = build_vision_prompt(user_prompt, config.get("active_subject", "math"))

    # 判断使用的模型：优先使用专门的 vision_model，否则使用当前 model
    model = config.get("vision_model") or config.get("model", "deepseek-chat")
    base_url = config.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
    api_key = config.get("api_key", "").strip()

    # 如果当前配置是纯文本 deepseek-chat，且用户未配置 vision_model，则提示用户
    if "deepseek-chat" in model and not config.get("vision_model"):
        # 很多用户使用 SiliconFlow / Qwen / OpenRouter 的视觉模型
        pass

    url = f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Kaoyan-Vision-Solver/1.0"
    }

    # 标准 OpenAI 视觉格式
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
        print("提示：若当前服务商的默认模型不支持图片输入，请在 /config 中配置支持 Vision 的多模态模型（如 qwen-vl-max, glm-4v, gpt-4o 等）。")
        return ""
    except Exception as e:
        print(f"\n[网络异常]: {e}\n")
        return ""
