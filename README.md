# CEF framework repacks

Builds of the Chromium Embedded Framework, repacked from the CEF project's
official minimal builds (cef-builds.spotifycdn.com). Each release is one
CEF version:

- `cef-<version>-mac-x64.tar.xz`, `cef-<version>-mac-arm64.tar.xz`: only
  `Chromium Embedded Framework.framework/` (symlinks and modes kept) and
  the licenses, `LICENSE.txt` and `CREDITS.html`; xz -9e.
- `pin.json`: the version, its macOS API hash, and per platform the
  archive's URL, size and SHA-256.

Releases are never replaced.

## A new version

Actions → **pack** → Run workflow, with the CEF version (`154.0.34`): it
runs `pack.py` and creates the release.

`python3 -I pack.py <version> [--platform mac-x64] [--source <tar.bz2>]`
does the same locally, into `out/`. The archives are reproducible: the
same build packs to the same bytes. Python's standard library only.

## License

CEF is under the BSD license (`LICENSE.txt` in each archive); Chromium's
and its parts' licenses are in `CREDITS.html`.
