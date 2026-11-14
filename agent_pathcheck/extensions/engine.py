"""Optional worker-pool reuse; every checkout still creates a fresh session."""

from pathlib import Path
import sys
import sysconfig

from pydantic_monty import Monty


class Engine:
    """Own one pool; every run checks out a new session and private evidence."""
    def __init__(self, **pool_options):
        self.pool_options = pool_options
        self.pool = None

    def __enter__(self):
        if self.pool is not None:
            raise RuntimeError("Engine is already open")
        binary = Path(sysconfig.get_path("scripts")) / ("monty.exe" if sys.platform == "win32" else "monty")
        if not binary.is_file():
            raise FileNotFoundError(f"Monty runtime not found: {binary}")
        self.pool = Monty(binary_path=str(binary), **{"max_processes": 1, "request_timeout": 2.0,
                                                     **self.pool_options})
        self.pool.__enter__()
        return self

    def __exit__(self, *args):
        pool, self.pool = self.pool, None
        return pool.__exit__(*args)

    def run(self, code, **kwargs):
        if self.pool is None:
            raise RuntimeError("Use Engine as a context manager")
        from ..runtime import run
        return run(code, engine=self, **kwargs)
