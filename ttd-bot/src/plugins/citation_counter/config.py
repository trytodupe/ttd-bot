from pydantic import BaseModel


class Config(BaseModel):
    """Plugin Config Here"""
    citation_counter_ignore_user_ids: set[str] = set()


