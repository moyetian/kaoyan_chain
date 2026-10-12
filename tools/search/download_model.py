# -*- coding: utf-8 -*-
"""
模型下载和管理工具

负责下载和管理向量编码模型（bge-small-zh-v1.5）
"""

import os
import sys
from pathlib import Path
import urllib.request
import json

ROOT = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = ROOT / "data" / "models"

# 模型配置
#: [P0 修复·2026-10-11 第五轮深审] 换模型下载源：
#: 旧 URL ``BAAI/bge-small-zh-v1.5/resolve/main/onnx/model.onnx`` **404**
#: （BAAI 官方仓库没有 onnx/ 子目录，实测 HTTP 404）——下载脚本从写下那天
#: 起就不可能成功。改用 ``Xenova/bge-small-zh-v1.5``（transformers.js 官方
#: ONNX 转换仓库，实测全套 200）：onnx/model.onnx 94.8MB。
#: embedding_dim 同步修正 384 → 512（bge-small-zh 的 hidden_size）。
MODEL_CONFIG = {
    "bge-small-zh-v1.5": {
        "onnx_url": "https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/main/onnx/model.onnx",
        "tokenizer_url": "https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/main/tokenizer.json",
        "vocab_url": "https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/main/vocab.txt",
        "config_url": "https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/main/config.json",
        # AutoTokenizer.from_pretrained(本地目录) 还需要这两个配套文件；
        # 缺 tokenizer_config.json 时 fast tokenizer 可能回落到慢速路径。
        "tokenizer_config_url": "https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/main/tokenizer_config.json",
        "special_tokens_url": "https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/main/special_tokens_map.json",
        "size_mb": 95,
        "embedding_dim": 512,
    }
}


def download_file(url: str, dest: Path, desc: str = ""):
    """下载文件并显示进度"""
    print(f"下载 {desc or url} ...")
    print(f"目标: {dest}")

    try:
        # 创建目录
        dest.parent.mkdir(parents=True, exist_ok=True)

        # 下载
        urllib.request.urlretrieve(url, dest)

        size_mb = dest.stat().st_size / 1024 / 1024
        print(f"✅ 下载完成 ({size_mb:.1f} MB)")
        return True

    except Exception as e:
        print(f"❌ 下载失败: {e}")
        return False


def check_model_exists(model_name: str = "bge-small-zh-v1.5") -> bool:
    """检查模型是否已存在"""
    model_path = MODELS_DIR / f"{model_name}.onnx"
    tokenizer_path = MODELS_DIR / "tokenizer.json"

    exists = model_path.exists() and tokenizer_path.exists()

    if exists:
        size_mb = model_path.stat().st_size / 1024 / 1024
        print(f"✅ 模型已存在: {model_path} ({size_mb:.1f} MB)")
    else:
        print(f"⚠️ 模型不存在: {model_path}")

    return exists


def download_model(model_name: str = "bge-small-zh-v1.5"):
    """下载指定模型"""
    config = MODEL_CONFIG.get(model_name)
    if not config:
        print(f"❌ 未知模型: {model_name}")
        return False

    print(f"\n开始下载模型: {model_name}")
    print(f"预计大小: ~{config['size_mb']} MB")
    print(f"嵌入维度: {config['embedding_dim']}")
    print()

    # 下载 ONNX 模型
    model_path = MODELS_DIR / f"{model_name}.onnx"
    if not download_file(config['onnx_url'], model_path, "ONNX 模型"):
        return False

    # 下载 tokenizer
    tokenizer_path = MODELS_DIR / "tokenizer.json"
    if not download_file(config['tokenizer_url'], tokenizer_path, "Tokenizer"):
        return False

    # 下载 vocab（部分 tokenizer 路径回退用）
    download_file(config['vocab_url'], MODELS_DIR / "vocab.txt", "Vocab")

    # 下载 config
    config_path = MODELS_DIR / "config.json"
    download_file(config['config_url'], config_path, "配置文件")

    # [P0 修复·2026-10-11] 配套 tokenizer 元文件（AutoTokenizer 本地加载需要）
    download_file(config['tokenizer_config_url'],
                  MODELS_DIR / "tokenizer_config.json", "Tokenizer 配置")
    download_file(config['special_tokens_url'],
                  MODELS_DIR / "special_tokens_map.json", "特殊 token 映射")

    print("\n✅ 所有文件下载完成！")
    return True


if __name__ == "__main__":
    print("=== BGE 模型下载工具 ===\n")

    # 检查是否已存在
    if check_model_exists():
        print("\n模型已存在，无需重复下载")
        print("如需重新下载，请删除 data/models/ 目录下的文件")
        sys.exit(0)

    # 询问是否下载
    print("\n模型来源: Hugging Face (BAAI/bge-small-zh-v1.5)")
    print("注意: 下载需要稳定的网络连接，可能需要几分钟")

    response = input("\n是否开始下载？(y/N): ").strip().lower()
    if response != 'y':
        print("已取消下载")
        sys.exit(0)

    # 开始下载
    success = download_model()

    if success:
        print("\n✅ 模型下载成功！")
        print(f"位置: {MODELS_DIR}")
    else:
        print("\n❌ 模型下载失败")
        print("请检查网络连接，或手动从 Hugging Face 下载")
        sys.exit(1)
