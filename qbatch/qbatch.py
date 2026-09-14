#!/usr/bin/env python
import argparse
import fnmatch
import math
import os
import re
import shutil
import stat
import subprocess
import sys
from importlib.metadata import version
from textwrap import dedent

from qbatch.spec import CORES_PATTERN, DEFAULT_LOGDIR, SCHEDULERS, JobSpec, QbatchError

# environment vars to ignore when copying the environment to the job script
IGNORE_ENV_VARS = [
    "PWD",
    "SGE_TASK_ID",
    "PBS_ARRAYID",
    "ARRAY_IND",
    "BASH_FUNC_*",
    "TMP",
    "TMPDIR",
]

PBS_HEADER_TEMPLATE = dedent(
    """\
    #!{shell}
    #PBS -S {shell}
    #PBS -l nodes={nodes}:{nodes_spec}ppn={ppj}
    #PBS -j oe
    #PBS -o {logdir}
    #PBS -d {workdir}
    #PBS -N {job_name}
    #PBS {o_memopts}
    #PBS {o_queue}
    #PBS {o_array}
    #PBS {o_walltime}
    #PBS {o_dependencies}
    #PBS {o_options}
    #PBS {o_env}
    #PBS {o_block}
    {env}
    {header_commands}
    ARRAY_IND=$PBS_ARRAYID
    """
)

SGE_HEADER_TEMPLATE = dedent(
    """\
    #!{shell}
    #$ -S {shell}
    #$ {o_ppj}
    #$ -j y
    #$ -o {logdir}
    #$ -wd {workdir}
    #$ -N {job_name}
    #$ {o_memopts}
    #$ {o_queue}
    #$ {o_array}
    #$ {o_walltime}
    #$ {o_dependencies}
    #$ {o_options}
    #$ {o_env}
    #$ {o_block}
    {env}
    {header_commands}
    ARRAY_IND=$SGE_TASK_ID
    """
)

SLURM_HEADER_TEMPLATE = dedent(
    """\
    #!{shell}
    #SBATCH --nodes={nodes}
    #SBATCH {o_ppj}
    #SBATCH {logfile}
    #SBATCH -D {workdir}
    #SBATCH --job-name={job_name}
    #SBATCH {o_memopts}
    #SBATCH {o_queue}
    #SBATCH {o_array}
    #SBATCH {o_walltime}
    #SBATCH {o_dependencies}
    #SBATCH {o_options}
    #SBATCH {o_env}
    #SBATCH {o_block}
    {env}
    {header_commands}
    ARRAY_IND=$SLURM_ARRAY_TASK_ID
    """
)

LOCAL_TEMPLATE = dedent(
    """\
    #!{shell}
    {env}
    {header_commands}
    cd {workdir}
    """
)


def run_command(command, logfile=None):
    # Run command and collect stdout
    # http://blog.endpoint.com/2015/01/getting-realtime-output-using-python.html
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    if logfile:
        filehandle = open(logfile, "w", encoding="utf-8")
    while True:
        output = process.stdout.readline().decode("utf-8").strip()
        if output == "" and process.poll() is not None:
            break
        if output and logfile:
            filehandle.write(output)
            filehandle.write("\n")
        print(output)
    rc = process.poll()
    if logfile:
        filehandle.close()
    return rc


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


def compute_threads(spec):
    """Computes either number cores per job available"""
    ppj = spec.ppj or 1
    cores = spec.cores
    if isinstance(cores, str) and cores.endswith("%"):
        return int(math.floor(int(ppj) * float(cores.strip("%")) / 100))
    return int(ppj) // int(cores)


def pbs_find_jobs(spec):
    """Finds jobs with names matching the spec's depend patterns

    Returns a list of job IDs.

    Raises an Exception if there is an error running the 'qstat' command or
    parsing its output.
    """
    patterns = spec.depend
    if not patterns:
        return [], []

    if isinstance(patterns, str):
        patterns = [patterns]

    import xml.etree.ElementTree as ET

    output = subprocess.check_output(["qstat", "-x"])
    if not output:
        print(
            "qbatch: warning: Dependencies specified but no running jobs found",
            file=sys.stderr,
        )
        return [], []
    tree = ET.fromstring(output)

    array_matches = []
    regular_matches = []
    for job in tree:
        jobid = job.find("Job_Id").text
        name = job.find("Job_Name").text
        state = job.find("job_state").text

        # ignore completed or errored jobs
        if state in ["C", "E"]:
            continue

        for pattern in patterns:
            if fnmatch.fnmatch(name, pattern):
                if fnmatch.fnmatch(jobid, "*[[][]]*"):
                    array_matches.append(jobid)
                else:
                    regular_matches.append(jobid)
            if fnmatch.fnmatch(jobid, pattern):
                if fnmatch.fnmatch(jobid, "*[[][]]*"):
                    array_matches.append(jobid)
                else:
                    regular_matches.append(jobid)
    return array_matches, regular_matches


def slurm_find_jobs(spec):
    """Finds jobs with names matching the spec's depend patterns
    Returns a list of job IDs.
    Raises an Exception if there is an error running the 'squeue' command or
    parsing its output.
    """
    patterns = spec.depend
    if not patterns:
        return []

    if isinstance(patterns, str):
        patterns = [patterns]

    output = subprocess.check_output(
        [
            "squeue",
            "-h",
            "--user={}".format(os.environ.get("USER")),
            "--states=PD,R,S,CF",
            "--format=%j %A",
        ]
    ).decode("utf-8")
    if not output:
        print(
            "qbatch: warning: Dependencies specified but no running jobs found",
            file=sys.stderr,
        )
        return []

    regular_matches = []
    for line in output.split("\n"):
        for pattern in patterns:
            # ignore completed jobs
            if re.search(pattern, line):
                jobid = line.split()[1]
                regular_matches.append(jobid)
    return regular_matches


def generate_scripts(spec):
    """Generate phase: turn a job spec into job script text.

    Returns a list of (basename, text) pairs. Produces text only: no
    filesystem, process, or scheduler access.
    """
    task_list = spec.task_list
    job_name = spec.job_name
    chunk_size = spec.chunk_size
    scheduler = spec.scheduler
    walltime = spec.walltime
    use_array = not spec.individual
    mem = spec.mem != "0" and spec.mem or None
    memvars = spec.memvars.split(",")
    mem_string = ",".join([f"{var}={mem}" for var in memvars])
    header_commands = spec.header and "\n".join(spec.header) or ""
    footer_commands = spec.footer and "\n".join(spec.footer) or ""
    nodes_spec = (spec.pbs_nodes_spec and ":".join(spec.pbs_nodes_spec) + ":") or ""

    # compute the number of jobs needed. This will be the number of elements in
    # the array job
    if scheduler == "local" or chunk_size == 0:
        use_array = False
        num_jobs = 1
        chunk_size = sys.maxsize
    elif len(task_list) <= chunk_size:
        use_array = False
        num_jobs = 1
        if spec.verbose:
            print(
                "Number of commands less than chunk size, "
                "building single non-array job",
                file=sys.stderr,
            )
    else:
        num_jobs = int(math.ceil(len(task_list) / float(chunk_size)))

    if scheduler == "container":
        # collected by an external monitor, so no scheduler header
        return [
            (job_name + ".joblist", "".join(task_list)),
            (job_name + ".meta", spec.container_meta),
        ]

    # copy the current environment
    env_exports = ""
    if spec.env == "copied":
        env_exports = "\n".join(
            [
                'export {0}="{1}"'.format(k, v.replace('"', r"\""))
                for k, v in list(spec.environ.items())
                if not any(fnmatch.fnmatch(k, pattern) for pattern in IGNORE_ENV_VARS)
            ]
        )
        env_exports = env_exports.replace("$", "$$")
        env_exports = f"# -- start copied env\n{env_exports}\n# -- end copied env"

    # placeholders every scheduler template shares
    common = {
        "shell": spec.shell,
        "logdir": spec.logdir,
        "workdir": spec.workdir,
        "job_name": job_name,
        "env": env_exports,
        "header_commands": header_commands,
    }

    if scheduler == "pbs":
        o_dependencies = ""
        if spec.depend_array_ids or spec.depend_job_ids:
            o_dependencies = "-W depend="
            if spec.depend_job_ids:
                o_dependencies += "afterok:" + ":".join(spec.depend_job_ids)
            if spec.depend_array_ids and spec.depend_job_ids:
                o_dependencies += ","
            if spec.depend_array_ids:
                o_dependencies += "afterokarray:" + ":".join(spec.depend_array_ids)

        header = PBS_HEADER_TEMPLATE.format(
            nodes=spec.nodes,
            nodes_spec=nodes_spec,
            ppj=spec.ppj,
            o_array=use_array and f"-t 1-{num_jobs}" or "",
            o_walltime=walltime and f"-l walltime={walltime}" or "",
            o_dependencies=o_dependencies,
            o_options="\n#PBS ".join(spec.options),
            o_memopts=(mem and mem_string) and f"-l {mem_string}" or "",
            o_env=(spec.env == "batch") and "-V" or "",
            o_queue=spec.queue and f"-q {spec.queue}" or "",
            o_block=spec.block and " -Wblock=true" or "",
            **common,
        )

    elif scheduler == "sge":
        o_dependencies = (
            spec.depend and "-hold_jid '" + "','".join(spec.depend) + "'" or ""
        )

        header = SGE_HEADER_TEMPLATE.format(
            o_ppj=(spec.ppj > 1) and f"-pe {spec.sge_pe} {spec.ppj}" or "",
            o_array=use_array and f"-t 1-{num_jobs}" or "",
            o_walltime=walltime and f"-l h_rt={walltime}" or "",
            o_dependencies=o_dependencies,
            o_options="\n#$ ".join(spec.options),
            o_memopts=(mem and mem_string) and f"-l {mem_string}" or "",
            o_env=(spec.env == "batch") and "-V" or "",
            o_queue=spec.queue and f"-q {spec.queue}" or "",
            o_block=spec.block and " -sync y" or "",
            **common,
        )

    elif scheduler == "slurm":
        if walltime and walltime.find(":") > 0:
            o_walltime = f"--time={walltime}"
        elif walltime:
            o_walltime = f"--time={int(walltime) / 60:1.0f}"
        else:
            o_walltime = ""

        logfile = (
            use_array
            and f"--output={spec.logdir}/slurm-{job_name}-%A_%a.out"
            or f"--output={spec.logdir}/slurm-{job_name}-%j.out"
        )

        header = SLURM_HEADER_TEMPLATE.format(
            nodes=spec.nodes,
            o_ppj=(spec.ppj > 1) and f"--cpus-per-task={spec.ppj}" or "",
            logfile=logfile,
            o_array=use_array and f"--array=1-{num_jobs}" or "",
            o_walltime=o_walltime,
            o_dependencies="--dependency=afterok:" + ":".join(spec.depend_job_ids)
            if spec.depend_job_ids
            else "",
            o_options="\n#SBATCH ".join(spec.options),
            o_memopts=(mem and mem_string) and f"--{mem_string}" or "",
            o_env=(spec.env == "batch") and "--export=ALL" or "--export=NONE",
            o_queue=spec.queue and f"--partition={spec.queue}" or "",
            o_block=spec.block and " --wait" or "",
            **common,
        )

    elif scheduler == "local":
        header = LOCAL_TEMPLATE.format(**common)

    else:
        raise QbatchError(f"qbatch: error: unknown system {scheduler}")

    # emit job script text
    scripts = []
    if use_array:
        script_lines = [
            header,
            'command -v parallel > /dev/null 2>&1 || { echo "GNU parallel '
            'not found in job environment. Exiting."; exit 1; }',
            f"CHUNK_SIZE={chunk_size}",
            f"CORES={spec.cores}",
            f"export THREADS_PER_COMMAND={compute_threads(spec)}",
            'sed -n "$(( (${ARRAY_IND} - 1) * ${CHUNK_SIZE} + 1 )),'
            "+$(( ${CHUNK_SIZE} - 1 ))p\" << 'EOF' | parallel -j${CORES}"
            " --tag --line-buffer --compress",
            "".join(task_list),
            "EOF",
        ]

        text = "\n".join(script_lines)
        if footer_commands:
            text += "\n" + footer_commands
        scripts.append((job_name + ".array", text))
    else:
        for chunk in range(num_jobs):
            if len(task_list) == 1:
                script_lines = [
                    header,
                    f"export THREADS_PER_COMMAND={compute_threads(spec)}",
                    "".join(task_list),
                ]
            else:
                script_lines = [
                    header,
                    'command -v parallel > /dev/null 2>&1 || { echo "GNU'
                    ' parallel not found in job environment. Exiting.";'
                    " exit 1; }",
                    f"CORES={spec.cores}",
                    f"export THREADS_PER_COMMAND={compute_threads(spec)}",
                    "parallel -j${CORES} --tag --line-buffer --compress << 'EOF'",
                    "".join(
                        task_list[chunk * chunk_size : chunk * chunk_size + chunk_size]
                    ),
                    "EOF",
                ]
            text = "\n".join(script_lines)
            if footer_commands:
                text += "\n" + footer_commands
            scripts.append((f"{job_name}.{chunk}", text))

    return scripts


def submit_scripts(scripts, spec):
    """Submit phase: write job scripts to disk and hand them to the
    scheduler. Owns every side effect. Returns the paths written.
    """
    scheduler = spec.scheduler

    os.makedirs(spec.logdir, exist_ok=True)
    os.makedirs(spec.script_folder, exist_ok=True)

    written = []
    for basename, text in scripts:
        path = os.path.join(spec.script_folder, basename)
        with open(path, "w", encoding="utf-8") as script:
            script.write(text)
        written.append(path)

    if scheduler == "container":
        # container scripts are collected by an external monitor,
        # nothing to submit
        return written

    # preflight checks, only needed when jobs will actually be submitted
    if not spec.dry_run:
        if scheduler == "slurm":
            if not shutil.which("sbatch"):
                raise QbatchError("qbatch: error: system is slurm but sbatch not found")
            if not shutil.which("squeue"):
                raise QbatchError("qbatch: error: system is slurm but squeue not found")
        elif (scheduler == "pbs") or (scheduler == "sge"):
            if not shutil.which("qsub"):
                raise QbatchError("qbatch: error: system is pbs/sge but qsub not found")
            if not shutil.which("qstat"):
                raise QbatchError(
                    "qbatch: error: system is pbs/sge but qstat not found"
                )
        if not shutil.which("parallel"):
            raise QbatchError("qbatch: error: gnu-parallel not found")

    # execute the job script(s)
    for script in written:
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)
        if scheduler == "sge" or scheduler == "pbs":
            if spec.verbose:
                print(f"Running: qsub {script}")
            if spec.dry_run:
                continue
            return_code = subprocess.call(["qsub", script])
            if return_code:
                raise QbatchError(
                    f"qbatch: error: qsub call returned error code {return_code}"
                )
        elif scheduler == "slurm":
            if spec.verbose:
                print(f"Running: sbatch {script}")
            if spec.dry_run:
                continue
            return_code = subprocess.call(["sbatch", script])
            if return_code:
                raise QbatchError(
                    f"qbatch: error: sbatch call returned error code {return_code}"
                )
        elif scheduler == "local":
            logfile = f"{spec.logdir}/{spec.job_name}.log"
            if spec.verbose:
                print(f"Launching jobscript. Output to {logfile}")
            if spec.dry_run:
                continue
            return_code = run_command(script, logfile=logfile)
            if return_code:
                raise QbatchError(
                    f"qbatch: error: local run call returned error code {return_code}"
                )
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
        task_list = task_list + open(file, "r", encoding="utf-8").readlines()
        job_name = job_name or os.path.basename(file)
    return task_list, job_name


def qbatchDriver(spec):
    """Read the tasks, resolve dependencies, then generate and submit."""
    task_list, job_name = _read_task_list(spec)

    # Drop commented out lines
    spec.task_list = [x for x in task_list if not x.startswith("#")]
    spec.job_name = job_name

    if len(spec.task_list) == 0:
        print("qbatch: warning: No jobs to submit, exiting", file=sys.stderr)
        return

    # resolve dependency patterns to job ids before the generate phase
    if spec.scheduler == "pbs":
        try:
            spec.depend_array_ids, spec.depend_job_ids = pbs_find_jobs(spec)
        except Exception as e:
            raise QbatchError(f"qbatch: error: Error matching depend pattern {e!s}")
        if spec.depend_array_ids and spec.depend_job_ids:
            print(
                "qbatch: warning: depdendencies on both regular and"
                " array jobs found, this is only supported on"
                " Torque 6.0.2 and above. You may get qsub error"
                " code 168.",
                file=sys.stderr,
            )
    elif spec.scheduler == "slurm":
        try:
            spec.depend_job_ids = slurm_find_jobs(spec)
        except Exception as e:
            raise QbatchError(f"Error matching depend pattern {e!s}")

    spec.environ = dict(os.environ)

    scripts = generate_scripts(spec)
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
        help="""Maximum walltime for an array job element or individual job""",
    )
    parser.add_argument(
        "-c",
        "--chunksize",
        default=defaults.chunk_size,
        type=int,
        help="""Number of commands from the command list that are wrapped into
        each job""",
    )
    parser.add_argument(
        "-j",
        "--cores",
        default=defaults.cores,
        type=int_or_percent,
        help="""Number of commands each job runs in parallel. If the chunk size
        (-c) is smaller than -j then only chunk size commands will run in
        parallel. This option can also be expressed as a percentage (e.g.
        100%%) of the total available cores""",
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
        help="""Memory required for each job (e.g. --mem 1G).  This value will
        be set on each variable specified in --memvars. To not set any memory
        requirement, set this to 0""",
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
