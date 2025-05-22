from pydantic import BaseModel
from pathlib import Path


class Config(BaseModel):
    """Plugin Config Here"""
    citation_counter_ignore_user_ids: set[str] = set()
    citation_counter_db_path: Path = Path("./db/citation_count.db")


