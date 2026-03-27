from pathlib import Path

from pydantic import BaseModel


class Config(BaseModel):
    coc_checker_group_id: int = 607572668
    coc_checker_interval_seconds: int = 1800
    coc_checker_timeout_seconds: int = 120
    coc_checker_shared_dir: Path = Path("/shared")
