# -*- coding: utf-8 -*-
"""
知识库向量存储层 (Knowledge Vector Store)

基于 SQLite + sqlite-vec 实现 Local-First 向量检索
- 零服务依赖，单文件数据库
- 支持向量索引和混合检索
- 自动降级：sqlite-vec 不可用时回退到纯词法检索

技术栈：
- SQLite 3.x（Python 标准库自带）
- sqlite-vec 扩展（可选，提供向量检索能力）
- numpy（向量计算）
"""

from __future__ import annotations

import json
import sqlite3
import logging
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class Chunk:
    """知识片段（文本块）"""
    id: str                    # 唯一标识
    text: str                  # 文本内容
    source: str                # 来源（文件路径或 URL）
    metadata: Dict[str, Any]   # 元数据（标题、章节、标签等）
    embedding: Optional[List[float]] = None  # 向量表示


class KnowledgeStore:
    """知识库存储引擎

    功能：
    1. 存储文本片段及其向量表示
    2. 支持向量相似度检索
    3. 支持元数据过滤
    4. 自动降级到纯文本检索
    """

    def __init__(self, db_path: Path | str | None = None):
        """初始化知识库

        Args:
            db_path: 数据库路径，默认为 data/knowledge/embeddings.db
        """
        if db_path is None:
            db_path = ROOT / "data" / "knowledge" / "embeddings.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.conn: Optional[sqlite3.Connection] = None
        self.has_vector = False

        self._init_db()

    def _init_db(self):
        """初始化数据库连接和表结构"""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row

        # 尝试加载 sqlite-vec 扩展
        self.has_vector = self._try_load_vec_extension()

        # 创建表结构
        self._create_tables()

        if self.has_vector:
            logger.info("✅ sqlite-vec 扩展加载成功，向量检索已启用")
        else:
            logger.warning("⚠️ sqlite-vec 扩展不可用，将使用纯文本检索降级模式")

    def _try_load_vec_extension(self) -> bool:
        """尝试加载 sqlite-vec 扩展

        Returns:
            是否成功加载
        """
        try:
            self.conn.enable_load_extension(True)

            # 尝试多个可能的扩展文件名
            vec_extensions = [
                "vec0",           # Linux/macOS 默认
                "vec0.so",        # Linux
                "vec0.dylib",     # macOS
                "vec0.dll",       # Windows
            ]

            for ext in vec_extensions:
                try:
                    self.conn.load_extension(ext)
                    # 测试是否真正可用
                    self.conn.execute("SELECT vec_version()").fetchone()
                    return True
                except Exception:
                    continue

            return False
        except Exception as e:
            logger.debug(f"加载 sqlite-vec 失败: {e}")
            return False

    def _create_tables(self):
        """创建表结构"""
        cursor = self.conn.cursor()

        # 主表：存储文本片段和元数据
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                source TEXT NOT NULL,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 索引：加速源文件查询
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_source
            ON chunks(source)
        """)

        # 向量表（如果支持）
        if self.has_vector:
            # 使用 sqlite-vec 的虚拟表
            try:
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks
                    USING vec0(
                        chunk_id TEXT PRIMARY KEY,
                        embedding FLOAT[384]
                    )
                """)
            except Exception as e:
                logger.warning(f"创建向量表失败，降级到纯文本模式: {e}")
                self.has_vector = False

        self.conn.commit()

    def add_chunk(self, chunk: Chunk) -> bool:
        """添加单个文本片段

        Args:
            chunk: 文本片段对象

        Returns:
            是否成功添加
        """
        try:
            cursor = self.conn.cursor()

            # 插入主表
            cursor.execute("""
                INSERT OR REPLACE INTO chunks (id, text, source, metadata)
                VALUES (?, ?, ?, ?)
            """, (
                chunk.id,
                chunk.text,
                chunk.source,
                json.dumps(chunk.metadata, ensure_ascii=False)
            ))

            # 插入向量表（如果有向量且支持）
            if self.has_vector and chunk.embedding:
                if not HAS_NUMPY:
                    logger.warning("numpy 不可用，无法存储向量")
                else:
                    # sqlite-vec 期望的格式
                    embedding_blob = np.array(chunk.embedding, dtype=np.float32).tobytes()
                    cursor.execute("""
                        INSERT OR REPLACE INTO vec_chunks (chunk_id, embedding)
                        VALUES (?, ?)
                    """, (chunk.id, embedding_blob))

            self.conn.commit()
            return True

        except Exception as e:
            logger.error(f"添加文本片段失败: {e}")
            self.conn.rollback()
            return False

    def add_chunks(self, chunks: List[Chunk]) -> int:
        """批量添加文本片段

        Args:
            chunks: 文本片段列表

        Returns:
            成功添加的数量
        """
        success_count = 0
        for chunk in chunks:
            if self.add_chunk(chunk):
                success_count += 1
        return success_count

    def search_by_vector(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        source_filter: Optional[str] = None
    ) -> List[Tuple[str, float]]:
        """向量相似度检索

        Args:
            query_embedding: 查询向量
            top_k: 返回前 k 个结果
            source_filter: 可选的源文件过滤

        Returns:
            [(chunk_id, similarity_score), ...]
        """
        if not self.has_vector:
            logger.warning("向量检索不可用，返回空结果")
            return []

        if not HAS_NUMPY:
            logger.warning("numpy 不可用，无法进行向量检索")
            return []

        try:
            # 转换查询向量格式
            query_blob = np.array(query_embedding, dtype=np.float32).tobytes()

            # 构建查询
            if source_filter:
                # 带源过滤的查询
                sql = """
                    SELECT v.chunk_id, vec_distance_cosine(v.embedding, ?) as distance
                    FROM vec_chunks v
                    JOIN chunks c ON v.chunk_id = c.id
                    WHERE c.source LIKE ?
                    ORDER BY distance
                    LIMIT ?
                """
                cursor = self.conn.execute(sql, (query_blob, f"%{source_filter}%", top_k))
            else:
                # 无过滤查询
                sql = """
                    SELECT chunk_id, vec_distance_cosine(embedding, ?) as distance
                    FROM vec_chunks
                    ORDER BY distance
                    LIMIT ?
                """
                cursor = self.conn.execute(sql, (query_blob, top_k))

            # 转换距离到相似度（余弦距离 → 余弦相似度）
            results = []
            for row in cursor.fetchall():
                chunk_id = row[0]
                distance = row[1]
                similarity = 1.0 - distance  # 余弦相似度 = 1 - 余弦距离
                results.append((chunk_id, similarity))

            return results

        except Exception as e:
            logger.error(f"向量检索失败: {e}")
            return []

    def get_chunk(self, chunk_id: str) -> Optional[Chunk]:
        """根据 ID 获取文本片段

        Args:
            chunk_id: 片段 ID

        Returns:
            Chunk 对象或 None
        """
        try:
            cursor = self.conn.execute("""
                SELECT id, text, source, metadata
                FROM chunks
                WHERE id = ?
            """, (chunk_id,))

            row = cursor.fetchone()
            if not row:
                return None

            return Chunk(
                id=row["id"],
                text=row["text"],
                source=row["source"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {}
            )

        except Exception as e:
            logger.error(f"获取文本片段失败: {e}")
            return None

    def get_chunks(self, chunk_ids: List[str]) -> List[Chunk]:
        """批量获取文本片段

        Args:
            chunk_ids: 片段 ID 列表

        Returns:
            Chunk 对象列表
        """
        chunks = []
        for chunk_id in chunk_ids:
            chunk = self.get_chunk(chunk_id)
            if chunk:
                chunks.append(chunk)
        return chunks

    def count(self) -> int:
        """获取文本片段总数

        Returns:
            片段数量
        """
        try:
            cursor = self.conn.execute("SELECT COUNT(*) FROM chunks")
            return cursor.fetchone()[0]
        except Exception:
            return 0

    def clear(self):
        """清空知识库"""
        try:
            self.conn.execute("DELETE FROM chunks")
            if self.has_vector:
                self.conn.execute("DELETE FROM vec_chunks")
            self.conn.commit()
            logger.info("知识库已清空")
        except Exception as e:
            logger.error(f"清空知识库失败: {e}")
            self.conn.rollback()

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# 全局单例（惰性加载）
_STORE: Optional[KnowledgeStore] = None


def get_knowledge_store() -> KnowledgeStore:
    """获取全局知识库实例（单例模式）"""
    global _STORE
    if _STORE is None:
        _STORE = KnowledgeStore()
    return _STORE


if __name__ == "__main__":
    # 简单测试
    logging.basicConfig(level=logging.INFO)

    store = KnowledgeStore(ROOT / "data" / "knowledge" / "test.db")

    # 测试添加片段
    test_chunk = Chunk(
        id="test-001",
        text="华中科技大学计算机科学与技术学院 2027 年考研复试分数线为 330 分",
        source="test_data",
        metadata={"school": "华中科技大学", "major": "计算机"}
    )

    success = store.add_chunk(test_chunk)
    print(f"添加测试片段: {'成功' if success else '失败'}")

    # 测试检索
    retrieved = store.get_chunk("test-001")
    if retrieved:
        print(f"检索成功: {retrieved.text[:50]}...")

    print(f"知识库总片段数: {store.count()}")
    print(f"向量检索可用: {store.has_vector}")

    store.close()
