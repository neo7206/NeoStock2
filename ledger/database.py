"""
NeoStock2 帳本 — SQLite 資料庫管理

負責：
- SQLite 連線管理
- 資料表建立
- Session 管理
"""

import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from ledger.models import Base

logger = logging.getLogger("neostock2.ledger.database")


class Database:
    """SQLite 資料庫管理器"""

    def __init__(self, db_path: str = "data/neostock2.db"):
        self.db_path = db_path
        self._engine = None
        self._session_factory = None
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
        )
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)
        logger.info(f"資料庫已初始化: {self.db_path}")

    def get_session(self) -> Session:
        """取得新的資料庫 Session"""
        return self._session_factory()

    @property
    def engine(self):
        return self._engine
