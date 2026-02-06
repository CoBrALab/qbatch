# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

qbatch is a command-line tool and Python library for executing shell commands in parallel on high-performance computing clusters. It abstracts differences between various job schedulers (PBS/Torque, SGE, Slurm) and provides a unified interface for submitting batch jobs.

## Common Development Commands

### Testing
```bash
# Run all tests
nosetests test/test_qbatch.py

# Run a specific test
nosetests test/test_qbatch.py:test_run_qbatch_local_piped_commands

# Run with verbose output
nosetests -v test/test_qbatch.py
```

### Building
```bash
# Build source distribution and wheel
python setup.py sdist bdist_wheel

# Build for local testing
pip install -e .
```

### Installation
```bash
# Install from source
pip install .

# Install with testing dependencies
pip install -r requirements-testing.txt
```

## Architecture

### Core Components

The codebase is intentionally simple, with all logic contained in a single main file:

- **qbatch/qbatch.py** (777 lines): Contains all functionality
- **qbatch/__init__.py**: Package exports

### Key Functions

**`qbatchParser(args=None)`** (line 645-772)
- Argument parser using argparse
- Parses command-line options and environment variables
- Calls `qbatchDriver()` with parsed arguments

**`qbatchDriver(**kwargs)`** (line 341-642)
- Main driver function that orchestrates job submission
- Accepts either a command file or a `task_list` (list of command strings)
- Generates job scripts based on the selected scheduler system
- Supports "chunking" commands into groups, each running in parallel via GNU parallel

**System-specific functions:**
- `pbs_find_jobs(patterns)` (line 238-285): Finds PBS/Torque jobs using qstat XML output
- `slurm_find_jobs(patterns)` (line 288-317): Finds Slurm jobs using squeue
- `compute_threads(ppj, ncores)` (line 228-235): Calculates threads per command

### Templates (lines 76-155)

The system uses template strings for generating job scheduler headers:
- `PBS_HEADER_TEMPLATE`: PBS/Torque job scripts
- `SGE_HEADER_TEMPLATE`: Grid Engine job scripts
- `SLURM_HEADER_TEMPLATE`: Slurm job scripts
- `LOCAL_TEMPLATE`: Local execution using GNU parallel
- `CONTAINER_TEMPLATE`: For containerized environments

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

Tests use `nosetests` and rely on:
- Setting `QBATCH_SCRIPT_FOLDER` to a temp directory
- Testing dry-run mode (`-n`) to avoid actual job submission
- Simulating scheduler environment variables (e.g., `SGE_TASK_ID`, `PBS_ARRAYID`)
- Using Python 2/3 compatibility via `future` library

Tests are integration-style, generating actual job scripts and verifying they produce expected output when executed.

## Python 2/3 Compatibility

The codebase maintains Python 2.7+ compatibility using:
- `future` library for standard library aliases
- Custom `_EnvironDict` class for UTF-8 environment variable handling on Python 2
- `io.open()` for UTF-8 file handling
- Careful string/unicode handling

## Version Management

- Version is defined in `setup.py` (line 14)
- Uses `importlib.metadata` for version retrieval at runtime (line 9)
- GitHub Actions workflow publishes releases to PyPI when a release is created
