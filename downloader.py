"""GoFile command-line downloader.

This module intentionally contains no GUI/live-terminal UI so it works in:
- Termux
- Linux
- GitHub Actions
- Docker/CI
- SSH/headless environments
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from src.config import (
    DEFAULT_USAGE,
    DOWNLOAD_FOLDER,
    EXTENDED_HEADERS,
    LOCALE,
    MAX_WORKERS,
    PASSWORD_USAGE,
    parse_arguments,
)
from src.download_utils import save_file_with_progress
from src.file_utils import create_download_directory
from src.gofile_utils import (
    check_response_status,
    generate_content_url,
    generate_website_token,
    get_account_token,
    get_content_id,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_DOWNLOAD_PATH = Path.cwd() / DOWNLOAD_FOLDER


class Downloader:
    """Download GoFile content using plain CLI output only."""

    def __init__(self, url: str, args: Namespace | None = None) -> None:
        self.url = url
        self.password = getattr(args, "password", None) if args else None
        self.max_workers = MAX_WORKERS
        self.token = get_account_token()

        custom_path = getattr(args, "custom_path", None) if args else None
        self.download_path = (
            Path(custom_path) if custom_path else DEFAULT_DOWNLOAD_PATH
        )
        self.download_path.mkdir(parents=True, exist_ok=True)
        os.chdir(self.download_path)

    def download_item(self, current_task: int, file_info: dict) -> None:
        """Download one file and report progress using normal log lines."""
        filename = file_info["filename"]
        final_path = Path(file_info["download_path"]) / filename
        download_link = file_info["download_link"]

        if final_path.exists() and final_path.stat().st_size > 0:
            LOGGER.info("[SKIP] %s (already exists)", filename)
            return

        LOGGER.info("[START %d] %s", current_task + 1, filename)

        try:
            headers = self._prepare_headers(url=download_link)

            with requests.get(
                download_link,
                headers=headers,
                stream=True,
                timeout=(10, 30),
            ) as response:
                if not check_response_status(response, filename):
                    LOGGER.error("[FAIL] %s: HTTP %s", filename, response.status_code)
                    return

                save_file_with_progress(response, final_path, current_task, self.max_workers)

            LOGGER.info("[DONE] %s", filename)

        except requests.RequestException as exc:
            LOGGER.error("[FAIL] %s: %s", filename, exc)
        except OSError as exc:
            LOGGER.error("[FAIL] %s: %s", filename, exc)

    def run_in_parallel(self, content_directory: str, files_info: list[dict]) -> None:
        """Execute downloads in parallel."""
        previous_cwd = Path.cwd()
        os.chdir(content_directory)

        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = [
                    executor.submit(self.download_item, current_task, item_info)
                    for current_task, item_info in enumerate(files_info)
                ]
                # Retrieve exceptions raised by worker threads.
                for future in futures:
                    future.result()
        finally:
            os.chdir(previous_cwd)

    def _prepare_headers(
        self,
        url: str | None = None,
        *,
        include_auth: bool = False,
    ) -> dict:
        """Prepare HTTP headers."""
        headers = EXTENDED_HEADERS.copy()

        if include_auth:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-Website-Token"] = generate_website_token(self.token)
            headers["X-BL"] = LOCALE
        else:
            if url:
                adjusted_url = url + ("/" if not url.endswith("/") else "")
                headers["Referer"] = adjusted_url
                headers["Origin"] = url
            headers["Cookie"] = f"accountToken={self.token}"

        return headers

    def parse_links(
        self,
        identifier: str,
        files_info: list[dict],
        password: str | None = None,
    ) -> None:
        """Resolve a GoFile URL into downloadable file entries."""

        def append_file_info(data: dict) -> None:
            files_info.append(
                {
                    "download_path": str(Path.cwd()),
                    "filename": data["name"],
                    "download_link": data["link"],
                }
            )

        content_url = generate_content_url(identifier, password=password)
        headers = self._prepare_headers(include_auth=True)

        try:
            response = requests.get(
                content_url,
                headers=headers,
                timeout=10,
            ).json()
        except (requests.RequestException, ValueError) as exc:
            LOGGER.error("[FAIL] Request error: %s", exc)
            return

        if response.get("status") != "ok":
            LOGGER.error("[FAIL] Failed to get file information from GoFile.")
            return

        data = response["data"]

        password_exists = "password" in data
        password_ok = data.get("passwordStatus") == "passwordOk"
        if password_exists and not password_ok:
            LOGGER.error(
                "[FAIL] This URL requires a valid password. "
                "Provide it as the second argument."
            )
            return

        if data["type"] == "folder":
            create_download_directory(data["name"])
            os.chdir(data["name"])
            try:
                for child in data["children"].values():
                    if child["type"] == "folder":
                        self.parse_links(child["id"], files_info, password)
                    else:
                        append_file_info(child)
            finally:
                os.chdir(os.path.pardir)
        else:
            append_file_info(data)

    def initialize_download(self) -> None:
        """Resolve and download all files."""
        content_id = get_content_id(self.url)
        content_directory = self.download_path / content_id
        create_download_directory(content_directory)

        files_info: list[dict] = []
        hashed_password = (
            hashlib.sha256(self.password.encode()).hexdigest()
            if self.password
            else None
        )

        LOGGER.info("[INFO] URL: %s", self.url)
        LOGGER.info("[INFO] Content ID: %s", content_id)

        self.parse_links(content_id, files_info, hashed_password)

        if not os.listdir(content_directory) and not files_info:
            try:
                content_directory.rmdir()
            except OSError:
                pass
            return

        LOGGER.info("[INFO] Found %d file(s)", len(files_info))
        self.run_in_parallel(str(content_directory), files_info)
        LOGGER.info("[INFO] Download process finished for %s", content_id)


def handle_download_process(
    url: str,
    args: Namespace | None = None,
) -> None:
    """Handle one URL."""
    if not url:
        LOGGER.error(
            "Usage: %s\nPassword usage: %s",
            DEFAULT_USAGE,
            PASSWORD_USAGE,
        )
        raise SystemExit(1)

    Downloader(url=url, args=args).initialize_download()


def main() -> None:
    """CLI entry point for a single GoFile URL."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_arguments()
    try:
        handle_download_process(args.url, args=args)
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        sys.exit(130)


if __name__ == "__main__":
    main()
