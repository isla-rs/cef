#!/usr/bin/env python3
"""Repacks CEF builds: the framework and its licenses only, tar + xz.

    python3 -I pack.py <cef version> [--platform mac-x64] [--out <dir>]
                       [--source <cef_binary_…_minimal.tar.bz2>] [--base-url <url>]

For each platform (default: mac-x64 and mac-arm64) it:

1. downloads Spotify's minimal build of that version from the CEF build CDN
   (named and checked by SHA-1 from the CDN's index), unless `--source`
   gives it (one platform only);
2. keeps only `Release/Chromium Embedded Framework.framework/` and the
   licenses (`LICENSE.txt`, `CREDITS.html` where present), with their
   symlinks and modes, and reads the API hash for macOS from
   `include/cef_api_versions.h`;
3. writes `cef-<version>-<platform>.tar.xz` into `--out` (default `out/`):
   a tar with sorted entries, owner 0 and a fixed mtime, so the same build
   packs to the same bytes, compressed with xz -9e;
4. prints its size and SHA-256.

Then it writes `pin.json` there: the version, its API hash, and each
archive's URL under `--base-url` (default: this repo's releases), size and
SHA-256. Python's standard library only.
"""

import argparse
import hashlib
import json
import lzma
import os
import re
import shutil
import stat
import sys
import tarfile
import time
import urllib.parse
import urllib.request

CDN = "https://cef-builds.spotifycdn.com"
BASE_URL = "https://github.com/isla-rs/cef/releases/download"
FRAMEWORK = "Chromium Embedded Framework.framework"
LICENSES = ["LICENSE.txt", "CREDITS.html"]
# Our platform names, and the CDN's.
PLATFORMS = {"mac-x64": "macosx64", "mac-arm64": "macosarm64"}
# Every entry's mtime in our archives (2026-01-01): the bytes depend only on the files.
MTIME = 1767225600
CHUNK = 1 << 20


def short(version):
    return version.split("+", 1)[0]


def index_entry(version, cdn_platform, index):
    """The CDN's entry for `version` (full or short) and its minimal build."""
    versions = index[cdn_platform]["versions"]
    for entry in versions:
        full = entry["cef_version"]
        if version in (full, short(full)):
            for file in entry["files"]:
                if file["type"] == "minimal":
                    return full, file
            sys.exit(f"CEF {full} for {cdn_platform} has no minimal build")
    sys.exit(f"no CEF {version} for {cdn_platform} in {CDN}/index.json")


def fetch_index():
    print(f"reading {CDN}/index.json", flush=True)
    with urllib.request.urlopen(f"{CDN}/index.json", timeout=60) as response:
        return json.load(response)


def download(url, path, sha1, size):
    """Downloads `url` to `path` (kept if it is there and matches)."""
    if os.path.isfile(path) and os.path.getsize(path) == size and file_hash(path, "sha1") == sha1:
        print(f"have {os.path.basename(path)}", flush=True)
        return
    print(f"downloading {url} ({size / 1e6:.1f} MB)", flush=True)
    hash = hashlib.sha1()
    done, started, shown = 0, time.monotonic(), 0
    partial = path + ".part"
    with urllib.request.urlopen(url, timeout=60) as response, open(partial, "wb") as out:
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            hash.update(chunk)
            out.write(chunk)
            done += len(chunk)
            if done - shown >= 16 * CHUNK:
                shown = done
                rate = done / 1e6 / max(time.monotonic() - started, 1e-3)
                print(f"  {done / 1e6:.0f} of {size / 1e6:.0f} MB, {rate:.1f} MB/s", flush=True)
    if done != size or hash.hexdigest() != sha1:
        os.remove(partial)
        sys.exit(f"{url}: {done} bytes, SHA-1 {hash.hexdigest()}; the index says {size}, {sha1}")
    os.replace(partial, path)
    print(f"  {done / 1e6:.1f} MB in {time.monotonic() - started:.0f} s", flush=True)


def file_hash(path, name):
    hash = hashlib.new(name)
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(CHUNK), b""):
            hash.update(chunk)
    return hash.hexdigest()


def kept(name):
    """Where member `name` of the CEF archive goes in ours, if we keep it."""
    parts = name.split("/")[1:]  # drop `cef_binary_<version>_<platform>_minimal/`
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    if parts[0] == "Release" and len(parts) > 1 and parts[1] == FRAMEWORK:
        return "/".join(parts[1:])
    if parts[-1] in LICENSES and (len(parts) == 1 or (len(parts) == 2 and parts[0] == "Release")):
        return parts[-1]
    return None


API_HASH = re.compile(r'#elif defined\(OS_MAC\)\s*#define CEF_API_HASH_(\d+) "([0-9a-f]{40})"')
API_LAST = re.compile(r"#define CEF_API_VERSION_LAST CEF_API_VERSION_(\d+)")


def api_hash(header):
    """The macOS API hash of the build's last API version."""
    last = API_LAST.search(header)
    if not last:
        sys.exit("cef_api_versions.h has no CEF_API_VERSION_LAST")
    for version, digest in API_HASH.findall(header):
        if version == last.group(1):
            return int(version), digest
    sys.exit(f"cef_api_versions.h has no macOS hash for API version {last.group(1)}")


def unpack(source, staging):
    """Extracts what we keep of `source` into `staging`: the API version and hash."""
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    api = None
    with tarfile.open(source, "r|bz2") as tar:
        for member in tar:
            if member.name.endswith("/include/cef_api_versions.h"):
                api = api_hash(tar.extractfile(member).read().decode())
                continue
            rel = kept(member.name)
            if rel is None:
                continue
            out = os.path.join(staging, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if member.isdir():
                os.makedirs(out, exist_ok=True)
            elif member.issym():
                target = os.path.normpath(os.path.join(os.path.dirname(rel), member.linkname))
                if os.path.isabs(member.linkname) or not target.startswith(FRAMEWORK + os.sep):
                    sys.exit(f"{member.name}: a link out of the framework ({member.linkname})")
                os.symlink(member.linkname, out)
            elif member.isfile():
                with tar.extractfile(member) as data, open(out, "wb") as file:
                    shutil.copyfileobj(data, file, CHUNK)
                os.chmod(out, 0o755 if member.mode & 0o111 else 0o644)
            else:
                sys.exit(f"{member.name}: not a file, folder or symlink")
    if not os.path.isfile(os.path.join(staging, FRAMEWORK, "Chromium Embedded Framework")):
        sys.exit(f"{source} has no Release/{FRAMEWORK}")
    if api is None:
        sys.exit(f"{source} has no include/cef_api_versions.h")
    return api


def entries(staging):
    """Every path under `staging`, relative, sorted, folders before what they hold."""
    paths = []
    for root, dirs, files in os.walk(staging):
        dirs.sort()
        for name in dirs + sorted(files):
            paths.append(os.path.relpath(os.path.join(root, name), staging))
    return sorted(paths, key=lambda p: p.split(os.sep))


def repack(staging, archive):
    """`staging` as a reproducible .tar.xz: its size in bytes on disk, unpacked."""
    unpacked = 0
    partial = archive + ".part"
    with lzma.open(partial, "wb", format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=9 | lzma.PRESET_EXTREME) as xz:
        with tarfile.open(fileobj=xz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for rel in entries(staging):
                path = os.path.join(staging, rel)
                meta = os.lstat(path)
                info = tarfile.TarInfo(rel.replace(os.sep, "/"))
                info.mtime, info.uid, info.gid, info.uname, info.gname = MTIME, 0, 0, "", ""
                if stat.S_ISLNK(meta.st_mode):
                    info.type, info.linkname, info.mode = tarfile.SYMTYPE, os.readlink(path), 0o755
                    tar.addfile(info)
                elif stat.S_ISDIR(meta.st_mode):
                    info.type, info.mode = tarfile.DIRTYPE, 0o755
                    tar.addfile(info)
                else:
                    info.size, info.mode = meta.st_size, 0o755 if meta.st_mode & 0o111 else 0o644
                    unpacked += meta.st_size
                    with open(path, "rb") as file:
                        tar.addfile(info, file)
    os.replace(partial, archive)
    return unpacked


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("version", help="the CEF version, full (154.0.34+g14c5a08+chromium-154.0.8037.98) or short (154.0.34)")
    parser.add_argument("--platform", action="append", choices=sorted(PLATFORMS), help="repeatable; default: all")
    parser.add_argument("--out", default="out")
    parser.add_argument("--source", help="Spotify's .tar.bz2 already downloaded (one --platform)")
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    platforms = args.platform or sorted(PLATFORMS)
    if args.source and len(platforms) != 1:
        sys.exit("--source takes one --platform")
    os.makedirs(args.out, exist_ok=True)

    index = fetch_index()
    pin = {"version": None, "cef": None, "api_version": None, "api_hash": None, "platforms": {}}
    for platform in platforms:
        full, file = index_entry(args.version, PLATFORMS[platform], index)
        version = short(full)
        source = args.source
        if source is None:
            source = os.path.join(args.out, file["name"])
            download(f"{CDN}/{urllib.parse.quote(file['name'])}", source, file["sha1"], file["size"])
        elif file_hash(source, "sha1") != file["sha1"]:
            sys.exit(f"{source} is not {file['name']} (its SHA-1 differs from the index's)")

        started = time.monotonic()
        staging = os.path.join(args.out, f"cef-{version}-{platform}")
        print(f"unpacking {os.path.basename(source)}", flush=True)
        api_version, digest = unpack(source, staging)
        name = f"cef-{version}-{platform}.tar.xz"
        archive = os.path.join(args.out, name)
        print(f"packing {name} (xz -9e)", flush=True)
        unpacked = repack(staging, archive)
        shutil.rmtree(staging)
        size, sha256 = os.path.getsize(archive), file_hash(archive, "sha256")
        print(f"{name}  {size:,} bytes ({size / 1e6:.1f} MB; {unpacked / 1e6:.1f} MB unpacked)  sha256 {sha256}  in {time.monotonic() - started:.0f} s")

        for key, value in [("version", version), ("cef", full), ("api_version", api_version), ("api_hash", digest)]:
            if pin[key] not in (None, value):
                sys.exit(f"{platform}: {key} {value} differs from the other platform's {pin[key]}")
            pin[key] = value
        url = f"{args.base_url}/{version}/{name}"
        pin["platforms"][platform] = {"url": url, "size": size, "sha256": sha256}

    path = os.path.join(args.out, "pin.json")
    with open(path, "w") as file:
        json.dump(pin, file, indent=2)
        file.write("\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
