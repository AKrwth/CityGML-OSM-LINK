# Legacy Artifacts

This folder contains historical code and documents that are intentionally excluded from active runtime paths.

## Why this exists

To keep runtime structure clear:
- active addon/runtime code stays under `pipeline/`, `utils/`, and root entry files,
- historical and deprecated artifacts are moved under `docs/legacy/`.

## Contents

- `pipeline-Legacy/`
  - Previously located at `pipeline/Legacy/`.
  - Moved during structural clarity cleanup because it had no active imports in runtime code.
  - Kept for audit/history reference only.

## Policy

- Do not import from files under `docs/legacy/`.
- If code must be restored, copy/refactor it back into active modules first.
