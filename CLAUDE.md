# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

qbatch is a command-line tool and Python library for executing shell commands in parallel on high-performance computing clusters. It abstracts differences between various job schedulers (PBS/Torque, SGE, Slurm) and provides a unified interface for submitting batch jobs.

## Common Development Commands

### Testing
```bash
# Run all tests
pytest test/

# Run a specific test
pytest test/test_schedulers.py::test_pbs_header_directives

# Run with verbose output
pytest -v test/
```

### Linting
```bash
ruff check --fix qbatch/ test/
ruff format qbatch/ test/
```

### Building
```bash
# Build source distribution and wheel using uv
uv build

# Build for local testing
uv pip install -e .
```

### Installation
```bash
# Install from source
pip install .

# Install with uv
uv pip install .
```

## Architecture

Domain terms (task, chunk, job, job script, scheduler, generate phase, submit
phase) are defined in `CONTEXT.md`. Use them.

### Modules

Imports go in one direction: `errors` ← `schedulers` ← `spec` ← `qbatch`.

- **qbatch/errors.py**: `QbatchError`, raised for every user-facing error.
- **qbatch/schedulers.py**: one adapter class per scheduler (`pbs`, `sge`,
  `slurm`, `local`, `container`), the job script templates, and the
  registry. Everything that differs between schedulers lives here.
- **qbatch/spec.py**: `JobSpec`, the dataclass that describes one
  submission. Field defaults read the `QBATCH_*` environment variables at
  construction. `__post_init__` validates; `from_kwargs` accepts argparse
  option names.
- **qbatch/qbatch.py**: the command line parser, the driver, and the submit
  phase.

### Flow

`qbatchParser` builds a `JobSpec` with `JobSpec.from_kwargs` and calls
`qbatchDriver(spec)`. The driver reads the tasks, gets the adapter with
`scheduler_for(spec)`, resolves dependencies, calls `build_scripts()` (the
generate phase), then `submit_scripts(scripts, spec)` (the submit phase).

### The scheduler adapter

Callers use four members:
- `build_scripts()`: returns `[(basename, text)]`. Text only, no side effects.
- `find_dependencies()`: returns `(array_ids, job_ids)` for the spec's
  `depend` patterns. Only PBS and Slurm query the scheduler.
- `required_binaries`: checked on `PATH` before a real submission.
- `submit(path)`: hands one written script to the scheduler.

The base class implements `build_scripts()` (chunking, the GNU parallel body,
the footer) in terms of `directives()`, which each scheduler subclass fills
in with its header. `local` sets `one_job_only`. `container` overrides
`build_scripts()` and submits nothing.

To add a scheduler: write a subclass, add it to `REGISTRY`. The command line
choices and spec validation both come from the registry.

### Environment Variables

All defaults can be overridden via environment variables (prefix `QBATCH_`):
- `QBATCH_SYSTEM`: Scheduler type (pbs, sge, slurm, local, container)
- `QBATCH_PPJ`: Processors per job
- `QBATCH_CHUNKSIZE`: Commands per job chunk
- `QBATCH_CORES`: Parallel commands per job
- `QBATCH_MEM`: Memory request
- `QBATCH_QUEUE`: Queue name
- `QBATCH_SCRIPT_FOLDER`: Where to write generated scripts (default: `.qbatch/`)

### Key Concepts

**Chunking**: Commands are divided into chunks (controlled by `-c`). Each chunk becomes one job submission. Within a job, commands run in parallel using GNU parallel (controlled by `-j`).

**Array vs Individual Jobs**: By default, qbatch creates array jobs when chunks > 1. The `-i` flag submits individual jobs instead.

**Job Dependencies**: The `--depend` option accepts glob patterns or job IDs to wait for before starting new jobs.

**Environment Propagation**: Three modes (via `--env`):
- `copied`: Exports current environment variables into job script
- `batch`: Uses scheduler's native environment propagation (-V, --export=ALL)
- `none`: No environment propagation

## Testing Notes

One test file per module:
- `test/test_schedulers.py`: adapters. Builds specs and checks returned script
  text, dependency resolution (with `subprocess.check_output` monkeypatched),
  and submission (with `subprocess.call` monkeypatched). No disk, no `PATH`.
- `test/test_spec.py`: `JobSpec` defaults, validation, `from_kwargs`.
- `test/test_qbatch.py`: command line integration tests first, then the
  driver and submit phase. The integration tests run `qbatch` as a
  subprocess with `-n`, set `QBATCH_SCRIPT_FOLDER` to a temp directory, and
  run the array scripts with simulated `SGE_TASK_ID`, `PBS_ARRAYID`, or
  `SLURM_ARRAY_TASK_ID`. They need GNU parallel installed.
- `test/conftest.py`: the `make_spec` fixture, which pins every
  environment-derived field so a developer's `QBATCH_*` settings cannot
  change results.

## Version Management

- Version is defined in `pyproject.toml`
- Uses `importlib.metadata` for version retrieval at runtime
- GitHub Actions workflow uses `uv` to build and publish releases to PyPI when a release is created
