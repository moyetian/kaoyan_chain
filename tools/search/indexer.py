# -*- coding: utf-8 -*-
"""
知识库索引构建器 (Knowledge Indexer)

功能：
1. 从项目资源构建初始知识库索引
   - 院校库（data/universities/registry.json）
   - 参考资料（04-专业课/参考资料/）
   - 技能文档（tools/skills/）
2. 增量更新索引
3. 显示构建进度
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Generator
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class Document:
    """待索引的文档"""
    id: str
    text: str
    source: str
    metadata: dict


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """将长文本切分为片段

    Args:
        text: 原始文本
        chunk_size: 片段大小（字符数）
        overlap: 重叠大小（字符数）

    Returns:
        文本片段列表
    """
    # [S4 修复·边界] 空串旧返回 [""]（下游插入空 chunk）；overlap>=chunk_size
    # 时 start 不推进死循环。默认参数安全，此为公开函数缺校验。
    # [B4 修复·死循环] 入口新增 chunk_size<=0 校验：此前 chunk_size=0 时
    # overlap(0)>=chunk_size(0) 归零、len(text)<=0 为假，进入循环后
    # end=start+0 恒等、start=end-overlap 永不推进 —— 实测 timeout 8s 挂死。
    # 公开函数 fail-fast；循环末尾另有「必然推进」兜底（见下）。
    if not (text or "").strip():
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须为正整数")
    if overlap >= chunk_size:
        overlap = 0
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]

        # 尽量在句子边界切分
        if end < len(text):
            # 查找最后的句号、问号、感叹号
            last_punct = max(
                chunk.rfind('。'),
                chunk.rfind('？'),
                chunk.rfind('！'),
                chunk.rfind('\n')
            )
            if last_punct > chunk_size // 2:
                chunk = chunk[:last_punct + 1]
                end = start + last_punct + 1

        chunks.append(chunk.strip())
        # [B4 修复·死循环] 兜底保证 start 每轮必然前进：正常路径 end-overlap
        # 必大于 start（因 chunk_size>overlap），异常路径退回按 chunk_size 步进。
        new_start = end - overlap
        start = new_start if new_start > start else start + max(1, chunk_size)

    return chunks


def load_universities() -> Generator[Document, None, None]:
    """加载院校库"""
    registry_path = ROOT / "data" / "universities" / "registry.json"

    if not registry_path.exists():
        logger.warning(f"院校库不存在: {registry_path}")
        return

    try:
        with open(registry_path, encoding='utf-8') as f:
            registry = json.load(f)

        for code, info in registry.items():
            name = info.get('name', '')
            aliases = info.get('aliases', [])
            level = info.get('level', [])
            region = info.get('region', '')

            # 构建文本
            text = f"{name}"
            if aliases:
                text += f" ({', '.join(aliases)})"
            text += f"\n院校代码: {code}"
            if region:
                text += f"\n地区: {region}"
            if level:
                text += f"\n层次: {', '.join(level)}"

            # 添加学院信息
            departments = info.get('departments', {})
            if departments:
                text += "\n学院专业:"
                for dept_key, dept_info in departments.items():
                    dept_name = dept_info.get('college_name', dept_key)
                    text += f"\n  - {dept_name}"

            yield Document(
                id=f"univ-{code}",
                text=text,
                source=f"universities/{code}",
                metadata={
                    "type": "university",
                    "code": code,
                    "name": name,
                    "region": region
                }
            )

    except Exception as e:
        logger.error(f"加载院校库失败: {e}")


def load_materials() -> Generator[Document, None, None]:
    """加载参考资料"""
    materials_dir = ROOT / "04-专业课" / "参考资料"

    if not materials_dir.exists():
        logger.warning(f"参考资料目录不存在: {materials_dir}")
        return

    for file_path in materials_dir.glob("**/*.md"):
        if file_path.name.startswith('.'):
            continue

        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')

            # 提取标题
            lines = content.split('\n')
            title = file_path.stem
            for line in lines[:10]:
                if line.startswith('# '):
                    title = line[2:].strip()
                    break

            # 切分长文档
            chunks = chunk_text(content, chunk_size=500, overlap=50)

            for i, chunk in enumerate(chunks):
                chunk_id = f"material-{file_path.stem}-{i}"
                yield Document(
                    id=chunk_id,
                    text=chunk,
                    source=str(file_path.relative_to(ROOT)),
                    metadata={
                        "type": "material",
                        "title": title,
                        "chunk_index": i,
                        "total_chunks": len(chunks)
                    }
                )

        except Exception as e:
            logger.error(f"加载资料失败 {file_path}: {e}")


def build_index(enable_vector: bool = True, show_progress: bool = True):
    """构建知识库索引

    Args:
        enable_vector: 是否启用向量编码
        show_progress: 是否显示进度
    """
    from .knowledge_store import get_knowledge_store, Chunk
    from .vector import get_encoder

    store = get_knowledge_store()
    encoder = None

    # 检查向量编码器
    if enable_vector:
        encoder = get_encoder()
        if encoder is None:
            logger.warning("向量编码器不可用，将只构建文本索引")
        elif not store.has_vector:
            logger.warning("向量存储不可用，将只构建文本索引")
            encoder = None

    print("=== 开始构建知识库索引 ===\n")

    # 收集所有文档
    documents: List[Document] = []

    print("1. 加载院校库...")
    univ_docs = list(load_universities())
    documents.extend(univ_docs)
    print(f"   ✅ 加载 {len(univ_docs)} 所院校\n")

    print("2. 加载参考资料...")
    material_docs = list(load_materials())
    documents.extend(material_docs)
    print(f"   ✅ 加载 {len(material_docs)} 个文档片段\n")

    total = len(documents)
    print(f"总计: {total} 个文档片段")

    if total == 0:
        print("⚠️ 没有找到可索引的文档")
        return

    # 批量编码（如果启用向量）
    embeddings = []
    if encoder:
        print("\n3. 生成向量表示...")
        try:
            texts = [doc.text for doc in documents]
            embeddings = encoder.encode_documents(texts, batch_size=32)
            print(f"   ✅ 完成 {len(embeddings)} 个向量编码\n")
        except Exception as e:
            logger.error(f"向量编码失败: {e}")
            embeddings = []

    # 插入知识库
    print("4. 插入知识库...")
    success_count = 0

    for i, doc in enumerate(documents):
        embedding = embeddings[i] if i < len(embeddings) else None

        chunk = Chunk(
            id=doc.id,
            text=doc.text,
            source=doc.source,
            metadata=doc.metadata,
            embedding=embedding
        )

        if store.add_chunk(chunk):
            success_count += 1

        # 进度提示
        if show_progress and (i + 1) % 50 == 0:
            print(f"   进度: {i + 1}/{total}")

    print(f"   ✅ 成功插入 {success_count}/{total} 个片段\n")

    # 统计
    print("=== 索引构建完成 ===")
    print(f"知识库路径: {store.db_path}")
    print(f"总片段数: {store.count()}")
    print(f"向量索引: {'已启用' if store.has_vector and embeddings else '未启用'}")


def rebuild_index():
    """重建索引（清空后重新构建）"""
    from .knowledge_store import get_knowledge_store

    store = get_knowledge_store()

    print("⚠️ 将清空现有索引并重新构建")
    response = input("是否继续？(y/N): ").strip().lower()

    if response != 'y':
        print("已取消")
        return

    print("\n清空现有索引...")
    store.clear()

    build_index()


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s: %(message)s'
    )

    if len(sys.argv) > 1 and sys.argv[1] == 'rebuild':
        rebuild_index()
    else:
        build_index()
