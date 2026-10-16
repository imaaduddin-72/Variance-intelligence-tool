"""
logger_setup.py
===============
Provides get_logger(), which returns a logger that writes timestamped,
levelled messages to both the console and a log file.

Inputs:  module name (__name__) from the calling module
Outputs: log lines on the console and in pipeline.log
"""

import logging
import config


_configured = False  # prevents adding duplicate handlers


def get_logger(name):
    """
    Return a logger configured to write to both console and
    pipeline.log. Handlers are attached to the root logger once,
    on the first call.
    """
    global _configured

    root = logging.getLogger()

    if not _configured:
        root.setLevel(logging.INFO)

        # e.g. 2026-06-12 15:30:01 - database - INFO - Connected
        formatter = logging.Formatter(
            "%(asctime)s - %(name)-20s - %(levelname)-8s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

        # Append so history accumulates across runs
        file_handler = logging.FileHandler(
            config.BASE_DIR / "pipeline.log", mode="a", encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

        _configured = True

    return logging.getLogger(name)
