import logging
import sys
from rich.logging import RichHandler
from config.settings import LOG_LEVEL

def setup_logger(name: str, log_file="utils/scanner.log", level=LOG_LEVEL):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False  # 🔒 prevent leaking to root logger

    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )

        file_handler = logging.FileHandler(log_file, mode="a")
        file_handler.setFormatter(formatter)

        logger.addHandler(file_handler)

    return logger

