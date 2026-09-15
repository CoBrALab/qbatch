#!/usr/bin/env python
import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
from importlib.metadata import version

from qbatch.errors import QbatchError
from qbatch.schedulers import format_hms, scheduler_for
from qbatch.spec import CORES_PATTERN, DEFAULT_LOGDIR, SCHEDULERS, JobSpec


def positive_int(string):
    """Checks agument is a positive integer"""
    msg = "Must be a positive integer"

    try:
        value = int(string)
    except ValueError:
        raise argparse.ArgumentTypeError(msg)

    if value < 1:
        raise argparse.ArgumentTypeError(msg)
    return value


def int_or_percent(string):
    """Checks argument is an integer or integer percentage"""
    if not re.match(CORES_PATTERN, string):
        msg = "Must be an integer or positive integer percentage"
        raise argparse.ArgumentTypeError(msg)
    return string


def submit_scripts(scripts, spec):
    """Submit phase: write job scripts to disk and hand them to the
    scheduler. Owns every side effect. Returns the paths written.
    """
    scheduler = scheduler_for(spec)

    os.makedirs(spec.logdir, exist_ok=True)
    os.makedirs(spec.script_folder, exist_ok=True)

    written = []
    for basename, text in scripts:
        path = os.path.join(spec.script_folder, basename)
        with open(path, "w", encoding="utf-8") as script:
            script.write(text)
        written.append(path)

    # preflight checks, only needed when jobs will actually be submitted
    if not spec.dry_run:
        for binary in scheduler.required_binaries:
            if not shutil.which(binary):
                raise QbatchError(
                    f"qbatch: error: system is {scheduler.name} but {binary} not found"
                )
        # moreutils installs a different program with the same name
        if "parallel" in scheduler.required_binaries:
            result = subprocess.run(
                ["parallel", "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
            )
            if b"GNU parallel" not in result.stdout:
                raise QbatchError("qbatch: error: parallel on PATH is not GNU parallel")

    # execute the job script(s)
    for script in written:
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)
        scheduler.submit(script)
    return written


def _read_task_list(spec):
    """Read the commands to run, and name the job after their source."""
    if spec.task_list:
        return spec.task_list, spec.job_name or "qbatchDriver"

    command_file = spec.command_file
    if command_file[0] == "--":
        if len(command_file) > 1:
            return ([" ".join(command_file[1:])], spec.job_name or command_file[1])
        raise QbatchError("qbatch: error: no command provided as last argument")

    if command_file[0] == "-":
        with open(
            getattr(sys.stdin, "buffer", sys.stdin).fileno(), encoding="utf8"
        ) as reader:
            return reader.readlines(), spec.job_name or "STDIN"

    task_list = []
    job_name = spec.job_name
    for file in command_file:
        if not os.path.isfile(file):
            raise QbatchError(
                f"qbatch: error: command_file {file}"
                + " does not exist or cannot be read"
            )
        with open(file, encoding="utf-8") as reader:
            task_list = task_list + reader.readlines()
        job_name = job_name or os.path.basename(file)
    return task_list, job_name


def qbatchDriver(spec):
    """Read the tasks, resolve dependencies, then generate and submit."""
    for warning in spec.warnings:
        print(warning, file=sys.stderr)
    task_list, job_name = _read_task_list(spec)

    # Drop commented out lines
    spec.task_list = [x for x in task_list if not x.startswith("#")]
    spec.job_name = job_name

    if len(spec.task_list) == 0:
        print("qbatch: warning: No jobs to submit, exiting", file=sys.stderr)
        return

    scheduler = scheduler_for(spec)

    resolution = scheduler.walltime_resolution
    if spec.walltime_seconds and spec.walltime_seconds % resolution:
        print(
            f"qbatch: warning: {scheduler.name} counts whole minutes, --walltime"
            f" {spec.walltime} rounded up to"
            f" {format_hms(spec.walltime_seconds, resolution)}",
            file=sys.stderr,
        )

    # resolve dependency patterns to job ids before the generate phase
    try:
        spec.depend_array_ids, spec.depend_job_ids = scheduler.find_dependencies()
    except Exception as e:
        raise QbatchError(f"qbatch: error: Error matching depend pattern {e!s}") from e
    if spec.depend_array_ids and spec.depend_job_ids:
        # only torque reports both kinds
        print(
            "qbatch: warning: depdendencies on both regular and"
            " array jobs found, this is only supported on"
            " Torque 6.0.2 and above. You may get qsub error"
            " code 168.",
            file=sys.stderr,
        )

    spec.environ = dict(os.environ)

    scripts = scheduler.build_scripts()
    submit_scripts(scripts, spec)


def qbatchParser(args=None):
    try:
        defaults = JobSpec()
    except QbatchError as e:
        sys.exit(str(e))
    __version__ = version("qbatch")

    parser = argparse.ArgumentParser(
        description=f"""Submits a list of commands to a queueing system.
        The list of commands can be broken up into 'chunks' when submitted, so
        that the commands in each chunk run in parallel (using GNU parallel).
        The job script(s) generated by %(prog)s are stored in the folder
        {defaults.script_folder}""",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "command_file",
        nargs=argparse.REMAINDER,
        help="""An input file containing a list of shell commands to be
        submitted, - to read the command list from stdin or -- followed
        by a single command""",
    )
    parser.add_argument(
        "-w",
        "--walltime",
        help="""Maximum walltime for an array job element or individual job:
        seconds (3600), [[HH:]MM:]SS (1:00:00, or 10:00 for 10 minutes),
        D-HH:MM:SS (1-12:00:00), or units d, h, m and s (1d12h, 2h30m, 90m).
        A number with no unit is seconds. To not set a walltime, give 0 or
        none""",
    )
    parser.add_argument(
        "-c",
        "--chunksize",
        default=argparse.SUPPRESS,
        type=int,
        help="""Number of commands from the command list that are wrapped into
        each job (default: $QBATCH_CHUNKSIZE, else --ppj)""",
    )
    parser.add_argument(
        "-j",
        "--cores",
        default=argparse.SUPPRESS,
        type=int_or_percent,
        help="""Number of commands each job runs in parallel. If the chunk size
        (-c) is smaller than -j then only chunk size commands will run in
        parallel. This option can also be expressed as a percentage (e.g.
        100%%) of the total available cores (default: $QBATCH_CORES, else
        --ppj)""",
    )
    parser.add_argument(
        "--ppj",
        default=defaults.ppj,
        type=positive_int,
        help="""Requested number of processors per job (aka ppn on PBS,
        slots on SGE, cpus per task on SLURM). Cores can be over subscribed
        if -j is larger than --ppj
        (useful to make use of hyper-threading on some systems)""",
    )
    parser.add_argument(
        "-N",
        "--jobname",
        action="store",
        help="""Set job name (defaults to name of command file, or STDIN)""",
    )
    parser.add_argument(
        "--mem",
        default=defaults.mem,
        help="""Memory required for each job, as a number and a unit (e.g.
        --mem 4G, --mem 1.5GB, --mem 512M). Units are K, M, G, T and P, with
        or without B, and are powers of 1024. A number with no unit is GB.
        This value is set on each variable specified in --memvars. To not set
        any memory requirement, give 0 or none""",
    )
    parser.add_argument(
        "-q",
        "--queue",
        default=defaults.queue,
        help="""Name of queue to submit jobs to (defaults to no queue)""",
    )

    parser.add_argument(
        "-n",
        "--dryrun",
        action="store_true",
        help="Dry run; Create jobfiles but do not submit or run any commands",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--version", action="version", version=__version__)

    group = parser.add_argument_group("advanced options")
    group.add_argument(
        "--depend",
        action="append",
        help="""Wait for successful completion of job(s) with name matching
        given glob pattern or job id matching given job id(s) before
        starting""",
    )
    group.add_argument(
        "-d", "--workdir", default=defaults.workdir, help="Job working directory"
    )
    group.add_argument(
        "--logdir",
        action="store",
        default=DEFAULT_LOGDIR,
        help="""Directory to save store log files""",
    )
    group.add_argument(
        "-o",
        "--options",
        action="append",
        default=defaults.options,
        help="""Custom options passed directly to the queuing system (e.g
        --options "-l vf=8G". This option can be given multiple times""",
    )
    group.add_argument(
        "--header",
        action="append",
        help="""A line to insert verbatim at the start of the script, and will
        be run once per job. This option can be given multiple times""",
    )
    group.add_argument(
        "--footer",
        action="append",
        help="""A line to insert verbatim at the end of the script, and will
        be run once per job. This option can be given multiple times""",
    )
    group.add_argument(
        "--nodes",
        default=defaults.nodes,
        type=positive_int,
        help="(PBS and SLURM only) Nodes to request per job",
    )
    group.add_argument(
        "--sge-pe",
        default=defaults.sge_pe,
        help="""(SGE-only) The parallel environment to use if more than one
        processor per job is requested""",
    )
    group.add_argument(
        "--memvars",
        default=defaults.memvars,
        help="""A comma-separated list of variables to set with the memory
        limit given by the --mem option (e.g. --memvars=h_vmem,vf)""",
    )
    group.add_argument(
        "--pbs-nodes-spec",
        action="append",
        help="(PBS-only) String to be inserted into nodes= line of job",
    )
    group.add_argument(
        "-i",
        "--individual",
        action="store_true",
        help="Submit individual jobs instead of an array job",
    )
    group.add_argument(
        "-b",
        "--system",
        default=defaults.scheduler,
        choices=SCHEDULERS,
        help="""The type of queueing system to use. 'pbs' and 'sge' both make
        calls to qsub to submit jobs. 'slurm' calls sbatch.
        'local' runs the entire command list (without chunking) locally.
        'container' creates a joblist and metadata file, to pass commands out
        of a container to a monitoring process for submission to a
        batch system.""",
    )
    group.add_argument(
        "--env",
        choices=["copied", "batch", "none"],
        default=defaults.env,
        help="""Determines how your environment is propagated when your
              job runs. "copied" records your environment settings in
              the job submission script, "batch" uses the cluster's
              mechanism for propagating your environment, and "none"
              does not propagate any environment variables.""",
    )
    group.add_argument(
        "--shell",
        default=defaults.shell,
        help="""Shell to use for spawning jobs
        and launching single commands""",
    )
    group.add_argument(
        "--block",
        action="store_true",
        help="""For SGE, PBS and SLURM, blocks execution until jobs are
        finished.""",
    )
    group.add_argument(
        "--script-folder",
        default=defaults.script_folder,
        help="""Directory where job scripts are stored""",
    )

    args = parser.parse_args(args)
    if not args.command_file:
        parser.print_usage()
        sys.exit("qbatch: error: no command file or command provided")
    try:
        spec = JobSpec.from_kwargs(
            container_meta=" ".join(sys.argv[1:-1]), **vars(args)
        )
        qbatchDriver(spec)
    except QbatchError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    qbatchParser()
