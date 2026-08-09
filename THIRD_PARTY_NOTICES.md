# Third-party notices

## asmr-one-downloader

- Upstream: https://github.com/thiliapr/asmr-one-downloader
- Author/copyright: thiliapr and contributors
- License: GNU Affero General Public License v3.0 or later
- SPDX: `AGPL-3.0-or-later`
- Adaptation: the upstream API/file-tree and curl resume ideas were reworked into
  `asmr_lrc.downloader` and the JSONL worker protocol. The GUI adds bounded retries,
  safe Windows path handling, atomic manifests, cancellation and queue integration.
- No upstream source is bundled verbatim; the adapted behavior remains available in
  source form under this repository's AGPL license.

The upstream source and license are available at the URL above. See `COPYING` for
the license that applies to this project and the adapted downloader.

## Optional Windows runtime artifacts

The MSI does not bundle these artifacts. The first-run bootstrapper downloads them only
after explicit user confirmation and verifies the SHA-256 values recorded in the release
manifest:

- Python Embeddable: https://www.python.org/downloads/windows/ (Python Software Foundation
  License; the exact version and hash are recorded per release).
- `get-pip.py`: https://bootstrap.pypa.io/get-pip.py (PSF license; the exact hash is recorded
  per release).
- FFmpeg, when a release manifest offers it, is listed with its upstream build, version and
  license in that release's manifest. It is not silently installed.
- WiX Toolset is a maintainer build dependency, not an installed application component:
  https://wixtoolset.org/.

The project wheel and dependency lock files are generated from this repository. Their
licenses and notices remain available in the installed `THIRD_PARTY_NOTICES.md` and in the
source tree.
