"""GoFile command-line downloader.

Downloads are ALWAYS stored under:

    <DOWNLOAD_FOLDER>/<content_id>/

Folder names returned by GoFile are never used as the top-level
download directory.
"""

from __future__ import annotations

import hashlib
import logging
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

        # This is the fixed root. Nothing below this point depends on cwd.
        self.download_path = (
            Path(custom_path).expanduser().resolve()
            if custom_path
            else DEFAULT_DOWNLOAD_PATH.resolve()
        )
        self.download_path.mkdir(parents=True, exist_ok=True)

    def download_item(self, current_task: int, file_info: dict) -> None:
        """Download one file to its absolute, pre-calculated path."""
        filename = file_info["filename"]

        # file_info["download_path"] is always an absolute directory.
        final_path = Path(file_info["download_path"]) / filename
        download_link = file_info["download_link"]

        final_path.parent.mkdir(parents=True, exist_ok=True)

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
                    LOGGER.error(
                        "[FAIL] %s: HTTP %s",
                        filename,
                        response.status_code,
                    )
                    return

                save_file_with_progress(
                    response,
                    final_path,
                    current_task,
                    self.max_workers,
                )

            LOGGER.info("[DONE] %s", filename)

        except requests.RequestException as exc:
            LOGGER.error("[FAIL] %s: %s", filename, exc)
        except OSError as exc:
            LOGGER.error("[FAIL] %s: %s", filename, exc)

    def run_in_parallel(self, files_info: list[dict]) -> None:
        """Execute downloads in parallel without changing the process cwd."""
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(self.download_item, current_task, item_info)
                for current_task, item_info in enumerate(files_info)
            ]

            for future in futures:
                future.result()

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
        base_directory: Path,
        password: str | None = None,
    ) -> None:
        """Resolve a GoFile URL into files using absolute directories.

        base_directory is the directory where this level's files/folders
        belong. It is always inside <DOWNLOAD_FOLDER>/<content_id>.
        """

        def append_file_info(data: dict, target_directory: Path) -> None:
            target_directory.mkdir(parents=True, exist_ok=True)

            files_info.append(
                {
                    "download_path": str(target_directory.resolve()),
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
            # IMPORTANT:
            # Never os.chdir() into GoFile's folder name.
            # Build the path explicitly under the content ID directory.
            folder_directory = base_directory

            # For the root content folder, base_directory is already:
            # Downloads/<content_id>
            #
            # For recursive child folders, the caller passes the child's
            # directory, so its name is also kept below the content ID.
            for child in data["children"].values():
                if child["type"] == "folder":
                    child_directory = folder_directory / child["name"]
                    create_download_directory(child_directory)

                    self.parse_links(
                        child["id"],
                        files_info,
                        child_directory,
                        password,
                    )
                else:
                    append_file_info(child, folder_directory)
        else:
            append_file_info(data, base_directory)

    def initialize_download(self) -> None:
        """Resolve and download all files."""
        content_id = get_content_id(self.url)

        # THE ONLY ROOT DOWNLOAD LOCATION:
        # <DOWNLOAD_FOLDER>/<content_id>/
        content_directory = (
            self.download_path / str(content_id)
        ).resolve()

        create_download_directory(content_directory)

        files_info: list[dict] = []

        hashed_password = (
            hashlib.sha256(self.password.encode()).hexdigest()
            if self.password
            else None
        )

        LOGGER.info("[INFO] URL: %s", self.url)
        LOGGER.info("[INFO] Content ID: %s", content_id)
        LOGGER.info("[INFO] Download directory: %s", content_directory)

        self.parse_links(
            content_id,
            files_info,
            content_directory,
            hashed_password,
        )

        if not content_directory.exists() and not files_info:
            return

        if content_directory.exists() and not any(content_directory.iterdir()) and not files_info:
            try:
                content_directory.rmdir()
            except OSError:
                pass
            return

        LOGGER.info("[INFO] Found %d file(s)", len(files_info))

        self.run_in_parallel(files_info)

        LOGGER.info(
            "[INFO] Download process finished for %s",
            content_id,
        )


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
