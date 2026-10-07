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
import sys
import threading
from pathlib import Path

# [F3 修复·脚本直跑导入引导] `py tools/search/knowledge_store.py` 时 sys.path[0]
# 是 tools/search/，`from workspace`（在 tools/ 下）与 `from tools.workspace`
# （需仓库根）双双失败（实测 ModuleNotFoundError）。按 init_workspace.py
# 既有模式把 tools/search、tools、仓库根插入 path 后再导入。
_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE.parent), str(_HERE.parent.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

logger = logging.getLogger(__name__)

ROOT = resolve_workspace_root(__file__)

#: 默认知识库路径（单一真源）。调用方若只想「读」知识库（如 ky rag），
#: 应先判这个文件是否存在 —— KnowledgeStore() 一构造就会建库建表，
#: 在空工作区里凭空造出一个空库，对只读语义是副作用。
DEFAULT_DB_PATH = ROOT / "data" / "knowledge" / "embeddings.db"


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
    5. 线程安全：连接为线程本地（每线程独立连接 + WAL 并发读写）
    """

    def __init__(self, db_path: Path | str | None = None):
        """初始化知识库

        Args:
            db_path: 数据库路径，默认为 data/knowledge/embeddings.db
        """
        if db_path is None:
            db_path = DEFAULT_DB_PATH

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # [R4 #207 修复·跨线程 SQLite] sqlite3 连接默认绑定创建它的线程，
        # 跨线程使用会抛 ``ProgrammingError: SQLite objects created in a
        # thread can only be used in that same thread``。GUI 的「建索引」与
        # 「本地检索」各在独立 QThread 里执行，却共享全局单例 → 检索线程
        # 复用索引线程的连接 → 报错被 hybrid 词法分支吞掉 → 静默 0 条，
        # 界面还提示「知识库是空的」。
        # 修法：连接改为**线程本地** —— 每线程各自持有连接到同一库文件
        # （WAL 模式保证读写并发互不阻塞）；close() 只关当前线程的连接。
        self._local = threading.local()
        self.has_vector = False

        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        """当前线程的数据库连接（首次访问时惰性建立）。

        线程本地：同一线程内多次访问复用同一连接（不重复建连）；其他线程
        各自独立。``store.conn.execute(...)`` 的既有用法（hybrid 词法分支）
        保持不变。
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open_connection()
            self._local.conn = conn
        return conn

    def _open_connection(self) -> sqlite3.Connection:
        """为当前线程建立连接（WAL + sqlite-vec 扩展加载 + 幂等建表）。"""
        # timeout：多线程写并发时等锁而非立刻报 database is locked
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row

        # [K3] 启用 WAL 日志模式：读写并发互不阻塞（看板/检索与入库并存时
        # 避免「database is locked」），崩溃恢复也更稳。不支持 WAL 的文件系统
        # 回落默认模式，不阻断初始化。
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error as e:
            logger.warning("⚠️ 启用 WAL 日志模式失败（保持默认模式）: %s", e)

        # 尝试加载 sqlite-vec 扩展（扩展是 per-connection 的：每个线程的
        # 新连接都要重新加载，否则该线程拿不到向量检索能力）
        if self._try_load_vec_extension(conn):
            self.has_vector = True

        # 建表（CREATE ... IF NOT EXISTS 幂等；仅当表缺失时才执行 DDL，
        # 后续线程的连接零写入）
        self._ensure_schema(conn)

        return conn

    def _init_db(self):
        """初始化数据库（保持历史语义：构造即建库建表）。

        [只读守卫依赖] ``hybrid.search_with_diagnostics`` / ``ky rag`` 等
        只读入口靠「先判 DEFAULT_DB_PATH.exists()」防止凭空建库 —— 本方法
        必须在构造时就把库文件建出来，该守卫才有意义。
        """
        self.conn  # 触发当前线程的连接建立
        if self.has_vector:
            logger.info("✅ sqlite-vec 扩展加载成功，向量检索已启用")
        else:
            logger.warning("⚠️ sqlite-vec 扩展不可用，将使用纯文本检索降级模式")

    def _try_load_vec_extension(self, conn: sqlite3.Connection) -> bool:
        """尝试在指定连接上加载 sqlite-vec 扩展

        Args:
            conn: 要加载扩展的连接（扩展是 per-connection 的）

        Returns:
            是否成功加载
        """
        try:
            conn.enable_load_extension(True)

            # 尝试多个可能的扩展文件名
            vec_extensions = [
                "vec0",           # Linux/macOS 默认
                "vec0.so",        # Linux
                "vec0.dylib",     # macOS
                "vec0.dll",       # Windows
            ]

            for ext in vec_extensions:
                try:
                    conn.load_extension(ext)
                    # 测试是否真正可用
                    conn.execute("SELECT vec_version()").fetchone()
                    return True
                except Exception:
                    continue

            return False
        except Exception as e:
            logger.debug(f"加载 sqlite-vec 失败: {e}")
            return False

    def _ensure_schema(self, conn: sqlite3.Connection):
        """确保表结构存在（幂等；仅缺失时做 DDL，已有库的线程连接零写入）。"""
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks'"
        ).fetchone()
        if row is None:
            self._create_tables(conn)
            return
        # 边界：库先建、sqlite-vec 后装 —— 旧实现每次构造都会补建向量表，
        # 这里保持同语义（仅当扩展可用且向量表缺失时补一次）
        if self.has_vector:
            vec = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='vec_chunks'"
            ).fetchone()
            if vec is None:
                self._create_tables(conn)

    def _create_tables(self, conn: sqlite3.Connection):
        """在指定连接上创建表结构"""
        cursor = conn.cursor()

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

        conn.commit()

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

        Raises:
            sqlite3.Error: 查询失败。[R4 #207 修复] 此前吞掉异常返回 []，
                让 hybrid 无法区分「0 命中」与「查询坏了」；已知不可用
                （has_vector / numpy 缺失）在函数开头已提前返回，故此处
                异常一律上抛，由上层记入用户可见的降级原因。
        """
        if not self.has_vector:
            logger.warning("向量检索不可用，返回空结果")
            return []

        if not HAS_NUMPY:
            logger.warning("numpy 不可用，无法进行向量检索")
            return []

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
        """关闭当前线程的数据库连接。

        [R4 #207] 连接是线程本地的：本方法只关闭**当前线程**持有的连接，
        其他线程的连接随其线程退出（threading.local 清理）或对象回收自动
        释放。关闭后本线程再次访问会惰性重建连接。
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

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
