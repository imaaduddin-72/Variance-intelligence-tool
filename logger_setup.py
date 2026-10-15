"""
logger_setup.py
================

PURPOSE: One function, get_logger(), that every module calls to get
         a configured logger. Replaces scattered print() statements
         with proper logging.

WHY THIS MATTERS (print vs logging):

    print() just dumps text to the screen. Once the terminal closes,
    it's gone. If the pipeline runs unattended (Task Scheduler at
    2am on the 1st of the month), nobody sees those prints.

    logging:
    - writes to BOTH the screen AND a file (pipeline.log)
    - timestamps every line automatically
    - has severity LEVELS (INFO, WARNING, ERROR) so you can filter
      "show me only problems from last month" without scrolling
      through every successful run
    - the log file ACCUMULATES across runs - so you have a history
      of every pipeline execution, which is exactly what you need
      before the snapshot table even exists

FINANCIAL ANALOGY:
    print()  = talking out loud during month-end close - useful in
               the moment, but no record afterward.
    logging  = the close checklist that gets filed - dated, signed
               off, and you can pull last month's to compare.

HOW EVERY MODULE WILL USE THIS:

    from logger_setup import get_logger
    log = get_logger(__name__)

    log.info("Loaded 414 rows")           # normal progress
    log.warning("3 categories unmapped")  # not fatal, but notable
    log.error("CSV file missing")         # something failed

    __name__ is the module's own name (e.g. "database") - so every
    log line shows WHICH module wrote it, automatically.
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
