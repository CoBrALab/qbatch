"""The job spec: everything qbatch needs to generate and submit jobs.

Defaults come from the QBATCH_* environment variables and are read every
time a spec is constructed, so two specs built under different
environments never share state.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields
from decimal import ROUND_CEILING, Decimal

from qbatch.errors import QbatchError
from qbatch.schedulers import REGISTRY, format_mem

SCHEDULERS = tuple(REGISTRY)
ENV_MODES = ("copied", "batch", "none")

# an integer, or an integer percentage
CORES_PATTERN = r"^([-+]?\d+|^\d+%)$"

# {workdir} is substituted when the spec is built
DEFAULT_LOGDIR = "{workdir}/logs"

# a number and an optional unit, case does not matter: 4G, 1.5gb, 512MiB
MEM_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:([kmgtp])i?b?)?$", re.IGNORECASE)
# each unit as a power of 1024 of a MiB; a plain number is GiB
MEM_POWERS = {"k": -1, "m": 0, "g": 1, "t": 2, "p": 3}

# argparse dest names that differ from the JobSpec field they set
_ARGPARSE_NAMES = {
    "chunksize": "chunk_size",
    "jobname": "job_name",
    "dryrun": "dry_run",
    "system": "scheduler",
}


def parse_mem(text):
    """Read a --mem value. Returns (mib, warning): mib is None for no
    request, and warning is None unless the value changed."""
    text = "" if text is None else str(text).strip()
    if text.lower() in ("", "none"):
        return None, None
    match = MEM_PATTERN.match(text)
    if not match:
        raise QbatchError(
            f"qbatch: error: cannot read --mem {text}, expected a number and"
            " a unit, for example 4G or 1536M"
        )
    number, unit = match.groups()
    exact = Decimal(number) * Decimal(1024) ** MEM_POWERS[(unit or "g").lower()]
    if exact == 0:
        return None, None
    mib = int(exact.to_integral_value(rounding=ROUND_CEILING))
    if unit is None:
        return (
            mib,
            f"qbatch: warning: --mem {text} has no unit, using {format_mem(mib)}",
        )
    if mib != exact:
        return mib, f"qbatch: warning: --mem {text} rounded up to {format_mem(mib)}"
    return mib, None


def _env_ppj():
    return int(os.environ.get("QBATCH_PPJ", "1"))


def _env_options():
    return [os.environ["QBATCH_OPTIONS"]] if os.environ.get("QBATCH_OPTIONS") else []


@dataclass
class JobSpec:
    """Everything needed to generate and submit a set of jobs."""

    # what to run
    task_list: list[str] | None = None
    command_file: list[str] | None = None
    job_name: str | None = None

    # how it is divided into chunks
    # None means QBATCH_CHUNKSIZE or QBATCH_CORES, else ppj
    chunk_size: int | None = None
    cores: int | str | None = None
    ppj: int = field(default_factory=_env_ppj)
    individual: bool = False

    # what is requested from the scheduler
    scheduler: str = field(
        default_factory=lambda: os.environ.get("QBATCH_SYSTEM", "local")
    )
    queue: str | None = field(default_factory=lambda: os.environ.get("QBATCH_QUEUE"))
    nodes: int = field(default_factory=lambda: int(os.environ.get("QBATCH_NODES", "1")))
    sge_pe: str = field(default_factory=lambda: os.environ.get("QBATCH_SGE_PE", "smp"))
    pbs_nodes_spec: list[str] | None = None
    walltime: str | None = None
    mem: str | None = field(default_factory=lambda: os.environ.get("QBATCH_MEM"))
    memvars: str = field(
        default_factory=lambda: os.environ.get("QBATCH_MEMVARS", "mem")
    )
    options: list[str] = field(default_factory=_env_options)
    block: bool = False

    # what goes into the job script
    shell: str = field(
        default_factory=lambda: os.environ.get("QBATCH_SHELL", "/bin/sh")
    )
    header: list[str] | None = None
    footer: list[str] | None = None
    env: str = "copied"
    environ: dict[str, str] = field(default_factory=dict)
    container_meta: str = ""

    # where things are written
    workdir: str = field(default_factory=os.getcwd)
    logdir: str = DEFAULT_LOGDIR
    script_folder: str = field(
        default_factory=lambda: os.environ.get("QBATCH_SCRIPT_FOLDER", ".qbatch/")
    )

    # dependencies: patterns, and the job ids they resolve to
    depend: list[str] | None = None
    depend_array_ids: list[str] = field(default_factory=list)
    depend_job_ids: list[str] = field(default_factory=list)

    verbose: bool = False
    dry_run: bool = False

    # set by __post_init__, not by the caller
    warnings: list[str] = field(init=False, default_factory=list, repr=False)

    def __post_init__(self):
        if self.scheduler not in SCHEDULERS:
            raise QbatchError(
                "qbatch: error: unknown system {}, expected one of {}".format(
                    self.scheduler, ", ".join(SCHEDULERS)
                )
            )
        if self.env not in ENV_MODES:
            raise QbatchError(
                "qbatch: error: unknown env mode {}, expected one of {}".format(
                    self.env, ", ".join(ENV_MODES)
                )
            )
        if int(self.ppj) < 1:
            raise QbatchError(
                f"qbatch: error: ppj must be a positive integer, got {self.ppj}"
            )
        if self.chunk_size is None:
            self.chunk_size = int(os.environ.get("QBATCH_CHUNKSIZE", self.ppj))
        if self.cores is None:
            self.cores = os.environ.get("QBATCH_CORES", int(self.ppj))
        if int(self.chunk_size) < 0:
            raise QbatchError(
                f"qbatch: error: chunk size cannot be negative, got {self.chunk_size}"
            )
        if isinstance(self.cores, str) and not re.match(CORES_PATTERN, self.cores):
            raise QbatchError(
                "qbatch: error: cores must be an integer or integer"
                f" percentage, got {self.cores}"
            )
        _, warning = parse_mem(self.mem)
        if warning:
            self.warnings.append(warning)
        self.logdir = self.logdir.format(workdir=self.workdir)

    # read from mem when used, so a later change to spec.mem is seen
    @property
    def mem_mib(self):
        """The memory request in MiB, or None for no request."""
        return parse_mem(self.mem)[0]

    @classmethod
    def from_kwargs(cls, **kwargs):
        """Build a spec from argparse-style key-value pairs.

        Accepts the option names used by the command line parser, so
        callers of the pre-3.0 qbatchDriver(**kwargs) interface can
        migrate with qbatchDriver(JobSpec.from_kwargs(**options)).
        """
        values = {_ARGPARSE_NAMES.get(k, k): v for k, v in kwargs.items()}
        unknown = set(values) - {f.name for f in fields(cls) if f.init}
        if unknown:
            raise QbatchError(
                "qbatch: error: unknown option(s) {}".format(", ".join(sorted(unknown)))
            )
        return cls(**values)
