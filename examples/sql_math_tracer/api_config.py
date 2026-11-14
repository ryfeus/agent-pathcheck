"""Compatibility imports for shared example API configuration."""

from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.agent_support import DEFAULT_ENV_FILE, api_client, api_error, read_api_key

__all__ = ["DEFAULT_ENV_FILE", "api_client", "api_error", "read_api_key"]
