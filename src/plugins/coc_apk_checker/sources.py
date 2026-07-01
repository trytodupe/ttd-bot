"""APK download sources for the coc_apk_checker plugin.

Each source provides two async methods:
- get_latest_version(client) -> CocVersion | None
- download_apk(client, shared_dir) -> DownloadedApk

Add new sources by creating a class with SOURCE_NAME and these two methods,
then register it in _SOURCE_REGISTRY.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, unquote_plus

import httpx

# ---------------------------------------------------------------------------
# shared dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CocVersion:
    version_name: str
    version_code: str
    update_date: str

    @property
    def version_code_int(self) -> int:
        try:
            return int(self.version_code)
        except (TypeError, ValueError):
            return -1


@dataclass(frozen=True)
class DownloadedApk:
    filename: str
    path: Path


@dataclass(frozen=True)
class UploadResult:
    ok: bool
    detail: str


# ---------------------------------------------------------------------------
# shared utilities
# ---------------------------------------------------------------------------

_ZIP_FILE_SIGNATURE = b"PK\x03\x04"


def _looks_like_zip_archive(header_bytes: bytes) -> bool:
    return header_bytes.startswith(_ZIP_FILE_SIGNATURE)


# ---------------------------------------------------------------------------
# ApkComboSource
# ---------------------------------------------------------------------------


class ApkComboSource:
    SOURCE_NAME = "apkcombo"

    _APP_PAGE_URL = "https://apkcombo.com/clash-of-clans/com.supercell.clashofclans/"
    _DOWNLOAD_PAGE_URL = f"{_APP_PAGE_URL}download/apk"
    _COMMON_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://apkcombo.com/",
    }
    _JSON_LD_RE = re.compile(
        r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL
    )
    _R2_APK_LINK_RE = re.compile(r'href="(/r2\?u=[^"]*\.apk%3F[^"]*)"')
    _R2_XAPK_LINK_RE = re.compile(r'href="(/r2\?u=[^"]*\.apks%3F[^"]*)"')
    _VERSION_CODE_RE = re.compile(r"/(\d+)\.\w+\.apks?%3F")

    async def get_latest_version(
        self, client: httpx.AsyncClient
    ) -> CocVersion | None:
        # 1. Fetch main page for JSON-LD version info
        response = await client.get(
            self._APP_PAGE_URL, headers=self._COMMON_HEADERS
        )
        response.raise_for_status()
        html = response.text

        version_name: str | None = None
        update_date: str | None = None

        for match in self._JSON_LD_RE.finditer(html):
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if data.get("@type") == "MobileApplication" and "softwareVersion" in data:
                version_name = str(data.get("softwareVersion", "")).strip()
                update_date = str(data.get("dateModified", "")).strip()
                break

        if not version_name:
            return None

        # 2. Fetch download page for version_code from R2 APK URL
        version_code = "0"
        try:
            dl_response = await client.get(
                self._DOWNLOAD_PAGE_URL, headers=self._COMMON_HEADERS
            )
            dl_response.raise_for_status()
            dl_html = dl_response.text
            apk_match = self._R2_APK_LINK_RE.search(dl_html)
            if apk_match:
                vc_match = self._VERSION_CODE_RE.search(apk_match.group(1))
                if vc_match:
                    version_code = vc_match.group(1)
        except Exception:
            pass  # version_code defaults to "0", non-critical

        return CocVersion(
            version_name=version_name,
            version_code=version_code,
            update_date=update_date or "",
        )

    async def download_apk(
        self, client: httpx.AsyncClient, shared_dir: Path
    ) -> DownloadedApk:
        # 1. Fetch download page for R2 link
        response = await client.get(
            self._DOWNLOAD_PAGE_URL, headers=self._COMMON_HEADERS
        )
        response.raise_for_status()
        html = response.text

        apk_match = self._R2_APK_LINK_RE.search(html)
        if not apk_match:
            if self._R2_XAPK_LINK_RE.search(html):
                raise RuntimeError(
                    "APKCombo only serves XAPK for this version, "
                    "plain APK not available"
                )
            raise RuntimeError("No APK download link found on APKCombo download page")

        r2_path = apk_match.group(1)
        r2_url = f"https://apkcombo.com{r2_path}"

        # 2. Extract version name from URL path and construct filename
        decoded_r2 = unquote(r2_path)
        version_match = re.search(
            r"/com\.supercell\.clashofclans/([^/]+)/", decoded_r2
        )
        if not version_match:
            raise RuntimeError("Could not determine version from download URL")
        version_name = version_match.group(1)
        normalized_filename = f"Clash_of_Clans_{version_name}_APKCombo.apk"
        target_path = shared_dir / normalized_filename

        # 3. Stream download (follow_redirects=True follows the 302 to S3)
        async with client.stream(
            "GET", r2_url, headers=self._COMMON_HEADERS
        ) as dl_response:
            dl_response.raise_for_status()

            temp_path = shared_dir / f".{normalized_filename}.part"
            temp_path.unlink(missing_ok=True)

            try:
                with temp_path.open("wb") as handle:
                    async for chunk in dl_response.aiter_bytes():
                        if chunk:
                            handle.write(chunk)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

            temp_path.replace(target_path)

        # 4. Verify the downloaded APK is a valid standalone APK
        self._verify_apk(target_path)

        return DownloadedApk(filename=normalized_filename, path=target_path)

    @staticmethod
    def _verify_apk(apk_path: Path) -> None:
        """Verify the downloaded file is a valid standalone APK.

        Checks that the file is a ZIP with AndroidManifest.xml and at
        least one DEX file. Raises RuntimeError if not.
        """
        import zipfile as zf

        try:
            with zf.ZipFile(apk_path) as archive:
                names = set(archive.namelist())
        except (zf.BadZipFile, OSError) as exc:
            raise RuntimeError(f"Downloaded file is not a valid ZIP: {exc}") from exc

        if "AndroidManifest.xml" not in names:
            raise RuntimeError(
                "Downloaded APK missing AndroidManifest.xml — "
                "likely a split/incomplete APK"
            )

        if not any(n.endswith(".dex") for n in names):
            raise RuntimeError(
                "Downloaded APK has no DEX files — "
                "likely a split/incomplete APK"
            )


# ---------------------------------------------------------------------------
# ApkPureSource (legacy – APKPure stopped publishing plain APKs for new versions)
# ---------------------------------------------------------------------------


class ApkPureSource:
    SOURCE_NAME = "apkpure"

    _HISTORY_URL = (
        "https://tapi.pureapk.com/v3/get_app_his_version"
        "?package_name=com.supercell.clashofclans&hl=en"
    )
    _DOWNLOAD_URL = (
        "https://d.apkpure.com/b/APK/com.supercell.clashofclans?version=latest"
    )
    _HISTORY_HEADERS = {
        "Ual-Access-Businessid": "projecta",
        "Ual-Access-ProjectA": '{"device_info":{"os_ver":"35"}}',
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://apkpure.com",
        "Referer": "https://apkpure.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
    }
    _DOWNLOAD_HEADERS = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://apkpure.com/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
    }
    _APK_MIME = "application/vnd.android.package-archive"
    _GENERIC_BINARY_MIME = "application/octet-stream"

    # ------------------------------------------------------------------
    # internal helpers (moved from __init__.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_version_row(item: Any) -> CocVersion | None:
        if not isinstance(item, dict):
            return None

        asset = item.get("asset")
        if not isinstance(asset, dict) or asset.get("type") != "APK":
            return None

        version_name = str(item.get("version_name", "")).strip()
        version_code = str(item.get("version_code", "")).strip()
        update_date = str(item.get("update_date", "")).strip()
        if not version_name or not version_code or not update_date:
            return None

        return CocVersion(
            version_name=version_name,
            version_code=version_code,
            update_date=update_date,
        )

    @classmethod
    def _select_latest_version(
        cls, payload: dict[str, Any]
    ) -> CocVersion | None:
        version_list = payload.get("version_list")
        if not isinstance(version_list, list):
            return None

        versions = [
            version
            for item in version_list
            if (version := cls._parse_version_row(item)) is not None
        ]
        if not versions:
            return None

        return max(
            versions,
            key=lambda version: (version.version_code_int, version.update_date),
        )

    @staticmethod
    def _decode_content_disposition_filename(
        header_value: str | None,
    ) -> str | None:
        if not header_value:
            return None

        for part in header_value.split(";"):
            key, separator, raw_value = part.strip().partition("=")
            if not separator:
                continue

            normalized_key = key.lower()
            value = raw_value.strip().strip('"')
            if normalized_key == "filename*":
                try:
                    _, _, encoded_name = value.split("'", 2)
                except ValueError:
                    return _normalize(value)
                return _normalize(unquote(encoded_name))
            if normalized_key == "filename":
                return _normalize(value)
        return None

    @staticmethod
    def _normalize_content_type(header_value: str | None) -> str:
        return header_value.partition(";")[0].strip().lower() if header_value else ""

    @classmethod
    def _is_expected_apk_content_type(cls, content_type: str) -> bool:
        normalized = cls._normalize_content_type(content_type)
        return normalized in {cls._APK_MIME, cls._GENERIC_BINARY_MIME}

    @classmethod
    def _should_validate_apk_magic(cls, content_type: str) -> bool:
        return cls._normalize_content_type(content_type) == cls._GENERIC_BINARY_MIME

    @staticmethod
    def _is_apk_filename(filename: str | None) -> bool:
        return bool(filename) and filename.lower().endswith(".apk")

    # ------------------------------------------------------------------
    # public interface
    # ------------------------------------------------------------------

    async def get_latest_version(
        self, client: httpx.AsyncClient
    ) -> CocVersion | None:
        response = await client.get(self._HISTORY_URL, headers=self._HISTORY_HEADERS)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return self._select_latest_version(payload)

    async def download_apk(
        self, client: httpx.AsyncClient, shared_dir: Path
    ) -> DownloadedApk:

        async with client.stream(
            "GET", self._DOWNLOAD_URL, headers=self._DOWNLOAD_HEADERS
        ) as response:
            response.raise_for_status()
            filename = self._decode_content_disposition_filename(
                response.headers.get("Content-Disposition")
            )
            if not filename:
                raise RuntimeError(
                    "Missing Content-Disposition filename in download response"
                )
            if not self._is_apk_filename(filename):
                raise RuntimeError(f"Unexpected download filename: {filename}")

            content_type = response.headers.get("Content-Type", "")
            if (
                response.status_code != 200
                or not self._is_expected_apk_content_type(content_type)
            ):
                raise RuntimeError(
                    "Unexpected download response: "
                    f"status={response.status_code}, content-type={content_type}"
                )

            normalized = _normalize(filename)
            target_path = shared_dir / normalized
            temp_path = shared_dir / f".{normalized}.part"
            temp_path.unlink(missing_ok=True)
            header_bytes = bytearray()
            should_validate_magic = self._should_validate_apk_magic(content_type)

            try:
                with temp_path.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        if (
                            should_validate_magic
                            and len(header_bytes) < len(_ZIP_FILE_SIGNATURE)
                        ):
                            missing_bytes = len(_ZIP_FILE_SIGNATURE) - len(header_bytes)
                            header_bytes.extend(chunk[:missing_bytes])
                        handle.write(chunk)

                if should_validate_magic and not _looks_like_zip_archive(
                    bytes(header_bytes)
                ):
                    raise RuntimeError(
                        "Unexpected APK payload for generic binary response: "
                        f"filename={filename}, content-type={content_type}"
                    )
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

            temp_path.replace(target_path)
            return DownloadedApk(filename=normalized, path=target_path)


# ---------------------------------------------------------------------------
# filename helpers (used by _decode_content_disposition_filename above)
# ---------------------------------------------------------------------------


def _normalize(raw_filename: str) -> str:
    """Normalize a raw APK filename to the standard Clash_of_Clans_ prefix."""
    normalized = unquote_plus(raw_filename.strip())
    return re.sub(
        r"^Clash[ _]+of[ _]+Clans_", "Clash_of_Clans_", normalized
    )


# ---------------------------------------------------------------------------
# registry & factory
# ---------------------------------------------------------------------------

_SOURCE_REGISTRY: dict[str, type[ApkComboSource | ApkPureSource]] = {
    ApkComboSource.SOURCE_NAME: ApkComboSource,
    ApkPureSource.SOURCE_NAME: ApkPureSource,
}


def create_source(source_name: str) -> ApkComboSource | ApkPureSource:
    """Return a new APK source instance for the given name.

    Raises ValueError if the source name is unrecognised.
    """
    cls = _SOURCE_REGISTRY.get(source_name)
    if cls is None:
        raise ValueError(
            f"Unknown APK source: {source_name!r}. "
            f"Valid sources: {', '.join(sorted(_SOURCE_REGISTRY))}"
        )
    return cls()
