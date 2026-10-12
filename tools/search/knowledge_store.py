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

#: [P0 修复·2026-10-11 第五轮深审] 向量维度默认值（单一真源，与
#: ``vector.py`` 的 ``VectorEncoder.embedding_dim`` 默认保持一致；
#: tests/regression/test_search_pipeline_fixes.py 有常量一致性钉子）。
#: bge-small-zh-v1.5 的 hidden_size 实测 = **512**（官方 config.json；
#: 旧实现两处硬编码 384 是 bge-small-**en** 的维度——中文版从未对过：
#: 384 维表 + 512 维向量 = sqlite-vec 插入报错 → 整批回滚）。
DEFAULT_EMBEDDING_DIM = 512


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

    def __init__(self, db_path: Path | str | None = None,
                 embedding_dim: int = DEFAULT_EMBEDDING_DIM):
        """初始化知识库

        Args:
            db_path: 数据库路径，默认为 data/knowledge/embeddings.db
            embedding_dim: 向量维度（建表 FLOAT[dim] 用）。默认 512（bge-small-zh）；
                换模型时显式传入，维度不符的向量在写入时只跳过向量行（不回滚文本）。
        """
        if db_path is None:
            db_path = DEFAULT_DB_PATH

        self.db_path = Path(db_path)
        self.embedding_dim = int(embedding_dim)
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

        [P0 修复·2026-10-11 第五轮深审] 优先走 ``sqlite_vec`` 包自带加载 API：
        旧实现只试裸文件名（``load_extension("vec0")`` 等），依赖动态库在系统
        搜索路径里——pip 装好 ``sqlite-vec`` 后其 .so/.dll 在包目录内，裸名
        加载必然失败（实测 has_vector 恒 False，向量检索"名义功能"）。
        ``sqlite_vec.load(conn)`` 自动定位包内动态库；包不可用时再回落裸名
        （兼容手工把扩展放进系统路径的部署）。
        """
        try:
            conn.enable_load_extension(True)
        except Exception as e:                     # pragma: no cover - 平台差异
            logger.debug(f"启用扩展加载失败: {e}")
            return False


        # 1) sqlite_vec 包 API（首选）
        try:
            import sqlite_vec  # type: ignore
            sqlite_vec.load(conn)
            conn.execute("SELECT vec_version()").fetchone()
            return True
        except Exception as e:
            logger.debug(f"sqlite_vec.load 不可用: {e}")

        # 2) 裸文件名回落（手工部署/系统路径场景）
        try:
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
            # [P0 修复·2026-10-11] 维度参数化：旧实现硬编码 FLOAT[384]
            # （bge-small-en 维度），而 bge-small-zh 实际 512 —— 修好加载/
            # tokenizer/输入后生成 512 维向量，写入 384 维表即报错，且
            # add_chunks 单事务整批回滚会让文本也写不进（比修前更糟）。
            try:
                cursor.execute(f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks
                    USING vec0(
                        chunk_id TEXT PRIMARY KEY,
                        embedding FLOAT[{int(self.embedding_dim)}]
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
            # [P0 修复·2026-10-11 第五轮深审] 维度不符只跳向量、不拖垮文本：
            # 旧实现在此直接 INSERT，512 维向量撞 384 维表 → 异常 → 整个
            # add_chunk 回滚（连主表文本一起丢）。现先做维度前置校验，
            # 向量写入再套一层独立 try（向量失败不牵连已写好的文本行）。
            if self.has_vector and chunk.embedding:
                if not HAS_NUMPY:
                    logger.warning("numpy 不可用，无法存储向量")
                elif len(chunk.embedding) != self.embedding_dim:
                    logger.warning(
                        "向量维度 %d 与库表 %d 不符，跳过该片段向量（文本仍入库）: %s",
                        len(chunk.embedding), self.embedding_dim, chunk.id)
                else:
                    try:
                        # sqlite-vec 期望的格式
                        embedding_blob = np.array(chunk.embedding, dtype=np.float32).tobytes()
                        cursor.execute("""
                            INSERT OR REPLACE INTO vec_chunks (chunk_id, embedding)
                            VALUES (?, ?)
                        """, (chunk.id, embedding_blob))
                    except Exception as e:
                        logger.warning("向量写入失败，仅跳过向量行（文本仍入库）: %s", e)

            self.conn.commit()
            return True

        except Exception as e:
            logger.error(f"添加文本片段失败: {e}")
            self.conn.rollback()
            return False

    def add_chunks(self, chunks: List[Chunk]) -> int:
        """批量添加文本片段（单事务 + executemany）。

        [P2 修复·2026-10-08] 旧实现是 ``for chunk in chunks: self.add_chunk(chunk)``
        —— 每条片段一次 INSERT + 一次 commit（N+1 次事务提交）。全量建索引
        （院校库 + 参考资料数百片段）时每条都要 fsync，是纯粹的写入放大。
        现改为单事务批量写入：

        * 主表 ``executemany``；向量行只对带 embedding 的片段批量写；
        * 全部成功 → 一次 commit，返回写入条数；
        * 任一条失败 → 整批 rollback，返回 0（调用方据此如实报告成功数，
          不做「部分成功」的模糊语义）。

        Args:
            chunks: 文本片段列表

        Returns:
            成功写入的片段数（0 = 整批失败/空输入）
        """
        if not chunks:
            return 0
        try:
            cursor = self.conn.cursor()
            cursor.executemany("""
                INSERT OR REPLACE INTO chunks (id, text, source, metadata)
                VALUES (?, ?, ?, ?)
            """, [(c.id, c.text, c.source,
                   json.dumps(c.metadata, ensure_ascii=False)) for c in chunks])

            if self.has_vector:
                self._write_vectors_batch(cursor, chunks)

            self.conn.commit()
            return len(chunks)

        except Exception as e:
            logger.error(f"批量添加文本片段失败（整批回滚）: {e}")
            self.conn.rollback()
            return 0

    def _write_vectors_batch(self, cursor: sqlite3.Cursor, chunks: List[Chunk]) -> None:
        """在 add_chunks 的同一事务内批量写入向量行。

        [P0 修复·2026-10-11] 维度前置校验 + 向量写入独立 try：维度不符的
        行跳过（记 warning），向量写入异常也不整批回滚文本 —— 旧实现让一个
        维度不符的向量把整批文本陪葬。
        """
        if not HAS_NUMPY:
            logger.warning("numpy 不可用，无法存储向量（仅写入文本索引）")
            return
        vec_rows = []
        for c in chunks:
            if not c.embedding:
                continue
            if len(c.embedding) != self.embedding_dim:
                logger.warning(
                    "向量维度 %d 与库表 %d 不符，跳过该片段向量（文本仍入库）: %s",
                    len(c.embedding), self.embedding_dim, c.id)
                continue

            vec_rows.append(
                (c.id, np.array(c.embedding, dtype=np.float32).tobytes()))
        if vec_rows:
            try:
                cursor.executemany("""
                    INSERT OR REPLACE INTO vec_chunks (chunk_id, embedding)
                    VALUES (?, ?)
                """, vec_rows)
            except Exception as e:
                logger.warning("向量批量写入失败，仅跳过向量行（文本仍入库）: %s", e)

    def prune_chunks(self, keep_ids, source_prefixes) -> int:
        """删除「source 属于指定前缀、且 id 不在 keep_ids 中」的片段。

        [P2 修复·2026-10-08] 建索引只做 INSERT OR REPLACE：源文件被删除、或
        文件变短（切片数减少）后，旧片段永远留在库里 —— ``ky rag`` 会命中
        已删除资料的内容。本方法给「增量建索引」补上收尾清理：

        * 只清理 ``source_prefixes`` 圈定的来源（调用方只传自己的来源前缀，
          不碰其它写入方的数据）；
        * 分隔符归一后再比对（Windows 下 ``relative_to`` 产出反斜杠）；
        * ``keep_ids`` 为空集即「该来源已整体消失」。

        Args:
            keep_ids: 本次重建仍有效的片段 id 集合
            source_prefixes: 受管理的来源前缀（如 ``universities/``）

        Returns:
            清理掉的片段数
        """
        keep = {str(x) for x in (keep_ids or ())}
        prefixes = [str(p).replace("\\", "/") for p in (source_prefixes or ()) if str(p)]
        if not prefixes:
            return 0
        try:
            stale = []
            for row in self.conn.execute("SELECT id, source FROM chunks").fetchall():
                src = str(row["source"] or "").replace("\\", "/")
                if any(src.startswith(p) for p in prefixes) and row["id"] not in keep:
                    stale.append(row["id"])
            if not stale:
                return 0
            self.conn.executemany("DELETE FROM chunks WHERE id = ?",
                                  [(chunk_id,) for chunk_id in stale])
            if self.has_vector:
                try:
                    self.conn.executemany("DELETE FROM vec_chunks WHERE chunk_id = ?",
                                          [(chunk_id,) for chunk_id in stale])
                except sqlite3.Error as e:
                    # 向量表清理失败不影响文本索引的正确性（检索侧按 chunk_id
                    # JOIN 回主表，孤儿向量行取不到正文会被丢弃）；留痕即可。
                    logger.warning(f"向量表失效片段清理失败（文本索引已清理）: {e}")
            self.conn.commit()
            return len(stale)
        except Exception as e:
            logger.error(f"清理失效片段失败: {e}")
            self.conn.rollback()
            return 0

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
