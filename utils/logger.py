import logging
import sys
from rich.logging import RichHandler
from config.settings import LOG_LEVEL

def setup_logger(name: str):
    logger = logging.getLogger(name)
    logger.setLevel(LOG_LEVEL)
    
    # Check if handler already exists to avoid duplicates
    if not logger.handlers:
        handler = RichHandler(rich_tracebacks=True, show_time=True)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        
    return logger
