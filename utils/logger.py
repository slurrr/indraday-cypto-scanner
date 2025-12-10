import logging
from logging.handlers import RotatingFileHandler
import sys
from rich.logging import RichHandler
from config.settings import LOG_LEVEL

def setup_logger(
    name: str, 
    log_file="utils/scanner.log", 
    level = LOG_LEVEL,
    max_bytes = 1024 * 1024 * 50,
    backup_count = 5,
):

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False  # 🔒 prevent leaking to root logger

    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )

        file_handler = RotatingFileHandler(
            log_file,
            mode="a",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)

        logger.addHandler(file_handler)

    return logger

