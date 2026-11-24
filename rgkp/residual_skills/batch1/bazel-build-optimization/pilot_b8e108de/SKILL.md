---
name: rgkp-residual
description: Repository-grounded guidance for the target task.
---

# Residual Skill

## Validated Knowledge

### 1

In Bazel BUILD files using `rules_python`, each rule symbol must be explicitly imported via a `load()` statement before use. The mappings are:
- `load("@rules_python//python:py_binary.bzl", "py_binary")`
- `load("@rules_python//python:py_library.bzl", "py_library")`
- `load("@rules_python//python:py_test.bzl", "py_test")`

Omitting these causes a `name 'py_binary' is not defined` (or similar) error at analysis time. All three paths are confirmed by `examples/py/BUILD` and `src/test/py/bazel/bazel_external_repository_test.py`.

## Execution Guidance

### 1

For `examples/python-bazel`, apply the `py_library` + `py_binary` pattern from `examples/py/BUILD`:
1. Extract shared logic (e.g., `greet` function) from `src/main.py` into a new `src/lib.py`.
2. In `src/BUILD.bazel`, add a `py_library` target: `name='lib'`, `srcs=['lib.py']`, `visibility=['//visibility:public']`.
3. Have `//src:main` (`py_binary`) depend on `deps=[':lib']`.
4. In `tests/BUILD.bazel`, have the `py_test` depend on `deps=['//src:lib']`. Do not point `py_test` deps at a `py_binary`.
5. Update `test_main.py` to import from the library rather than duplicating functions.

### 2

The standalone workspace in `examples/python-bazel` uses `WORKSPACE` (not `MODULE.bazel`). Declare `rules_python` as a dependency by adding an `http_archive` block in `WORKSPACE` to fetch it, then call its setup functions. Also add explicit `load()` statements in `src/BUILD.bazel` and `tests/BUILD.bazel`, mirroring the pattern in `examples/py/BUILD`.

### 3

After adding the `rules_python` `http_archive` to `WORKSPACE`, register a hermetic Python toolchain by appending:
```
load("@rules_python//python:repositories.bzl", "python_register_toolchains")
python_register_toolchains(name = "python_3_11", python_version = "3.11")
```
