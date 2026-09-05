"""Main entry point for the GoFile downloader.

Reads URLs from URLs.txt and downloads them using plain CLI output.
No GUI, Rich Live, terminal clearing, or interactive rendering is used.
"""

from __future__ import annotations

import logging
import sys
from argparse import Namespace
from pathlib import Path

from downloader import handle_download_process
from src.config import SESSION_LOG, URLS_FILE, parse_arguments
from src.file_utils import read_file, write_file

LOGGER = logging.getLogger(__name__)

URLS_FILE_PATH = Path.cwd() / URLS_FILE
SESSION_FILE_PATH = Path.cwd() / SESSION_LOG


def process_urls(urls: list[str], args: Namespace | None = None) -> None:
    """Download each URL sequentially."""
    for index, url in enumerate(urls, start=1):
        LOGGER.info("=" * 60)
        LOGGER.info("[URL %d/%d] %s", index, len(urls), url)
        LOGGER.info("=" * 60)
        handle_download_process(url, args=args)


def main() -> None:
    """Run the multi-URL downloader."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    write_file(SESSION_FILE_PATH)

    args = parse_arguments(common_only=True)
    urls = [url.strip() for url in read_file(URLS_FILE_PATH) if url.strip()]

    if not urls:
        LOGGER.warning("[INFO] No URLs found in %s", URLS_FILE_PATH)
        return

    try:
        process_urls(urls, args)
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        sys.exit(130)
    finally:
        write_file(URLS_FILE_PATH)


if __name__ == "__main__":
    main()
