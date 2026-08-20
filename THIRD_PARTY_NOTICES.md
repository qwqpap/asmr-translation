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

## Qt 6 via PySide6 (the `gui` extra)

- Upstream: https://www.qt.io/ and https://doc.qt.io/qtforpython/
- License: GNU Lesser General Public License v3.0 for the PyPI wheels, covering both the
  Qt libraries and the PySide6/Shiboken6 bindings.
- SPDX: `LGPL-3.0-only`
- Full text: https://www.gnu.org/licenses/lgpl-3.0.html
- Not bundled in this repository or in the MSI. `pip install .[gui]` installs
  `PySide6-Essentials` from PyPI; `packaging/linux/install.sh` does the same inside a
  per-user venv.
- Qt is used through dynamic linking only; this project contains no Qt source and no
  derivative of it. As LGPL-3.0 requires, that Qt can be replaced: install a
  distribution-provided PySide6 and create the venv with `--system-site-packages`, or
  replace the shared libraries under `site-packages/PySide6/Qt/lib` with a compatible
  build. Neither requires changes to this project.
- AGPL-3.0-or-later permits linking against LGPL-3.0 libraries, so the combined work
  remains distributable under this repository's license.

## PortAudio via sounddevice (the `gui` extra)

- `sounddevice`: MIT, https://github.com/spatialaudio/python-sounddevice
- PortAudio: MIT-style license, http://www.portaudio.com/
- On Linux PortAudio comes from the distribution (`libportaudio2`) and is not
  redistributed here. The Windows `sounddevice` wheel carries its own PortAudio build.

## Other pip dependencies

Installed from PyPI, never vendored into this repository:

- `faster-whisper` (MIT) and `ctranslate2` (MIT)
- `numpy` (BSD-3-Clause)
- `keyring` (MIT), non-Windows only
- `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` (NVIDIA proprietary licenses), only with the
  `cuda` extra, and only installed at the user's request

Speech and translation model weights are not part of this project and are never downloaded
automatically; their own licenses apply and must be reviewed by whoever installs them.
