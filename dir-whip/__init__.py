"""dir-whip plugin for Hermes.

Enforces file discipline by intercepting write_file/patch operations
outside the session directory.
"""

from .dir_whip import register

__all__ = ["register"]
