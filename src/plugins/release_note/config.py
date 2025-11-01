from pydantic import BaseModel
from nonebot import get_plugin_config


class Config(BaseModel):
    """Plugin configuration"""
    version: str = ""
    github_token: str = ""
    
    # GitHub repository configuration
    github_repo_owner: str = "trytodupe"
    github_repo_name: str = "ttd-bot"
    
    # Tag names
    last_deployed_tag: str = "last-deployed"
    
    # NapCat API endpoint
    napcat_api_base: str = ""
    
    # Release note formatting
    max_commits_display: int = 10
    max_message_length: int = 60


plugin_config = get_plugin_config(Config)
