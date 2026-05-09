# Versioning

GitHub Releases are the release source of truth for VR Hotspot. The current
stable release is `v1.0.4`.

The package version is recorded in two places:

- `pyproject.toml` stores the Python project version as `1.0.4`.
- `backend/vr_hotspotd/__init__.py` exposes the package `__version__` as
  `1.0.4`.

These values must stay aligned. Treat drift between them as a release metadata
bug.

## Backend Metadata

The backend exposes both `version` and `server_version` in status and info
responses.

- `version` is the application version, currently `1.0.4`.
- `server_version` is the server identifier, currently `vr-hotspotd/1.0.4`.

New consumers should read `version` when they need the release number.
`server_version` is kept for compatibility and for contexts where the daemon
identifier is useful.

## UI Metadata

The UI prefers `data.version` from the backend. It only falls back to
`data.server_version` when `data.version` is missing.

`assets/index.html` contains only a static fallback version in the initial
markup. That value is displayed before live backend metadata is loaded, or if
the UI cannot retrieve backend metadata. It is not the release source of truth.

## Drift Prevention

`tests/test_version_metadata.py` is the drift-prevention test for version
metadata. It checks that:

- `pyproject.toml` matches `backend/vr_hotspotd/__init__.py`.
- API version constants match the package version.
- backend status metadata reports the current version values.
- the static UI fallback in `assets/index.html` matches the current release.

Update this test whenever intentionally bumping the release version.

## How To Bump Versions

1. Create or identify the intended GitHub Release tag.
2. Update `pyproject.toml` `[project].version`.
3. Update `backend/vr_hotspotd/__init__.py` `__version__`.
4. Update the static fallback in `assets/index.html`.
5. Update expected values in `tests/test_version_metadata.py`.
6. Run the test suite before publishing the release.
