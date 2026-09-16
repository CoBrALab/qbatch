"""Scheduler adapters: everything that differs between pbs, sge, slurm,
local and container execution.

Build one with scheduler_for(spec). Callers use four members:
build_scripts(), find_dependencies(), required_binaries and submit(path).
Subclasses that write a scheduler header fill in directives().
"""

import fnmatch
import math
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from contextlib import nullcontext
from textwrap import dedent

from qbatch.errors import QbatchError

# environment vars to ignore when copying the environment to the job script.
# The copied exports come before the ARRAY_IND= line, so every array index
# variable that a template reads must be here, or qbatch run inside an array
# job copies that job's index into the new one.
IGNORE_ENV_VARS = [
    "PWD",
    "SGE_TASK_ID",
    "PBS_ARRAYID",
    "SLURM_ARRAY_TASK_ID",
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
    """Run command, printing each line of its output as it arrives and
    copying non-blank lines to logfile. Returns the exit code."""
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    with (
        open(logfile, "w", encoding="utf-8") if logfile else nullcontext()
    ) as filehandle:
        # read to the end of the output, not until the process exits: the
        # two do not happen at the same moment
        for line in process.stdout:
            output = line.decode("utf-8").strip()
            if output and logfile:
                filehandle.write(output)
                filehandle.write("\n")
            print(output)
    return process.wait()


def compute_threads(spec, most_tasks=None):
    """Computes either number cores per job available. most_tasks is the
    largest number of tasks one job holds: a job cannot run more at once."""
    ppj = spec.ppj or 1
    cores = spec.cores
    if isinstance(cores, str) and cores.endswith("%"):
        return math.floor(int(ppj) * float(cores.strip("%")) / 100)
    cores = int(cores)
    if most_tasks and cores > most_tasks:
        cores = most_tasks
    return int(ppj) // cores


def format_mem(mib):
    """Write a memory size that every scheduler reads the same way: whole
    GiB or MiB, with an uppercase unit. SGE reads a lowercase unit as a
    power of 1000, and rejects a trailing B."""
    return f"{mib // 1024}G" if mib % 1024 == 0 else f"{mib}M"


def format_hms(seconds, resolution=1):
    """Write a walltime as H:MM:SS, rounded up to whole steps of resolution
    seconds. Hours have no upper limit and there is no days field, because
    PBS, SGE and Slurm all read 36:00:00 as 36 hours."""
    seconds = -(-seconds // resolution) * resolution
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class Scheduler:
    """Base adapter. Holds the spec; methods read it when called, so
    changes the driver makes to the spec after construction are seen."""

    name = None
    # binaries that must be on PATH before submitting
    required_binaries = ()
    # run every task in one job, with no array and no chunk size limit
    one_job_only = False
    # the command that takes a job script, for batch schedulers
    submit_command = None
    # the scheduler rounds walltime up to whole steps of this many seconds
    walltime_resolution = 1

    def __init__(self, spec):
        self.spec = spec

    def find_dependencies(self):
        """Resolve the spec's depend patterns to (array_ids, job_ids)."""
        return [], []

    def directives(self, use_array, num_jobs, common):
        """The header that starts each job script."""
        raise NotImplementedError

    def build_scripts(self):
        """Generate phase: returns a list of (basename, text) pairs.
        Produces text only: no filesystem, process, or scheduler access."""
        spec = self.spec
        task_list = spec.task_list
        job_name = spec.job_name
        chunk_size = spec.chunk_size
        use_array = not spec.individual
        footer_commands = spec.footer and "\n".join(spec.footer) or ""

        # compute the number of jobs needed. This will be the number of
        # elements in the array job
        if self.one_job_only or chunk_size == 0:
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
            num_jobs = math.ceil(len(task_list) / float(chunk_size))

        # the last array element can hold fewer tasks than the
        # others, but shares their script, so it keeps their thread count
        threads = compute_threads(spec, min(chunk_size, len(task_list)))

        # placeholders every scheduler template shares
        common = {
            "shell": spec.shell,
            "logdir": spec.logdir,
            "workdir": spec.workdir,
            "job_name": job_name,
            "env": self._env_exports(),
            "header_commands": spec.header and "\n".join(spec.header) or "",
        }
        header = self.directives(use_array, num_jobs, common)

        # emit job script text
        scripts = []
        if use_array:
            script_lines = [
                header,
                (
                    'command -v parallel > /dev/null 2>&1 || { echo "GNU parallel '
                    'not found in job environment. Exiting."; exit 1; }'
                ),
                f"CHUNK_SIZE={chunk_size}",
                f"CORES={spec.cores}",
                f"export THREADS_PER_COMMAND={threads}",
                (
                    'sed -n "$(( (${ARRAY_IND} - 1) * ${CHUNK_SIZE} + 1 )),'
                    "+$(( ${CHUNK_SIZE} - 1 ))p\" << 'EOF' | parallel -j${CORES}"
                    " --tag --line-buffer --compress"
                ),
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
                        f"export THREADS_PER_COMMAND={threads}",
                        "".join(task_list),
                    ]
                else:
                    script_lines = [
                        header,
                        (
                            'command -v parallel > /dev/null 2>&1 || { echo "GNU'
                            ' parallel not found in job environment. Exiting.";'
                            " exit 1; }"
                        ),
                        f"CORES={spec.cores}",
                        f"export THREADS_PER_COMMAND={threads}",
                        "parallel -j${CORES} --tag --line-buffer --compress << 'EOF'",
                        "".join(
                            task_list[
                                chunk * chunk_size : chunk * chunk_size + chunk_size
                            ]
                        ),
                        "EOF",
                    ]
                text = "\n".join(script_lines)
                if footer_commands:
                    text += "\n" + footer_commands
                scripts.append((f"{job_name}.{chunk}", text))

        return scripts

    def submit(self, path):
        """Hand one written job script to the scheduler."""
        if self.spec.verbose:
            print(f"Running: {self.submit_command} {path}")
        if self.spec.dry_run:
            return
        return_code = subprocess.call([self.submit_command, path])
        if return_code:
            raise QbatchError(
                f"qbatch: error: {self.submit_command} call returned error code"
                f" {return_code}"
            )

    def _env_exports(self):
        """The copied environment block, or nothing."""
        if self.spec.env != "copied":
            return ""
        env_exports = "\n".join(
            [
                'export {}="{}"'.format(k, v.replace('"', r"\""))
                for k, v in list(self.spec.environ.items())
                if not any(fnmatch.fnmatch(k, pattern) for pattern in IGNORE_ENV_VARS)
            ]
        )
        env_exports = env_exports.replace("$", "$$")
        return f"# -- start copied env\n{env_exports}\n# -- end copied env"

    def _mem_string(self):
        """The memory request for every --memvars variable, or nothing."""
        if self.spec.mem_mib is None:
            return ""
        mem = format_mem(self.spec.mem_mib)
        return ",".join(f"{var}={mem}" for var in self.spec.memvars.split(","))


class PbsScheduler(Scheduler):
    name = "pbs"
    required_binaries = ("qsub", "qstat", "parallel")
    submit_command = "qsub"

    def find_dependencies(self):
        patterns = self.spec.depend
        if not patterns:
            return [], []

        if isinstance(patterns, str):
            patterns = [patterns]

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

    def directives(self, use_array, num_jobs, common):
        spec = self.spec
        mem_string = self._mem_string()

        o_dependencies = ""
        if spec.depend_array_ids or spec.depend_job_ids:
            o_dependencies = "-W depend="
            if spec.depend_job_ids:
                o_dependencies += "afterok:" + ":".join(spec.depend_job_ids)
            if spec.depend_array_ids and spec.depend_job_ids:
                o_dependencies += ","
            if spec.depend_array_ids:
                o_dependencies += "afterokarray:" + ":".join(spec.depend_array_ids)

        return PBS_HEADER_TEMPLATE.format(
            nodes=spec.nodes,
            nodes_spec=(spec.pbs_nodes_spec and ":".join(spec.pbs_nodes_spec) + ":")
            or "",
            ppj=spec.ppj,
            o_array=use_array and f"-t 1-{num_jobs}" or "",
            o_walltime=spec.walltime_seconds
            and f"-l walltime={format_hms(spec.walltime_seconds)}"
            or "",
            o_dependencies=o_dependencies,
            o_options="\n#PBS ".join(spec.options),
            o_memopts=mem_string and f"-l {mem_string}" or "",
            o_env=(spec.env == "batch") and "-V" or "",
            o_queue=spec.queue and f"-q {spec.queue}" or "",
            o_block=spec.block and " -Wblock=true" or "",
            **common,
        )


class SgeScheduler(Scheduler):
    name = "sge"
    required_binaries = ("qsub", "qstat", "parallel")
    submit_command = "qsub"

    def directives(self, use_array, num_jobs, common):
        spec = self.spec
        mem_string = self._mem_string()

        return SGE_HEADER_TEMPLATE.format(
            o_ppj=(spec.ppj > 1) and f"-pe {spec.sge_pe} {spec.ppj}" or "",
            o_array=use_array and f"-t 1-{num_jobs}" or "",
            o_walltime=spec.walltime_seconds
            and f"-l h_rt={format_hms(spec.walltime_seconds)}"
            or "",
            o_dependencies=(
                spec.depend and "-hold_jid '" + "','".join(spec.depend) + "'" or ""
            ),
            o_options="\n#$ ".join(spec.options),
            o_memopts=mem_string and f"-l {mem_string}" or "",
            o_env=(spec.env == "batch") and "-V" or "",
            o_queue=spec.queue and f"-q {spec.queue}" or "",
            o_block=spec.block and " -sync y" or "",
            **common,
        )


class SlurmScheduler(Scheduler):
    name = "slurm"
    required_binaries = ("sbatch", "squeue", "parallel")
    submit_command = "sbatch"
    walltime_resolution = 60

    def find_dependencies(self):
        patterns = self.spec.depend
        if not patterns:
            return [], []

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
            return [], []

        regular_matches = []
        for line in output.split("\n"):
            for pattern in patterns:
                # ignore completed jobs
                if re.search(pattern, line):
                    jobid = line.split()[1]
                    regular_matches.append(jobid)
        return [], regular_matches

    def directives(self, use_array, num_jobs, common):
        spec = self.spec
        mem_string = self._mem_string()

        logfile = (
            use_array
            and f"--output={spec.logdir}/slurm-{spec.job_name}-%A_%a.out"
            or f"--output={spec.logdir}/slurm-{spec.job_name}-%j.out"
        )

        return SLURM_HEADER_TEMPLATE.format(
            nodes=spec.nodes,
            o_ppj=(spec.ppj > 1) and f"--cpus-per-task={spec.ppj}" or "",
            logfile=logfile,
            o_array=use_array and f"--array=1-{num_jobs}" or "",
            o_walltime=spec.walltime_seconds
            and "--time=" + format_hms(spec.walltime_seconds, self.walltime_resolution)
            or "",
            o_dependencies="--dependency=afterok:" + ":".join(spec.depend_job_ids)
            if spec.depend_job_ids
            else "",
            o_options="\n#SBATCH ".join(spec.options),
            o_memopts=mem_string and f"--{mem_string}" or "",
            o_env=(spec.env == "batch") and "--export=ALL" or "--export=NONE",
            o_queue=spec.queue and f"--partition={spec.queue}" or "",
            o_block=spec.block and " --wait" or "",
            **common,
        )


class LocalScheduler(Scheduler):
    name = "local"
    required_binaries = ("parallel",)
    one_job_only = True

    def directives(self, use_array, num_jobs, common):
        return LOCAL_TEMPLATE.format(**common)

    def submit(self, path):
        logfile = f"{self.spec.logdir}/{self.spec.job_name}.log"
        if self.spec.verbose:
            print(f"Launching jobscript. Output to {logfile}")
        if self.spec.dry_run:
            return
        return_code = run_command(path, logfile=logfile)
        if return_code:
            raise QbatchError(
                f"qbatch: error: local run call returned error code {return_code}"
            )


class ContainerScheduler(Scheduler):
    """Writes the task list and metadata for a monitor outside the
    container to submit; submits nothing itself."""

    name = "container"

    def build_scripts(self):
        return [
            (self.spec.job_name + ".joblist", "".join(self.spec.task_list)),
            (self.spec.job_name + ".meta", self.spec.container_meta),
        ]

    def submit(self, path):
        pass


# insertion order is the order --help lists the choices in
REGISTRY = {
    cls.name: cls
    for cls in (
        PbsScheduler,
        SgeScheduler,
        SlurmScheduler,
        LocalScheduler,
        ContainerScheduler,
    )
}


def scheduler_for(spec):
    """The adapter for the spec's scheduler."""
    return REGISTRY[spec.scheduler](spec)
