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
MODEL_CONFIG = {
    "bge-small-zh-v1.5": {
        "onnx_url": "https://huggingface.co/BAAI/bge-small-zh-v1.5/resolve/main/onnx/model.onnx",
        "tokenizer_url": "https://huggingface.co/BAAI/bge-small-zh-v1.5/resolve/main/tokenizer.json",
        "vocab_url": "https://huggingface.co/BAAI/bge-small-zh-v1.5/resolve/main/vocab.txt",
        "config_url": "https://huggingface.co/BAAI/bge-small-zh-v1.5/resolve/main/config.json",
        "size_mb": 80,
        "embedding_dim": 384,
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

    # 下载 config
    config_path = MODELS_DIR / "config.json"
    download_file(config['config_url'], config_path, "配置文件")

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
