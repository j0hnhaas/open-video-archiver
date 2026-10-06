"""Shared application constants for Open Video Archiver."""

from pathlib import Path

APP_NAME = "Open Video Archiver"
APP_VERSION = "1.0"
REPO_URL = "https://github.com/j0hnhaas/open-video-archiver"
COPYRIGHT = "(c) 2026, John G. Haas"
POSITIONING = "Preserve online video. Document the source. Verify the integrity."
DEFAULT_ARCHIVE_ROOT = Path.home() / "Downloads" / "Open-Video-Archives"
RIGHTS_STATEMENT = (
    "Ich bestätige, dass ich berechtigt bin, dieses Video und die zugehörigen "
    "Metadaten/Untertitel für den vorgesehenen Zweck herunterzuladen und zu archivieren."
)
