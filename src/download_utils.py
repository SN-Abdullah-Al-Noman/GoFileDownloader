"""Utilities for downloading files with plain CLI progress output."""

from __future__ import annotations

import logging
from pathlib import Path

from requests import Response

from .config import LARGE_FILE_CHUNK_SIZE, THRESHOLDS

LOGGER = logging.getLogger(__name__)


def get_chunk_size(file_size: int) -> int:
    """Determine an appropriate chunk size."""
    for threshold, chunk_size in THRESHOLDS:
        if file_size >= 0 and file_size < threshold:
            return chunk_size
    return LARGE_FILE_CHUNK_SIZE


def _format_size(size: int) -> str:
    """Format bytes as a human-readable size."""
    if size < 1024:
        return f"{size} B"
    if size < 1024**2:
        return f"{size / 1024:.1f} KB"
    if size < 1024**3:
        return f"{size / 1024**2:.1f} MB"
    return f"{size / 1024**3:.2f} GB"


def save_file_with_progress(
    response: Response,
    download_path: str,
    task: int = 0,
    worker_count: int = 1,
) -> None:
    """Save a response to disk and print CI-friendly progress messages.

    No carriage-return/live rendering is used. Every progress update is a
    normal log line, so GitHub Actions can display it correctly.
    """
    file_size = int(response.headers.get("Content-Length", -1))
    chunk_size = get_chunk_size(file_size)
    total_downloaded = 0
    last_percent = -1

    with Path(download_path).open("wb") as file:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue

            file.write(chunk)
            total_downloaded += len(chunk)

            if file_size > 0:
                percent = int((total_downloaded * 100) / file_size)
                # Avoid flooding GitHub Actions logs.
                if percent >= last_percent + 10 or percent == 100:
                    last_percent = percent
                    LOGGER.info(
                        "[PROGRESS %d] %d%% (%s / %s)",
                        task + 1,
                        percent,
                        _format_size(total_downloaded),
                        _format_size(file_size),
                    )
            elif total_downloaded and total_downloaded % (10 * 1024 * 1024) < chunk_size:
                LOGGER.info(
                    "[PROGRESS %d] %s downloaded",
                    task + 1,
                    _format_size(total_downloaded),
                )

    if file_size > 0 and total_downloaded != file_size:
        LOGGER.warning(
            "[WARN] Download size mismatch: %s / %s",
            _format_size(total_downloaded),
            _format_size(file_size),
        )
