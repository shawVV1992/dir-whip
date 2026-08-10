"""workspace-guard plugin for Hermes.

Enforces file discipline by intercepting write_file/patch operations
outside the session directory.
"""

from .guard import register

__all__ = ["register"]
