#!/usr/bin/env python
"""The job spec: everything qbatch needs to generate and submit jobs.

Defaults come from the QBATCH_* environment variables and are read every
time a spec is constructed, so two specs built under different
environments never share state.
"""
import os
import re
from dataclasses import dataclass, field, fields
from typing import Dict, List, Optional, Union

SCHEDULERS = ('pbs', 'sge', 'slurm', 'local', 'container')
ENV_MODES = ('copied', 'batch', 'none')

# an integer, or an integer percentage
CORES_PATTERN = r"^([-+]?\d+|^\d+%)$"

# {workdir} is substituted when the spec is built
DEFAULT_LOGDIR = "{workdir}/logs"

# argparse dest names that differ from the JobSpec field they set
_ARGPARSE_NAMES = {
    'chunksize': 'chunk_size',
    'jobname': 'job_name',
    'dryrun': 'dry_run',
    'system': 'scheduler',
}


class QbatchError(Exception):
    """User-facing error from any qbatch phase"""


def _env_ppj():
    return int(os.environ.get("QBATCH_PPJ", "1"))


def _env_chunk_size():
    return int(os.environ.get("QBATCH_CHUNKSIZE",
                              os.environ.get("QBATCH_PPJ", "1")))


def _env_cores():
    return os.environ.get("QBATCH_CORES", os.environ.get("QBATCH_PPJ", "1"))


def _env_options():
    return ([os.environ["QBATCH_OPTIONS"]]
            if os.environ.get("QBATCH_OPTIONS") else [])


@dataclass
class JobSpec:
    """Everything needed to generate and submit a set of jobs."""

    # what to run
    task_list: Optional[List[str]] = None
    command_file: Optional[List[str]] = None
    job_name: Optional[str] = None

    # how it is divided into chunks
    chunk_size: int = field(default_factory=_env_chunk_size)
    cores: Union[int, str] = field(default_factory=_env_cores)
    ppj: int = field(default_factory=_env_ppj)
    individual: bool = False

    # what is requested from the scheduler
    scheduler: str = field(
        default_factory=lambda: os.environ.get("QBATCH_SYSTEM", "local"))
    queue: Optional[str] = field(
        default_factory=lambda: os.environ.get("QBATCH_QUEUE"))
    nodes: int = field(
        default_factory=lambda: int(os.environ.get("QBATCH_NODES", "1")))
    sge_pe: str = field(
        default_factory=lambda: os.environ.get("QBATCH_SGE_PE", "smp"))
    pbs_nodes_spec: Optional[List[str]] = None
    walltime: Optional[str] = None
    mem: str = field(
        default_factory=lambda: os.environ.get("QBATCH_MEM", "0"))
    memvars: str = field(
        default_factory=lambda: os.environ.get("QBATCH_MEMVARS", "mem"))
    options: List[str] = field(default_factory=_env_options)
    block: bool = False

    # what goes into the job script
    shell: str = field(
        default_factory=lambda: os.environ.get("QBATCH_SHELL", "/bin/sh"))
    header: Optional[List[str]] = None
    footer: Optional[List[str]] = None
    env: str = 'copied'
    environ: Dict[str, str] = field(default_factory=dict)
    container_meta: str = ''

    # where things are written
    workdir: str = field(default_factory=os.getcwd)
    logdir: str = DEFAULT_LOGDIR
    script_folder: str = field(
        default_factory=lambda: os.environ.get("QBATCH_SCRIPT_FOLDER",
                                               ".qbatch/"))

    # dependencies: patterns, and the job ids they resolve to
    depend: Optional[List[str]] = None
    depend_array_ids: List[str] = field(default_factory=list)
    depend_job_ids: List[str] = field(default_factory=list)

    verbose: bool = False
    dry_run: bool = False

    def __post_init__(self):
        if self.scheduler not in SCHEDULERS:
            raise QbatchError(
                "qbatch: error: unknown system {0}, expected one of"
                " {1}".format(self.scheduler, ', '.join(SCHEDULERS)))
        if self.env not in ENV_MODES:
            raise QbatchError(
                "qbatch: error: unknown env mode {0}, expected one of"
                " {1}".format(self.env, ', '.join(ENV_MODES)))
        if int(self.ppj) < 1:
            raise QbatchError(
                "qbatch: error: ppj must be a positive integer,"
                " got {0}".format(self.ppj))
        if int(self.chunk_size) < 0:
            raise QbatchError(
                "qbatch: error: chunk size cannot be negative,"
                " got {0}".format(self.chunk_size))
        if isinstance(self.cores, str) and not re.match(CORES_PATTERN,
                                                        self.cores):
            raise QbatchError(
                "qbatch: error: cores must be an integer or integer"
                " percentage, got {0}".format(self.cores))
        self.logdir = self.logdir.format(workdir=self.workdir)

    @classmethod
    def from_kwargs(cls, **kwargs):
        """Build a spec from argparse-style key-value pairs.

        Accepts the option names used by the command line parser, so
        callers of the pre-3.0 qbatchDriver(**kwargs) interface can
        migrate with qbatchDriver(JobSpec.from_kwargs(**options)).
        """
        values = {_ARGPARSE_NAMES.get(k, k): v for k, v in kwargs.items()}
        unknown = set(values) - {f.name for f in fields(cls)}
        if unknown:
            raise QbatchError("qbatch: error: unknown option(s) {0}".format(
                ', '.join(sorted(unknown))))
        return cls(**values)
