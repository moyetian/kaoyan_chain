# -*- coding: utf-8 -*-
"""
向量嵌入引擎 (Vector Embedding Engine)

基于 ONNX Runtime 实现轻量级本地嵌入模型推理
- 模型：bge-small-zh-v1.5（中文 SOTA 小模型，~80MB）
- 推理引擎：ONNX Runtime（已在项目依赖中）
- 量化：FP16 或 INT8，降低内存占用
- 惰性加载：首次使用时才加载模型，避免启动延迟

技术细节：
- 输入：文本字符串
- 输出：384 维向量（bge-small）
- 批处理：支持批量编码，提升吞吐量
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Union

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent

# 全局状态
_ENCODER: Optional['VectorEncoder'] = None
HAS_ONNXRUNTIME = False

try:
    import onnxruntime as ort
    HAS_ONNXRUNTIME = True
except ImportError:
    logger.warning("onnxruntime 不可用，向量编码功能将被禁用")

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    logger.warning("numpy 不可用，向量编码功能将被禁用")


class VectorEncoder:
    """向量编码器（基于 ONNX Runtime）

    功能：
    1. 将文本转换为向量表示
    2. 支持批量编码
    3. 自动规范化（L2 normalization）
    """

    def __init__(self, model_path: Path | str | None = None):
        """初始化编码器

        Args:
            model_path: ONNX 模型路径，默认为 data/models/bge-small-zh-v1.5.onnx
        """
        if not HAS_ONNXRUNTIME or not HAS_NUMPY:
            raise RuntimeError("需要安装 onnxruntime 和 numpy 才能使用向量编码")

        if model_path is None:
            model_path = ROOT / "data" / "models" / "bge-small-zh-v1.5.onnx"

        self.model_path = Path(model_path)
        self.session: Optional[ort.InferenceSession] = None
        self.tokenizer = None

        # 模型配置
        self.max_length = 512
        self.embedding_dim = 384

        # 延迟加载标志
        self._loaded = False

    def _load_model(self):
        """延迟加载模型（首次使用时）"""
        if self._loaded:
            return

        if not self.model_path.exists():
            raise FileNotFoundError(
                f"模型文件不存在: {self.model_path}\n"
                f"请下载 bge-small-zh-v1.5 ONNX 模型并放置到该路径"
            )

        logger.info(f"正在加载向量编码模型: {self.model_path.name}")

        try:
            # 加载 ONNX 模型
            sess_options = ort.SessionOptions()
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            self.session = ort.InferenceSession(
                str(self.model_path),
                sess_options=sess_options,
                providers=['CPUExecutionProvider']
            )

            # 加载 tokenizer（简化版，实际应使用 transformers.AutoTokenizer）
            self._init_simple_tokenizer()

            self._loaded = True
            logger.info("✅ 向量编码模型加载成功")

        except Exception as e:
            logger.error(f"加载向量编码模型失败: {e}")
            raise

    def _init_simple_tokenizer(self):
        """初始化 tokenizer

        尝试加载完整 tokenizer，失败则使用简化版
        """
        tokenizer_path = self.model_path.parent / "tokenizer.json"

        # 尝试加载 transformers tokenizer
        try:
            from transformers import AutoTokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(
                str(self.model_path.parent),
                local_files_only=True
            )
            logger.info("✅ 加载完整 tokenizer 成功")
            return
        except Exception as e:
            logger.debug(f"transformers tokenizer 加载失败: {e}")

        # 降级：尝试加载 tokenizer.json
        if tokenizer_path.exists():
            try:
                from tokenizers import Tokenizer
                self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
                logger.info("✅ 加载 tokenizers.Tokenizer 成功")
                return
            except Exception as e:
                logger.debug(f"tokenizers.Tokenizer 加载失败: {e}")

        # 最后降级：简化版
        logger.warning("使用简化版 tokenizer，仅供测试")
        self.tokenizer = None

    def _tokenize(self, texts: List[str]) -> dict:
        """文本分词

        Args:
            texts: 文本列表

        Returns:
            包含 input_ids, attention_mask 的字典
        """
        import numpy as np

        # 使用 transformers tokenizer
        if self.tokenizer and hasattr(self.tokenizer, 'encode_plus'):
            try:
                encoded = self.tokenizer(
                    texts,
                    padding='max_length',
                    truncation=True,
                    max_length=self.max_length,
                    return_tensors='np'
                )
                return {
                    "input_ids": encoded['input_ids'].astype(np.int64),
                    "attention_mask": encoded['attention_mask'].astype(np.int64)
                }
            except Exception as e:
                logger.error(f"tokenizer 编码失败: {e}")

        # 降级：简化版（仅用于架构测试）
        logger.warning("使用简化版分词，结果仅供测试")
        batch_size = len(texts)
        input_ids = np.zeros((batch_size, self.max_length), dtype=np.int64)
        attention_mask = np.ones((batch_size, self.max_length), dtype=np.int64)

        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask
        }

    def encode(
        self,
        texts: Union[str, List[str]],
        normalize: bool = True,
        show_progress: bool = False
    ) -> Union[List[float], List[List[float]]]:
        """编码文本为向量

        Args:
            texts: 单个文本或文本列表
            normalize: 是否 L2 归一化
            show_progress: 是否显示进度条（批量编码时）

        Returns:
            单个向量或向量列表
        """
        # 延迟加载模型
        if not self._loaded:
            self._load_model()

        # 统一为列表
        single_input = isinstance(texts, str)
        if single_input:
            texts = [texts]

        try:
            # 分词
            inputs = self._tokenize(texts)

            # 推理
            outputs = self.session.run(
                None,
                {
                    "input_ids": inputs["input_ids"],
                    "attention_mask": inputs["attention_mask"]
                }
            )

            # 提取向量（通常是最后一层的 [CLS] token 表示）
            embeddings = outputs[0][:, 0, :]  # shape: (batch_size, embedding_dim)

            # L2 归一化
            if normalize:
                import numpy as np
                norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                embeddings = embeddings / (norms + 1e-8)

            # 转换为 Python list
            result = embeddings.tolist()

            return result[0] if single_input else result

        except Exception as e:
            logger.error(f"向量编码失败: {e}")
            raise

    def encode_query(self, query: str) -> List[float]:
        """编码查询文本（便捷方法）

        Args:
            query: 查询文本

        Returns:
            查询向量
        """
        return self.encode(query, normalize=True)

    def encode_documents(self, documents: List[str], batch_size: int = 32) -> List[List[float]]:
        """批量编码文档（便捷方法）

        Args:
            documents: 文档列表
            batch_size: 批处理大小

        Returns:
            文档向量列表
        """
        all_embeddings = []

        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]
            embeddings = self.encode(batch, normalize=True)
            all_embeddings.extend(embeddings)

        return all_embeddings


def get_encoder() -> Optional[VectorEncoder]:
    """获取全局编码器实例（单例模式）

    Returns:
        VectorEncoder 实例或 None（如果依赖不满足）
    """
    global _ENCODER

    if not HAS_ONNXRUNTIME or not HAS_NUMPY:
        logger.warning("onnxruntime 或 numpy 不可用，向量编码器无法初始化")
        return None

    if _ENCODER is None:
        try:
            _ENCODER = VectorEncoder()
        except Exception as e:
            logger.error(f"初始化向量编码器失败: {e}")
            return None

    return _ENCODER


def encode_text(text: str) -> Optional[List[float]]:
    """编码单个文本（便捷函数）

    Args:
        text: 文本内容

    Returns:
        向量表示或 None
    """
    encoder = get_encoder()
    if encoder is None:
        return None

    try:
        return encoder.encode_query(text)
    except Exception as e:
        logger.error(f"文本编码失败: {e}")
        return None


def encode_texts(texts: List[str], batch_size: int = 32) -> Optional[List[List[float]]]:
    """批量编码文本（便捷函数）

    Args:
        texts: 文本列表
        batch_size: 批处理大小

    Returns:
        向量列表或 None
    """
    encoder = get_encoder()
    if encoder is None:
        return None

    try:
        return encoder.encode_documents(texts, batch_size=batch_size)
    except Exception as e:
        logger.error(f"批量文本编码失败: {e}")
        return None


if __name__ == "__main__":
    # 测试
    logging.basicConfig(level=logging.INFO)

    print("向量编码器测试")
    print(f"ONNX Runtime 可用: {HAS_ONNXRUNTIME}")
    print(f"NumPy 可用: {HAS_NUMPY}")

    if HAS_ONNXRUNTIME and HAS_NUMPY:
        print("\n注意: 完整功能需要下载 bge-small-zh-v1.5.onnx 模型")
        print("当前为占位实现，仅供架构测试")

        # encoder = get_encoder()
        # if encoder:
        #     test_text = "华中科技大学计算机考研复试线"
        #     embedding = encode_text(test_text)
        #     if embedding:
        #         print(f"编码成功，向量维度: {len(embedding)}")
    else:
        print("缺少必要依赖，请安装: pip install onnxruntime numpy")
