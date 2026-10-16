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


_configured = False  # guard so we don't add duplicate handlers


def get_logger(name):
    """
    Return a logger configured to write to both console and
    pipeline.log.

    The `_configured` guard matters because get_logger() is called
    once per module (6+ times per pipeline run). Without the guard,
    each call would add ANOTHER file handler, and log lines would
    print 2x, 3x, 6x as more modules import this.
    """
    global _configured

    root = logging.getLogger()  # the root logger - shared by all modules

    if not _configured:
        root.setLevel(logging.INFO)

        # Format: timestamp - module name - level - message
        # Example: 2026-06-12 15:30:01 - database - INFO - Connected
        formatter = logging.Formatter(
            "%(asctime)s - %(name)-20s - %(levelname)-8s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # Handler 1: console (same visibility as print() had)
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

        # Handler 2: file (the persistent record print() never had)
        # mode="a" = append, so history accumulates across runs
        file_handler = logging.FileHandler(
            config.BASE_DIR / "pipeline.log", mode="a", encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

        _configured = True

    return logging.getLogger(name)
