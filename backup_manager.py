"""
数据备份管理器 - 会话备份、配置备份、定时备份
"""
import json
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import zipfile
import threading

logger = logging.getLogger(__name__)


class BackupManager:
    """备份管理器"""
    
    def __init__(
        self,
        backup_dir: str = "/tmp/nanobot_backups",
        session_dir: str = "/tmp/nanobot_sessions",
        max_backups: int = 10
    ):
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir = Path(session_dir)
        self.max_backups = max_backups
        self._lock = threading.Lock()
    
    def create_backup(self, backup_type: str = "full", description: str = "") -> Dict[str, Any]:
        """创建备份"""
        with self._lock:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"backup_{backup_type}_{timestamp}"
            backup_path = self.backup_dir / f"{backup_name}.zip"
            
            try:
                with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    # 备份会话数据
                    if self.session_dir.exists():
                        for session_file in self.session_dir.glob("web_ui_*.json"):
                            zf.write(session_file, f"sessions/{session_file.name}")
                    
                    # 备份配置文件
                    config_path = Path.home() / "nanobot_v2" / "config.yaml"
                    if config_path.exists():
                        zf.write(config_path, "config.yaml")
                    
                    # 写入备份元数据
                    metadata = {
                        "backup_type": backup_type,
                        "created_at": datetime.now().isoformat(),
                        "description": description,
                        "files_count": len(zf.namelist())
                    }
                    zf.writestr("metadata.json", json.dumps(metadata, indent=2))
                
                # 清理旧备份
                self._cleanup_old_backups()
                
                logger.info(f"备份创建成功: {backup_path}")
                return {
                    "success": True,
                    "backup_name": backup_name,
                    "backup_path": str(backup_path),
                    "size_bytes": backup_path.stat().st_size,
                    "created_at": metadata["created_at"]
                }
                
            except Exception as e:
                logger.error(f"备份创建失败: {e}")
                return {"success": False, "error": str(e)}
    
    def restore_backup(self, backup_name: str) -> Dict[str, Any]:
        """恢复备份"""
        with self._lock:
            backup_path = self.backup_dir / f"{backup_name}.zip"
            
            if not backup_path.exists():
                return {"success": False, "error": "备份文件不存在"}
            
            try:
                # 先创建当前状态的备份
                self.create_backup("pre_restore", "恢复前自动备份")
                
                with zipfile.ZipFile(backup_path, 'r') as zf:
                    # 恢复会话数据
                    for name in zf.namelist():
                        if name.startswith("sessions/"):
                            zf.extract(name, self.session_dir.parent)
                        elif name == "config.yaml":
                            config_path = Path.home() / "nanobot_v2" / "config.yaml"
                            zf.extract(name, config_path.parent)
                
                logger.info(f"备份恢复成功: {backup_name}")
                return {"success": True, "restored_from": backup_name}
                
            except Exception as e:
                logger.error(f"备份恢复失败: {e}")
                return {"success": False, "error": str(e)}
    
    def list_backups(self) -> List[Dict[str, Any]]:
        """列出所有备份"""
        backups = []
        
        for backup_file in sorted(self.backup_dir.glob("backup_*.zip"), reverse=True):
            try:
                with zipfile.ZipFile(backup_file, 'r') as zf:
                    if "metadata.json" in zf.namelist():
                        metadata = json.loads(zf.read("metadata.json").decode())
                    else:
                        metadata = {}
                
                backups.append({
                    "name": backup_file.stem,
                    "file": backup_file.name,
                    "size_bytes": backup_file.stat().st_size,
                    "created_at": metadata.get("created_at", ""),
                    "backup_type": metadata.get("backup_type", "unknown"),
                    "description": metadata.get("description", ""),
                    "files_count": metadata.get("files_count", 0)
                })
            except:
                continue
        
        return backups
    
    def delete_backup(self, backup_name: str) -> Dict[str, Any]:
        """删除备份"""
        backup_path = self.backup_dir / f"{backup_name}.zip"
        
        if not backup_path.exists():
            return {"success": False, "error": "备份文件不存在"}
        
        try:
            backup_path.unlink()
            logger.info(f"备份已删除: {backup_name}")
            return {"success": True, "deleted": backup_name}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _cleanup_old_backups(self):
        """清理旧备份"""
        backups = sorted(self.backup_dir.glob("backup_*.zip"))
        
        while len(backups) > self.max_backups:
            oldest = backups.pop(0)
            oldest.unlink()
            logger.info(f"清理旧备份: {oldest.name}")
    
    def get_backup_stats(self) -> Dict[str, Any]:
        """获取备份统计"""
        backups = self.list_backups()
        total_size = sum(b["size_bytes"] for b in backups)
        
        return {
            "backup_dir": str(self.backup_dir),
            "total_backups": len(backups),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "max_backups": self.max_backups,
            "oldest_backup": backups[-1]["name"] if backups else None,
            "newest_backup": backups[0]["name"] if backups else None
        }
    
    def schedule_backup(self, interval_hours: int = 24) -> Dict[str, Any]:
        """设置定时备份（返回配置信息，实际调度由外部实现）"""
        return {
            "success": True,
            "interval_hours": interval_hours,
            "message": f"建议使用系统cron或外部调度器，每{interval_hours}小时调用 /api/backup/create"
        }


# 全局备份管理器实例
_backup_manager = None


def get_backup_manager() -> BackupManager:
    """获取全局备份管理器"""
    global _backup_manager
    if _backup_manager is None:
        _backup_manager = BackupManager()
    return _backup_manager
