# Developer / Debug Scripts

This folder contains standalone diagnostic and test scripts used during development. They are **not** part of the add-on runtime and are **never** imported by `__init__.py` or `auto_load.py`.

## Scripts

| Script | Purpose | How to run |
| ------ | ------- | ---------- |
| `check_bidx.py` | Compare `building_idx` values in the link DB vs scene meshes | `blender --background <file>.blend --python docs/dev/check_bidx.py` |
| `check_keys.py` | Inspect `source_tile` keys in a link SQLite DB | `python docs/dev/check_keys.py` (standalone, no bpy needed) |
| `test_launch.py` | Full link-operator smoke test (register addon → run link → verify DB) | `blender --background <file>.blend --python docs/dev/test_launch.py` |
| `test_link.py` | Quick link smoke test (runs inside an open Blender session) | Open Blender → Python console → `exec(open("docs/dev/test_link.py").read())` |
| `test_materialize.py` | Materialize-operator smoke test (verify face attributes) | `blender --background <file>.blend --python docs/dev/test_materialize.py` |

### Requirements

- Most scripts require Blender's Python environment (`bpy`, `bmesh`, `mathutils`).
- `check_keys.py` is the exception — it only needs `sqlite3` (stdlib).
- All scripts expect `m1dc_settings` to be registered (add-on must be enabled in the .blend file).

## outputs/

The `outputs/` subfolder holds sample console outputs from previous test runs. These are **machine-specific** and **gitignored** — they are not tracked in version control.

To regenerate outputs, run the corresponding script and redirect stdout:

```shell
blender --background Test3.blend --python docs/dev/check_bidx.py > docs/dev/outputs/check_bidx_output.txt 2>&1
```

## Policy

- Debug/test scripts go in `docs/dev/`, not in the repo root.
- Output/log files go in `docs/dev/outputs/` and must not be committed.
- Leading underscores (`_test_*`, `_check_*`) are not used — use clean names.
- No script in this folder may be imported by the add-on runtime.
