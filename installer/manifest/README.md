# Release manifest

`artifacts.json` is intentionally a checked-in template. Before making a release, replace
the Python and `get-pip.py` placeholder values with the exact official patch URL, byte size,
and SHA-256. Add optional `ffmpeg` and `whisper_model` objects only when their exact archive,
target directory, size, and hash are known. The release script computes hashes for the local
project wheel and dependency locks in the staging copy.

The bootstrapper accepts a mirror only as a user-provided base URL. It never changes the
expected hashes and never persists a temporary signed media URL.
