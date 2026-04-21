from pydantic import BaseModel


class Config(BaseModel):
    etx_osu_client_id: int | str = ""
    etx_osu_client_secret: str = ""
