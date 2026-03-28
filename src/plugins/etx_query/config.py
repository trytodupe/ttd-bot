from pydantic import BaseModel


class Config(BaseModel):
    etx_osu_client_id: str = ""
    etx_osu_client_secret: str = ""
