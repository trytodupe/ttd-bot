from pydantic import BaseModel, Field


class Config(BaseModel):
    parser_bili_sub_enabled: bool = True
    parser_bili_sub_interval: int = Field(default=300, ge=1)
    parser_bili_live_interval: int = Field(default=60, ge=1)
