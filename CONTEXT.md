# qbatch

Runs many shell commands in parallel on HPC clusters by turning them into batch jobs, hiding the differences between job schedulers behind one command-line interface.

## Language

**Task**:
One shell command line taken from the input (a command file, stdin, or a caller-supplied list). The unit of work.
_Avoid_: command, line, job (a job contains tasks)

**Chunk**:
A group of tasks that runs inside a single job, in parallel up to the cores limit.
_Avoid_: batch, group

**Job**:
One submission to a scheduler; it executes one chunk.
_Avoid_: task (a task is inside a job)

**Array job**:
A single submission whose numbered elements each execute one chunk. The default when there is more than one chunk.
_Avoid_: job array, task array

**Job script**:
The generated shell script for a job: scheduler directives, environment setup, then the chunk's tasks.
_Avoid_: batch script, submission script

**Scheduler**:
The batch system a job script is written for and handed to: PBS/Torque, SGE, Slurm, local execution, or container mode.
_Avoid_: system, backend, cluster type

**Directive**:
A scheduler-specific header line in a job script that requests resources or behaviour (`#PBS`, `#$`, `#SBATCH`).
_Avoid_: header option, pragma

**Generate phase**:
The step that turns tasks and settings into job-script text. It produces text only and reads nothing from the machine it runs on.
_Avoid_: build, render

**Submit phase**:
The step that writes job scripts to disk and hands them to the scheduler. Every side effect belongs to it.
_Avoid_: execute, launch

**Dependency**:
An existing job, matched by name pattern or ID, that must finish successfully before a new job may start.
_Avoid_: hold, prerequisite

**Dry run**:
Generate and write job scripts but submit nothing. Needs no scheduler binaries on the machine.
_Avoid_: no-op mode, preview
