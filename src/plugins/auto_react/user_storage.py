import json
from pathlib import Path
from typing import Set

class UserStorage:
    """简单的用户ID存储装置"""
    
    def __init__(self, file_path: Path):
        """
        初始化存储装置
        
        Args:
            file_path: 数据文件路径
        """
        self.file_path = file_path
        self._users: Set[str] = set()
        self._load()
    
    def _load(self) -> None:
        """从文件读取用户ID列表"""
        if self.file_path.exists():
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._users = set(data) if isinstance(data, list) else set()
            except (json.JSONDecodeError, IOError):
                self._users = set()
        else:
            self._users = set()
    
    def _save(self) -> None:
        """将用户ID列表保存到文件"""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(sorted(list(self._users)), f, ensure_ascii=False, indent=2)
    
    def add_user(self, user_id: str) -> bool:
        """
        添加用户ID
        
        Args:
            user_id: 用户ID
            
        Returns:
            是否添加成功（新增的情况）
        """
        if user_id not in self._users:
            self._users.add(user_id)
            self._save()
            return True
        return False
    
    def remove_user(self, user_id: str) -> bool:
        """
        删除用户ID
        
        Args:
            user_id: 用户ID
            
        Returns:
            是否删除成功
        """
        if user_id in self._users:
            self._users.remove(user_id)
            self._save()
            return True
        return False
    
    def has_user(self, user_id: str) -> bool:
        """
        检查用户ID是否存在
        
        Args:
            user_id: 用户ID
            
        Returns:
            用户是否存在
        """
        return user_id in self._users
    
    def get_all_users(self) -> Set[str]:
        """
        获取所有用户ID
        
        Returns:
            用户ID集合
        """
        return self._users.copy()
    
    def clear_all(self) -> None:
        """清空所有用户ID"""
        self._users.clear()
        self._save()
