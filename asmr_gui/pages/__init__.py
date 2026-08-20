"""Four-page GUI, one module per page."""

from __future__ import annotations

from .downloads_page import DownloadsPage
from .player_page import PlayerPage
from .settings_page import SettingsPage
from .tasks_page import TasksPage

__all__ = ["DownloadsPage", "PlayerPage", "SettingsPage", "TasksPage"]
