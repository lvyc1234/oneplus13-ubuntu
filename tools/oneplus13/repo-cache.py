#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Restore and verify pinned OnePlus 13 build caches from a Git tag."""

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "devices/oneplus13/repo-cache-r280.json"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_name(value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Unsafe cache path")
    return path


def safe_extract(archive, destination):
    destination = destination.resolve()
    with tarfile.open(archive) as source:
        for item in source:
            relative = item.name.removeprefix("./")
            if relative in {"", "."}:
                if item.isdir():
                    continue
                raise ValueError("Invalid cache root entry")
            path = safe_name(relative)
            target = (destination / path).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("Cache member escapes extraction root")
            if not (item.isfile() or item.isdir()):
                raise ValueError("Cache archive contains unsupported entry")
            if item.size > 512 * 1024 * 1024:
                raise ValueError("Oversized cache entry")
        source.extractall(destination, filter="data")


def download_candidates(url):
    marker = "https://raw.githubusercontent.com/"
    if not url.startswith(marker):
        return [url]
    owner, repository, tag, *parts = url.removeprefix(marker).split("/")
    suffix = "/".join(parts)
    return [
        url,
        f"https://github.com/{owner}/{repository}/raw/{tag}/{suffix}",
    ]


def download(url, target, expected_bytes):
    errors = []
    for attempt in range(6):
        candidates = download_candidates(url)
        candidate = candidates[min(attempt, len(candidates) - 1)]
        offset = target.stat().st_size if target.exists() else 0
        if offset > expected_bytes:
            target.unlink()
            offset = 0
        if offset == expected_bytes:
            return
        request_headers = {"User-Agent": "oneplus13-reproducible-cache/1"}
        if offset:
            request_headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(
            candidate, headers=request_headers
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                if response.status not in (200, 206):
                    raise RuntimeError(
                        f"Cache download returned HTTP {response.status}"
                    )
                if offset and response.status == 200:
                    target.unlink(missing_ok=True)
                    offset = 0
                with target.open("ab" if offset else "xb") as output:
                    while block := response.read(1024 * 1024):
                        output.write(block)
                if target.stat().st_size == expected_bytes:
                    return
                raise OSError("Incomplete cache chunk")
        except (OSError, RuntimeError) as error:
            errors.append(type(error).__name__)
            if attempt == 5:
                raise RuntimeError(
                    "Cache transport failed: " + ",".join(errors)
                ) from None
            time.sleep(attempt + 1)


def restore_archive(item, downloads):
    archive = downloads / item["file"]
    if archive.is_file() and digest(archive) == item["sha256"]:
        return archive
    if archive.exists():
        archive.unlink()
    temporary = archive.with_suffix(archive.suffix + ".partial")
    temporary.unlink(missing_ok=True)
    chunks = item["chunks"]

    def one(index):
        chunk = temporary.with_name(temporary.name + f".{index:03d}")
        chunk.unlink(missing_ok=True)
        download(chunks[index]["url"], chunk, chunks[index]["bytes"])
        if (
            chunk.stat().st_size != chunks[index]["bytes"]
            or digest(chunk) != chunks[index]["sha256"]
        ):
            chunk.unlink(missing_ok=True)
            raise RuntimeError(f"Cache chunk {index} verification failed")
        return chunk

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(one, range(len(chunks))))
    try:
        with temporary.open("xb") as output:
            for path in paths:
                with path.open("rb") as source:
                    while block := source.read(1024 * 1024):
                        output.write(block)
        if (
            temporary.stat().st_size != item["bytes"]
            or digest(temporary) != item["sha256"]
        ):
            raise RuntimeError("Rebuilt cache archive verification failed")
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)
        for path in paths:
            path.unlink(missing_ok=True)
    return archive


def restore_component(name, item, root):
    downloads = root / "cache/downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = restore_archive(item, downloads)
    if name in {"sources", "rootfs-debs", "resources-debs"}:
        safe_extract(archive, root / "cache")
        if name in {"rootfs-debs", "resources-debs"}:
            directory = root / "cache" / (
                "oneplus13-rootfs-debs"
                if name == "rootfs-debs"
                else "oneplus13-resources-debs"
            )
            for partial in directory.glob(".deb-*.partial"):
                partial.unlink()
    elif name == "apt-lists":
        staging = root / "cache/apt-lists-staging"
        temporary = Path(tempfile.mkdtemp(prefix="apt-lists-", dir=downloads))
        try:
            safe_extract(archive, temporary)
            for nested in temporary.glob("apt-lists-*.tar.gz"):
                snapshot = nested.name.removeprefix("apt-lists-").removesuffix(
                    ".tar.gz"
                )
                target = root / "cache/apt-lists" / snapshot
                target.mkdir(parents=True, exist_ok=True)
                safe_extract(nested, target)
        finally:
            for path in sorted(temporary.rglob("*"), reverse=True):
                if path.is_file() or path.is_symlink():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            temporary.rmdir()
    else:
        raise ValueError(f"Unknown cache component: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["restore"])
    parser.add_argument("components", nargs="+")
    parser.add_argument("--root", type=Path, default=REPO)
    options = parser.parse_args()
    if options.command != "restore":
        raise ValueError("Only restore is supported")
    manifest = json.loads(MANIFEST.read_text())
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported cache manifest")
    root = options.root.resolve()
    selected = []
    for name in options.components:
        if name not in manifest["components"]:
            raise ValueError(f"Unknown cache component: {name}")
        selected.append((name, manifest["components"][name]))
    for name, item in selected:
        print(f"ONE_PLUS_13_CACHE_RESTORE {name}", flush=True)
        restore_component(name, item, root)
    print("ONE_PLUS_13_CACHE_RESTORE_PASS", flush=True)


if __name__ == "__main__":
    main()
