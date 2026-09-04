#!/usr/bin/env python3
"""Download, name, and layer media for a scratch container image."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DEFAULT_LAYER_BYTES = 100_000_000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
CONTENT_RANGE_RE = re.compile(r"^bytes (?:\d+-\d+|\*)/(\d+)$")


class NoDowngradeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        resolved = urllib.parse.urljoin(request.full_url, new_url)
        if urllib.parse.urlsplit(request.full_url).scheme == "https" and urllib.parse.urlsplit(resolved).scheme != "https":
            raise ValueError(f"refusing HTTPS redirect downgrade: {resolved}")
        return super().redirect_request(request, response, code, message, headers, resolved)


URL_OPENER = urllib.request.build_opener(NoDowngradeRedirect)


@dataclass
class Entry:
    url: str
    path: str
    output_path: str = ""


class DownloadFailure(Exception):
    def __init__(self, failures: list[tuple[str, str]]):
        self.failures = failures
        super().__init__("; ".join(f"{url}: {reason}" for url, reason in failures))


def url_key(url: str) -> str:
    return hashlib.blake2b(url.encode(), digest_size=32).hexdigest()


def validate_url(url: str, allow_http: bool) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if allow_http and parsed.scheme == "http" and parsed.netloc:
        return
    raise ValueError(f"URL must use HTTPS: {url}")


def validate_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or value.startswith("/") or "\\" in value or "\x00" in value:
        raise ValueError(f"path must be relative to /data: {value!r}")
    if path.as_posix() != value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"path must be normalized and contain no control characters: {value!r}")
    if any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"path contains an unsafe component: {value!r}")
    if len(path.parts) > 16 or len(value.encode("utf-8")) > 900:
        raise ValueError(f"path is too deep or long: {value!r}")
    if any(len(part.encode("utf-8")) > 180 for part in path.parts):
        raise ValueError(f"path component is too long: {value!r}")
    return path.as_posix()


def default_path(url: str) -> str:
    name = urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(url).path).name)
    if not name or name in (".", ".."):
        name = "media"
    if len(name.encode("utf-8")) > 180:
        suffix = PurePosixPath(name).suffix[:20]
        name = f"media-{url_key(url)[:16]}{suffix}"
    return validate_path(name)


def add_suffix(path: str, suffix: str) -> str:
    item = PurePosixPath(path)
    extension = item.suffix
    stem = item.name[: -len(extension)] if extension else item.name
    return str(item.with_name(f"{stem}--{suffix}{extension}"))


def parse_manifest(text: str, allow_http: bool) -> tuple[list[Entry], int]:
    entries_by_url: dict[str, Entry] = {}
    duplicates = 0
    for line_number, raw in enumerate(text.splitlines(), 1):
        url = raw.strip()
        if not url or url.startswith("#"):
            continue
        try:
            validate_url(url, allow_http)
        except ValueError as error:
            raise ValueError(f"manifest line {line_number}: {error}") from error
        if url in entries_by_url:
            duplicates += 1
            continue
        entries_by_url[url] = Entry(url, default_path(url))

    entries = list(entries_by_url.values())
    if not entries:
        raise ValueError("manifest contains no media entries")

    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.path] = counts.get(entry.path, 0) + 1
    used: set[str] = set()
    for entry in sorted(entries, key=lambda value: (value.path, value.url)):
        candidate = entry.path
        if counts[entry.path] > 1 or candidate in used:
            candidate = add_suffix(entry.path, url_key(entry.url)[:16])
        counter = 2
        base = candidate
        while candidate in used:
            candidate = add_suffix(base, str(counter))
            counter += 1
        entry.output_path = candidate
        used.add(candidate)
    return entries, duplicates


def read_manifest(args: argparse.Namespace) -> str:
    if args.manifest_file:
        return Path(args.manifest_file).read_text(encoding="utf-8-sig")
    validate_url(args.manifest_url, args.allow_http)
    request = urllib.request.Request(args.manifest_url, headers={"User-Agent": "media-pull/1"})
    token = os.environ.get("MANIFEST_TOKEN")
    if token:
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with URL_OPENER.open(request, timeout=args.timeout) as response:
            data = response.read(MAX_MANIFEST_BYTES + 1)
    except Exception as error:
        raise RuntimeError(f"manifest fetch failed: {type(error).__name__}: {error}") from error
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
    return data.decode("utf-8-sig")


def download_once(url: str, part: Path, timeout: int) -> None:
    offset = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "media-pull/1"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = URL_OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        if error.code == 416 and part.exists():
            match = CONTENT_RANGE_RE.match(error.headers.get("Content-Range", ""))
            if match and part.stat().st_size == int(match.group(1)):
                return
            part.unlink()
        raise
    with response:
        append = offset > 0 and getattr(response, "status", None) == 206
        match = CONTENT_RANGE_RE.match(response.headers.get("Content-Range", ""))
        expected_size = int(match.group(1)) if match else None
        if expected_size is None and response.headers.get("Content-Length"):
            expected_size = (offset if append else 0) + int(response.headers["Content-Length"])
        with part.open("ab" if append else "wb") as output:
            shutil.copyfileobj(response, output, 1024 * 1024)
        if expected_size is not None and part.stat().st_size != expected_size:
            raise OSError(f"incomplete download: expected {expected_size} bytes, got {part.stat().st_size}")


def download_blob(url: str, cache_dir: Path, retries: int, timeout: int) -> Path:
    key = url_key(url)
    target = cache_dir / key
    part = cache_dir / f"{key}.part"
    target.unlink(missing_ok=True)  # No checksum means a completed URL cache may be stale.
    reason = "unknown error"
    for attempt in range(retries):
        try:
            download_once(url, part, timeout)
            os.replace(part, target)
            return target
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            if attempt + 1 < retries:
                time.sleep(min(2**attempt, 8))
    raise DownloadFailure([(url, reason)])


def pack_layers(entries: list[Entry], files: dict[str, Path], layer_max_bytes: int) -> list[list[Entry]]:
    layers: list[list[Entry]] = []
    current: list[Entry] = []
    current_size = 0
    for entry in sorted(entries, key=lambda value: value.output_path):
        size = files[entry.url].stat().st_size
        estimated_size = ((size + 511) // 512) * 512 + 16 * 1024 + 2 * len(entry.output_path.encode("utf-8"))
        if estimated_size > layer_max_bytes:
            if current:
                layers.append(current)
                current, current_size = [], 0
            layers.append([entry])
            continue
        if current and current_size + estimated_size > layer_max_bytes:
            layers.append(current)
            current, current_size = [], 0
        current.append(entry)
        current_size += estimated_size
    if current:
        layers.append(current)
    return layers


def materialize(context: Path, layers: list[list[Entry]], files: dict[str, Path]) -> list[int]:
    sizes: list[int] = []
    for number, layer in enumerate(layers):
        layer_root = context / f"layer-{number:04d}" / "data"
        total = 0
        for entry in layer:
            source = files[entry.url]
            target = layer_root / entry.output_path
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
            except OSError:
                shutil.copyfile(source, target)
            os.utime(target, (0, 0))
            total += source.stat().st_size
        sizes.append(total)

    for directory in sorted((path for path in context.rglob("*") if path.is_dir()), reverse=True):
        os.utime(directory, (0, 0))
    dockerfile = ["FROM scratch"]
    dockerfile.extend(f"COPY layer-{number:04d}/ /" for number in range(len(layers)))
    (context / "Dockerfile").write_text("\n".join(dockerfile) + "\n", encoding="utf-8")
    return sizes


def write_report(path: Path, status: str, summary: dict[str, object], failures: list[tuple[str, str]]) -> None:
    lines = ["# media-pull build report", "", f"Status: **{status}**", ""]
    for key, value in summary.items():
        lines.append(f"- {key}: {value}")
    if failures:
        lines.extend(["", "## Failures", "", "| URL | Reason |", "|---|---|"])
        for url, reason in failures:
            safe_url = url.replace("|", "%7C").replace("\r", "").replace("\n", "\\n")
            safe_reason = reason.replace("|", "&#124;").replace("\r", "").replace("\n", "\\n")
            lines.append(f"| {safe_url} | {safe_reason} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest-url", default=os.environ.get("MANIFEST_URL"))
    source.add_argument("--manifest-file")
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("JOBS", "8")))
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--layer-max-bytes", type=int, default=DEFAULT_LAYER_BYTES)
    parser.add_argument("--output", default=".media-build")
    parser.add_argument("--cache-dir", default=".media-cache")
    parser.add_argument("--allow-http", action="store_true", help="development/testing only")
    args = parser.parse_args()

    output = Path(args.output)
    cache_dir = Path(args.cache_dir)
    if args.jobs < 1 or args.retries < 1 or args.timeout < 1:
        parser.error("jobs, retries, and timeout must be positive")
    if not 1 <= args.layer_max_bytes <= DEFAULT_LAYER_BYTES:
        parser.error(f"layer-max-bytes must be between 1 and {DEFAULT_LAYER_BYTES}")

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    report = output / "report.md"
    summary: dict[str, object] = {}

    try:
        entries, duplicates = parse_manifest(read_manifest(args), args.allow_http)
        summary.update({"manifest entries": len(entries) + duplicates, "duplicate URLs removed": duplicates})
        files: dict[str, Path] = {}
        failures: list[tuple[str, str]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
            future_to_url = {
                executor.submit(download_blob, entry.url, cache_dir, args.retries, args.timeout): entry.url
                for entry in entries
            }
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    files[url] = future.result()
                except DownloadFailure as error:
                    failures.extend(error.failures)
                except Exception as error:
                    failures.append((url, f"{type(error).__name__}: {error}"))
        if failures:
            write_report(report, "FAILED", summary, failures)
            for url, reason in failures:
                print(f"FAILED {url}\n  {reason}", file=sys.stderr)
            return 1

        layers = pack_layers(entries, files, args.layer_max_bytes)
        layer_sizes = materialize(output / "context", layers, files)
        summary.update(
            {
                "unique URLs": len(entries),
                "downloaded files": len(files),
                "layers": len(layers),
                "layer payload bytes": ", ".join(map(str, layer_sizes)),
            }
        )
        write_report(report, "OK", summary, [])
        print(f"Prepared {len(entries)} files in {len(layers)} layers; report: {report}")
        return 0
    except DownloadFailure as error:
        write_report(report, "FAILED", summary, error.failures)
        for url, reason in error.failures:
            print(f"FAILED {url}\n  {reason}", file=sys.stderr)
        return 1
    except Exception as error:
        reason = f"{type(error).__name__}: {error}"
        write_report(report, "FAILED", summary, [("(manifest/build)", reason)])
        print(f"FAILED: {reason}\nReport: {report}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
