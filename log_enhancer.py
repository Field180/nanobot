"""
日志增强模块 - 结构化日志、动态级别、日志查询
"""
import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from logging.handlers import RotatingFileHandler


class StructuredFormatter(logging.Formatter):
    """结构化日志格式化器"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        
        # 添加额外字段
        if hasattr(record, "extra_data"):
            log_data["extra"] = record.extra_data
        
        # 添加异常信息
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data, ensure_ascii=False)


class LogEnhancer:
    """日志增强器"""
    
    def __init__(self, log_dir: str = "/tmp/nanobot_logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_levels = {}  # logger_name -> level
        self._setup_root_logger()
    
    def _setup_root_logger(self):
        """配置根日志器"""
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        
        # 控制台处理器（普通格式）
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_format = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        console_handler.setFormatter(console_format)
        root_logger.addHandler(console_handler)
        
        # 文件处理器（结构化格式）
        file_handler = RotatingFileHandler(
            self.log_dir / "nanobot.jsonl",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(StructuredFormatter())
        root_logger.addHandler(file_handler)
    
    def set_level(self, logger_name: str, level: str) -> bool:
        """动态设置日志级别"""
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL
        }
        
        if level.upper() not in level_map:
            return False
        
        logger = logging.getLogger(logger_name)
        logger.setLevel(level_map[level.upper()])
        self.log_levels[logger_name] = level.upper()
        return True
    
    def get_level(self, logger_name: str) -> str:
        """获取日志级别"""
        return self.log_levels.get(logger_name, "INFO")
    
    def get_all_levels(self) -> Dict[str, str]:
        """获取所有日志级别"""
        return self.log_levels.copy()
    
    def log_with_extra(self, logger_name: str, level: str, message: str, extra: Dict[str, Any]):
        """带额外数据的日志"""
        logger = logging.getLogger(logger_name)
        record = logger.makeRecord(
            logger_name,
            getattr(logging, level.upper(), logging.INFO),
            "", 0, message, (), None
        )
        record.extra_data = extra
        logger.handle(record)
    
    def read_recent_logs(self, lines: int = 100, level: Optional[str] = None) -> list:
        """读取最近的日志"""
        log_file = self.log_dir / "nanobot.jsonl"
        if not log_file.exists():
            return []
        
        logs = []
        try:
            with open(log_file, "r") as f:
                all_lines = f.readlines()[-lines:]
                for line in all_lines:
                    try:
                        log_entry = json.loads(line.strip())
                        if level is None or log_entry.get("level") == level:
                            logs.append(log_entry)
                    except:
                        continue
        except Exception as e:
            pass
        
        return logs
    
    def get_log_stats(self) -> Dict[str, Any]:
        """获取日志统计"""
        log_file = self.log_dir / "nanobot.jsonl"
        
        stats = {
            "log_file": str(log_file),
            "exists": log_file.exists(),
            "size_bytes": 0,
            "total_lines": 0,
            "level_counts": {},
            "oldest_timestamp": None,
            "newest_timestamp": None
        }
        
        if log_file.exists():
            stats["size_bytes"] = log_file.stat().st_size
            
            try:
                with open(log_file, "r") as f:
                    lines = f.readlines()
                    stats["total_lines"] = len(lines)
                    
                    for line in lines:
                        try:
                            entry = json.loads(line.strip())
                            level = entry.get("level", "UNKNOWN")
                            stats["level_counts"][level] = stats["level_counts"].get(level, 0) + 1
                            
                            ts = entry.get("timestamp")
                            if ts:
                                if stats["oldest_timestamp"] is None:
                                    stats["oldest_timestamp"] = ts
                                stats["newest_timestamp"] = ts
                        except:
                            continue
            except:
                pass
        
        return stats
    
    def clear_logs(self) -> bool:
        """清除日志文件"""
        log_file = self.log_dir / "nanobot.jsonl"
        try:
            if log_file.exists():
                log_file.unlink()
            return True
        except:
            return False


# 全局日志增强器实例
_log_enhancer = None


def get_log_enhancer() -> LogEnhancer:
    """获取全局日志增强器"""
    global _log_enhancer
    if _log_enhancer is None:
        _log_enhancer = LogEnhancer()
    return _log_enhancer
