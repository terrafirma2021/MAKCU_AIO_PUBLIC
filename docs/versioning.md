# Version Retrieval

The updater determines the current application version dynamically to avoid
hard‑coded values. The following sources are checked in order:

1. **Bundled `config.json`** – The file shipped with the executable contains a
   `version` field reflecting the build's version. This is the primary source
   and should be kept in sync with each release.
2. **Executable name** – If the executable follows the naming convention
   `MAKCU_<major>_<minor>.exe`, the version is extracted from the file name.
3. If neither source provides a version, `0.0` is used as a fallback.

To ensure update checks compare like‑for‑like values, future builds should keep
the `config.json` version accurate or encode the version in the executable
name. Doing so allows `modules.updater.Updater` to resolve the running version
without manual changes.

