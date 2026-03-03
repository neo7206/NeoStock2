import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, scoped_session
from sqlalchemy.pool import StaticPool

from ledger.models import Base

logger = logging.getLogger("neostock2.ledger.database")


class Database:
    """SQLite 資料庫管理器"""

    def __init__(self, db_path: str = "data/neostock2.db"):
        self.db_path = db_path
        self._engine = None
        self._session_factory = None
        self._scoped_session = None
        self._ensure_dir()
        self._init_db()

    def _ensure_dir(self):
        """確保資料庫目錄存在"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def _init_db(self):
        """初始化資料庫連線與資料表"""
        self._engine = create_engine(
            f"sqlite:///{self.db_path}",
            echo=False,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,  # SQLite 單連線池，避免多線程鎖定
        )
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)
        self._scoped_session = scoped_session(self._session_factory)
        self._run_migrations()
        logger.info(f"資料庫已初始化: {self.db_path}")

    def _run_migrations(self):
        """執行資料庫遷移（新增欄位等）"""
        from sqlalchemy import text
        with self._engine.connect() as conn:
            # 檢查 trades 表是否有 realized_pnl 欄位
            result = conn.execute(text("PRAGMA table_info(trades)"))
            columns = [row[1] for row in result]
            if "realized_pnl" not in columns:
                conn.execute(text("ALTER TABLE trades ADD COLUMN realized_pnl FLOAT DEFAULT NULL"))
                conn.commit()
                logger.info("DB 遷移: trades 表已新增 realized_pnl 欄位")

    def get_session(self) -> Session:
        """取得線程安全的資料庫 Session（使用 scoped_session）"""
        return self._scoped_session()

    @property
    def engine(self):
        return self._engine
