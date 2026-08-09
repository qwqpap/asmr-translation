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
