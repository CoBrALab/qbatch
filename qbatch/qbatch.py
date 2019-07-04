#!/usr/bin/env python
from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals
from __future__ import absolute_import
from future import standard_library
from builtins import *
from builtins import str
from builtins import range
import argparse
import math
import os
import pkg_resources
import re
import subprocess
import stat
import sys
import fnmatch
import errno
from io import open
from textwrap import dedent
standard_library.install_aliases()

# Fix python2's environment to return UTF-8 encoded items
# Stolen from https://stackoverflow.com/a/31004947/4130016
if sys.version_info[0] < 3:
    class _EnvironDict(dict):
        def __getitem__(self, key):
            return super(_EnvironDict,
                         self).__getitem__(key.encode("utf-8")).decode("utf-8")

        def __setitem__(self, key, value):
            return super(_EnvironDict, self).__setitem__(key.encode("utf-8"),
                                                         value.encode("utf-8"))

        def get(self, key, failobj=None):
            try:
                return super(_EnvironDict, self).get(key.encode("utf-8"),
                                                     failobj).decode("utf-8")
            except AttributeError:
                return super(_EnvironDict, self).get(key.encode("utf-8"),
                                                     failobj)
    os.environ = _EnvironDict(os.environ)


def _setupVars():
    # setup defaults (let environment override)
    global SYSTEM
    SYSTEM = os.environ.get("QBATCH_SYSTEM", "local")
    global PPJ
    PPJ = os.environ.get("QBATCH_PPJ", "1")
    global CHUNKSIZE
    CHUNKSIZE = os.environ.get("QBATCH_CHUNKSIZE", PPJ)
    global CORES
    CORES = os.environ.get("QBATCH_CORES", PPJ)
    global NODES
    NODES = os.environ.get("QBATCH_NODES", "1")
    global SGE_PE
    SGE_PE = os.environ.get("QBATCH_SGE_PE", "smp")
    global MEMVARS
    MEMVARS = os.environ.get("QBATCH_MEMVARS", "mem")
    global MEM
    MEM = os.environ.get("QBATCH_MEM", "0")
    global SCRIPT_FOLDER
    SCRIPT_FOLDER = os.environ.get("QBATCH_SCRIPT_FOLDER", ".qbatch/")
    global QUEUE
    QUEUE = os.environ.get("QBATCH_QUEUE", None)
    global SHELL
    SHELL = os.environ.get("QBATCH_SHELL", "/bin/sh")
    global OPTIONS
    OPTIONS = [os.environ.get("QBATCH_OPTIONS")] if os.environ.get(
        "QBATCH_OPTIONS") else []

    # environment vars to ignore when copying the environment to the job script
    global IGNORE_ENV_VARS
    IGNORE_ENV_VARS = ['PWD', 'SGE_TASK_ID', 'PBS_ARRAYID', 'ARRAY_IND',
                       'BASH_FUNC_*', "TMP", "TMPDIR"]

    global PBS_HEADER_TEMPLATE
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
    {env}
    {header_commands}
    ARRAY_IND=$PBS_ARRAYID
    """)

    global SGE_HEADER_TEMPLATE
    SGE_HEADER_TEMPLATE = dedent(
        """\
    #!{shell}
    #$ -S {shell}
    #$ {ppj}
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
    {env}
    {header_commands}
    ARRAY_IND=$SGE_TASK_ID
    """)

    global SLURM_HEADER_TEMPLATE
    SLURM_HEADER_TEMPLATE = dedent(
        """\
    #!{shell}
    #SBATCH --nodes={nodes}
    #SBATCH {ppj}
    #SBATCH {logfile}
    #SBATCH --chdir={workdir}
    #SBATCH --job-name={job_name}
    #SBATCH {o_memopts}
    #SBATCH {o_queue}
    #SBATCH {o_array}
    #SBATCH {o_walltime}
    #SBATCH {o_dependencies}
    #SBATCH {o_options}
    #SBATCH {o_env}
    {env}
    {header_commands}
    ARRAY_IND=$SLURM_ARRAY_TASK_ID
    """)

    global LOCAL_TEMPLATE
    LOCAL_TEMPLATE = dedent(
        """\
    #!{shell}
    {env}
    {header_commands}
    cd {workdir}
    """)

    global __varsSet
    __varsSet = True


def run_command(command, logfile=None):
    # Run command and collect stdout
    # http://blog.endpoint.com/2015/01/getting-realtime-output-using-python.html # noqa
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if logfile:
        filehandle = open(logfile, 'w', encoding="utf-8")
    while True:
        output = process.stdout.readline()
        if output.decode('UTF-8') == '' and process.poll() is not None:
            break
        if output and logfile:
            print(output.decode('UTF-8').strip())
            filehandle.write(output.decode('UTF-8').strip())
            filehandle.write("\n")
        elif output:
            print(output.decode('UTF-8').strip())
    rc = process.poll()
    if logfile:
        filehandle.close()
    return rc


def mkdirp(*p):
    """Like mkdir -p"""
    path = os.path.join(*p)

    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            pass
        else:
            raise
    return path


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
    if not re.match(r"^([-+]?\d+|^\d+%)$", string):
        msg = "Must be an integer or positive integer percentage"
        raise argparse.ArgumentTypeError(msg)
    return string


def compute_threads(ppj, ncores):
    """Computes either number cores per job available"""
    if not ppj:
        ppj = 1
    if ncores[-1] == '%':
        return int(math.floor(ppj * float(ncores.strip('%')) / 100))
    else:
        return int(ppj) // int(ncores)


def pbs_find_jobs(patterns):
    """Finds jobs with names matching a given list of patterns

    Returns a list of job IDs.

    Raises an Exception if there is an error running the 'qstat' command or
    parsing its output.
    """
    if not patterns:
        return [], []

    if isinstance(patterns, str):
        patterns = [patterns]

    import xml.etree.ElementTree as ET

    output = subprocess.check_output(['qstat', '-x'])
    if not output:
        print(
            "qbatch: warning: Dependencies specified but no running"
            " jobs found",
            file=sys.stderr)
        return [], []
    tree = ET.fromstring(output)

    array_matches = []
    regular_matches = []
    for job in tree:
        jobid = job.find('Job_Id').text
        name = job.find('Job_Name').text
        state = job.find('job_state').text

        # ignore completed or errored jobs
        if state in ['C', 'E']:
            continue

        for pattern in patterns:
            if fnmatch.fnmatch(name, pattern):
                if fnmatch.fnmatch(jobid, '*[[][]]*'):
                    array_matches.append(jobid)
                else:
                    regular_matches.append(jobid)
            if fnmatch.fnmatch(jobid, pattern):
                if fnmatch.fnmatch(jobid, '*[[][]]*'):
                    array_matches.append(jobid)
                else:
                    regular_matches.append(jobid)
    return array_matches, regular_matches


def slurm_find_jobs(patterns):
    """Finds jobs with names matching a given list of patterns
    Returns a list of job IDs.
    Raises an Exception if there is an error running the 'squeue' command or
    parsing its output.
    """
    if not patterns:
        return []

    if isinstance(patterns, str):
        patterns = [patterns]

    output = subprocess.check_output(
        ['squeue', '-h', '--user={}'.format(os.environ.get("USER")),
         '--states=PD,R,S,CF', '--format=%j %A']).decode('utf-8')
    if not output:
        print(
            "qbatch: warning: Dependencies specified but no running"
            " jobs found",
            file=sys.stderr)
        return []

    regular_matches = []
    for line in output.split("\n"):
        for pattern in patterns:
            # ignore completed jobs
            if re.search(pattern, line):
                jobid = line.split()[1]
                regular_matches.append(jobid)
    return regular_matches


def which(program):
    # Check for existence of important programs
    # Stolen from
    # http://stackoverflow.com/questions/377017/test-if-executable-exists-in-python # noqa
    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, fname = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            path = path.strip('"')
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file

    return None


def qbatchDriver(**kwargs):
    try:
        __varsSet
    except NameError:
        _setupVars()
    else:
        pass
    command_file = kwargs.get('command_file')
    walltime = kwargs.get('walltime')
    chunk_size = kwargs.get('chunksize')
    ncores = kwargs.get('cores')
    ppj = kwargs.get('ppj')
    job_name = kwargs.get('jobname')
    mem = kwargs.get('mem') != '0' and kwargs.get('mem') or None
    queue = kwargs.get('queue')
    verbose = kwargs.get('verbose')
    dry_run = kwargs.get('dryrun')
    depend_pattern = kwargs.get('depend')
    workdir = kwargs.get('workdir')
    logdir = kwargs.get('logdir').format(workdir=workdir)
    options = kwargs.get('options')
    header_commands = (kwargs.get('header') and
                       '\n'.join(kwargs.get('header')) or '')
    footer_commands = (kwargs.get('footer') and
                       '\n'.join(kwargs.get('footer')) or '')
    nodes = kwargs.get('nodes')
    sge_pe = kwargs.get('sge_pe')
    memvars = kwargs.get('memvars').split(',')
    nodes_spec = (kwargs.get('pbs_nodes_spec') and
                  ':'.join(kwargs.get('pbs_nodes_spec')) + ':') or ''
    use_array = not kwargs.get('individual')
    system = kwargs.get('system')
    env_mode = kwargs.get('env')
    shell = kwargs.get('shell')

    mkdirp(logdir)

    # read in commands
    if not kwargs.get('task_list'):
        if command_file[0] == '--':
            if (len(command_file) > 1):
                task_list = [" ".join(command_file[1:])]
                job_name = job_name or command_file[1]
            else:
                sys.exit("qbatch: error: no command provided as last argument")
        elif command_file[0] == '-':
            task_list = sys.stdin.readlines()
            job_name = job_name or 'STDIN'
        else:
            task_list = []
            for file in command_file:
                if os.path.isfile(file):
                    task_list = task_list + open(file,
                                                 'r',
                                                 encoding="utf-8").readlines()
                    job_name = job_name or os.path.basename(file)
                else:
                    sys.exit("qbatch: error: command_file {0}".format(file) +
                             " does not exist or cannot be read")
    else:
        job_name = job_name or 'qbatchDriver'

    # compute the number of jobs needed. This will be the number of elements in
    # the array job
    if len(task_list) == 0:
        print("qbatch: warning: No jobs to submit, exiting", file=sys.stderr)
        sys.exit()

    if system == 'local':
        use_array = False
        num_jobs = 1
        chunk_size = sys.maxsize
    elif len(task_list) <= chunk_size:
        use_array = False
        num_jobs = 1
        if verbose:
            print("Number of commands less than chunk size, "
                  "building single non-array job", file=sys.stderr)
    else:
        num_jobs = int(math.ceil(len(task_list) / float(chunk_size)))

    # copy the current environment
    env = ''
    if env_mode == 'copied':
        env = '\n'.join(['export {0}="{1}"'.format(k, v.replace('"', r'\"'))
                         for k, v in list(os.environ.items())
                         if not any(fnmatch.fnmatch(k, pattern) for pattern
                                    in IGNORE_ENV_VARS)])
        env = env.replace("$", "$$")
        env = "# -- start copied env\n{0}\n# -- end copied env".format(env)

    if system == 'pbs':
        try:
            matching_array_jobids, matching_regular_jobids = pbs_find_jobs(
                depend_pattern)
        except Exception as e:
            sys.exit(
                "qbatch: error: Error matching"
                " depend pattern {0}".format(str(e)))

        if (matching_array_jobids and matching_regular_jobids):
            print("qbatch: warning: depdendencies on both regular and"
                  " array jobs found, this is only supported on"
                  " Torque 6.0.2 and above. You may get qsub error"
                  " code 168.", file=sys.stderr)

        o_array = use_array and '-t 1-{0}'.format(num_jobs) or ''
        o_walltime = walltime and "-l walltime={0}".format(walltime) or ''
        o_dependencies = '{0}'.format(
            '-W depend=' if (matching_array_jobids or matching_regular_jobids)
            else '')
        o_dependencies += '{0}'.format(('afterok:' + ':'.join(
            matching_regular_jobids)) if matching_regular_jobids else '')
        o_dependencies += '{0}'.format(
            ',' if (matching_array_jobids and matching_regular_jobids) else '')
        o_dependencies += '{0}'.format(('afterokarray:' + ':'.join(
            matching_array_jobids)) if matching_array_jobids else '')
        o_options = '\n#PBS '.join(options)
        mem_string = ','.join(["{0}={1}".format(var, mem) for var in memvars])
        o_memopts = (mem and mem_string) and '-l {0}'.format(mem_string) or ''
        o_env = (env_mode == 'batch') and '-V' or ''
        o_queue = queue and '-q {0}'.format(queue) or ''

        header = PBS_HEADER_TEMPLATE.format(**vars())

    elif system == 'sge':
        ppj = (ppj > 1) and '-pe {0} {1}'.format(sge_pe, ppj) or ''
        o_array = use_array and '-t 1-{0}'.format(num_jobs) or ''
        o_walltime = walltime and "-l h_rt={0}".format(walltime) or ''
        o_dependencies = depend_pattern and '-hold_jid \'' + \
            '\',\''.join(depend_pattern) + '\'' or ''
        o_options = '\n#$ '.join(options)
        mem_string = ','.join(["{0}={1}".format(var, mem) for var in memvars])
        o_memopts = (mem and mem_string) and '-l {0}'.format(mem_string) or ''
        o_env = (env_mode == 'batch') and '-V' or ''
        o_queue = queue and '-q {0}'.format(queue) or ''

        header = SGE_HEADER_TEMPLATE.format(**vars())

    elif system == 'slurm':
        ppj = (ppj > 1) and '--cpus-per-task={0}'.format(ppj) or ''
        o_array = use_array and '--array=1-{0}'.format(num_jobs) or ''
        if (walltime and walltime.find(":") > 0):
            o_walltime = "--time={0}".format(walltime)
        elif walltime:
            o_walltime = "--time={:1.0f}".format(
                int(walltime) / 60)
        else:
            o_walltime = ''
        try:
            matching_regular_jobids = slurm_find_jobs(
                depend_pattern)
        except Exception as e:
            sys.exit("Error matching depend pattern {0}".format(str(e)))
        o_dependencies = '{0}'.format(
            '--dependency=afterok:' + ':'.join(matching_regular_jobids)
            if (matching_regular_jobids) else '')
        o_options = '\n#SBATCH '.join(options)
        mem_string = ','.join(["{0}={1}".format(var, mem) for var in memvars])
        o_memopts = (mem and mem_string) and '--{0}'.format(mem_string) or ''
        o_env = (env_mode == 'batch') and '--export=ALL' or '--export=NONE'
        logfile = use_array and '--output={0}/slurm-{1}-%A_%a.out'.format(
            logdir, job_name) or '--output={0}/slurm-{1}-%J.out'.format(
            logdir, job_name)
        o_queue = queue and '--partition={0}'.format(queue) or ''

        header = SLURM_HEADER_TEMPLATE.format(**vars())

    elif system == 'local':
        header = LOCAL_TEMPLATE.format(**vars())

    # emit job scripts
    job_scripts = []
    mkdirp(SCRIPT_FOLDER)
    if use_array:
        script_lines = [
            header,
            'command -v parallel > /dev/null 2>&1 || { echo "GNU parallel not '
            'found in job environment. Exiting."; exit 1; }',
            'CHUNK_SIZE={0}'.format(chunk_size),
            'CORES={0}'.format(ncores),
            'export THREADS_PER_COMMAND={0}'.format(
                compute_threads(
                    kwargs.get('ppj'),
                    ncores)),
            'sed -n "$(( (${ARRAY_IND} - 1) * ${CHUNK_SIZE} + 1 )),'
            '+$(( ${CHUNK_SIZE} - 1 ))p" << EOF | parallel -j${CORES} --tag'
            ' --line-buffer --compress',
            ''.join(task_list),
            'EOF']

        scriptfile = os.path.join(SCRIPT_FOLDER, job_name + ".array")
        script = open(scriptfile, 'w', encoding="utf-8")
        script.write('\n'.join(script_lines))
        if footer_commands:
            script.write('\n')
            script.write(footer_commands)
        script.close()
        job_scripts.append(scriptfile)
    else:
        for chunk in range(num_jobs):
            scriptfile = os.path.join(
                SCRIPT_FOLDER, "{0}.{1}".format(job_name, chunk))
            if len(task_list) == 1:
                script_lines = [
                    header,
                    'export THREADS_PER_COMMAND={0}'.format(
                        compute_threads(kwargs.get('ppj'), ncores)),
                    ''.join(task_list)]
            else:
                script_lines = [
                    header,
                    'command -v parallel > /dev/null 2>&1 || { echo "GNU'
                    ' parallel not found in job environment. Exiting.";'
                    ' exit 1; }',
                    'CORES={0}'.format(ncores),
                    'export THREADS_PER_COMMAND={0}'.format(
                        compute_threads(kwargs.get('ppj'), ncores)),
                    "parallel -j${CORES} --tag --line-buffer"
                    " --compress << EOF",
                    ''.join(task_list[chunk * chunk_size:chunk *
                                      chunk_size + chunk_size]),
                    'EOF']
            script = open(scriptfile, 'w', encoding="utf-8")
            script.write('\n'.join(script_lines))
            if footer_commands:
                script.write('\n')
                script.write(footer_commands)
            script.close()
            job_scripts.append(scriptfile)

    # preflight checks
    if SYSTEM == "slurm":
        which('sbatch') or sys.exit("qbatch: error: QBATCH_SYSTEM set to slurm"
                                    " but sbatch not found")
        which('squeue') or sys.exit("qbatch: error: QBATCH_SYSTEM set to slurm"
                                    " but squeue not found")
    elif (SYSTEM == "pbs") or (SYSTEM == "sge"):
        which('qsub') or sys.exit("qbatch: error: QBATCH_SYSTEM set to pbs/sge"
                                  " but qsub not found")
        which('qstat') or sys.exit("qbatch: error: QBATCH_SYSTEM set to"
                                   " pbs/sge but qstat not found")

    which('parallel') or sys.exit("qbatch: error: gnu-parallel not found")

    # execute the job script(s)
    for script in job_scripts:
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)
        if system == 'sge' or system == 'pbs':
            if verbose:
                print("Running: qsub {0}".format(script))
            if dry_run:
                continue
            return_code = subprocess.call(['qsub', script])
            if return_code:
                sys.exit("qbatch: error: qsub call " +
                         "returned error code {0}".format(return_code))
        elif system == 'slurm':
            if verbose:
                print("Running: sbatch {0}".format(script))
            if dry_run:
                continue
            return_code = subprocess.call(['sbatch', script])
            if return_code:
                sys.exit("qbatch: error: sbatch call " +
                         "returned error code {0}".format(return_code))
        else:
            logfile = "{0}/{1}.log".format(logdir, job_name)
            if verbose:
                print("Launching jobscript. Output to {0}".format(logfile))
            if dry_run:
                continue
            return_code = run_command(script, logfile=logfile)
            if return_code:
                sys.exit("qbatch: error: local run call " +
                         "returned error code {0}".format(return_code))


def qbatchParser(args=None):
    _setupVars()
    __version__ = pkg_resources.require("qbatch")[0].version

    parser = argparse.ArgumentParser(
        description="""Submits a list of commands to a queueing system.
        The list of commands can be broken up into 'chunks' when submitted, so
        that the commands in each chunk run in parallel (using GNU parallel).
        The job script(s) generated by %(prog)s are stored in the folder
        {0}""".format(SCRIPT_FOLDER),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "command_file", nargs=argparse.REMAINDER,
        help="""An input file containing a list of shell commands to be
        submitted, - to read the command list from stdin or -- followed
        by a single command""")
    parser.add_argument(
        "-w", "--walltime",
        help="""Maximum walltime for an array job element or individual job""")
    parser.add_argument(
        "-c", "--chunksize", default=CHUNKSIZE, type=positive_int,
        help="""Number of commands from the command list that are wrapped into
        each job""")
    parser.add_argument(
        "-j", "--cores", default=CORES, type=int_or_percent,
        help="""Number of commands each job runs in parallel. If the chunk size
        (-c) is smaller than -j then only chunk size commands will run in
        parallel. This option can also be expressed as a percentage (e.g.
        100%%) of the total available cores""")
    parser.add_argument(
        "--ppj", default=PPJ, type=positive_int,
        help="""Requested number of processors per job (aka ppn on PBS,
        slots on SGE, cpus per task on SLURM). Cores can be over subscribed
        if -j is larger than --ppj
        (useful to make use of hyper-threading on some systems)""")
    parser.add_argument(
        "-N", "--jobname", action="store",
        help="""Set job name (defaults to name of command file, or STDIN)""")
    parser.add_argument(
        "--mem", default=MEM,
        help="""Memory required for each job (e.g. --mem 1G).  This value will
        be set on each variable specified in --memvars. To not set any memory
        requirement, set this to 0""")
    parser.add_argument(
        "-q", "--queue", default=QUEUE,
        help="""Name of queue to submit jobs to (defaults to no queue)""")

    parser.add_argument(
        "-n", "--dryrun", action="store_true",
        help="Dry run; Create jobfiles but do not submit or run any commands")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output")
    parser.add_argument('--version', action='version', version=__version__)

    group = parser.add_argument_group('advanced options')
    group.add_argument(
        "--depend", action="append",
        help="""Wait for successful completion of job(s) with name matching
        given glob pattern or job id matching given job id(s) before
        starting""")
    group.add_argument(
        "-d", "--workdir", default=os.getcwd(),
        help="Job working directory")
    group.add_argument(
        "--logdir", action="store", default="{workdir}/logs",
        help="""Directory to save store log files""")
    group.add_argument(
        "-o", "--options", action="append", default=OPTIONS,
        help="""Custom options passed directly to the queuing system (e.g
        --options "-l vf=8G". This option can be given multiple times""")
    group.add_argument(
        "--header", action="append",
        help="""A line to insert verbatim at the start of the script, and will
        be run once per job. This option can be given multiple times""")
    group.add_argument(
        "--footer", action="append",
        help="""A line to insert verbatim at the end of the script, and will
        be run once per job. This option can be given multiple times""")
    group.add_argument(
        "--nodes", default=NODES, type=positive_int,
        help="(PBS and SLURM only) Nodes to request per job")
    group.add_argument(
        "--sge-pe", default=SGE_PE,
        help="""(SGE-only) The parallel environment to use if more than one
        processor per job is requested""")
    group.add_argument(
        "--memvars", default=MEMVARS,
        help="""A comma-separated list of variables to set with the memory
        limit given by the --mem option (e.g. --memvars=h_vmem,vf)""")
    group.add_argument(
        "--pbs-nodes-spec", action="append",
        help="(PBS-only) String to be inserted into nodes= line of job")
    group.add_argument(
        "-i", "--individual", action="store_true",
        help="Submit individual jobs instead of an array job")
    group.add_argument(
        "-b", "--system", default=SYSTEM, choices=['pbs', 'sge', 'slurm',
                                                   'local'],
        help="""The type of queueing system to use. 'pbs' and 'sge' both make
        calls to qsub to submit jobs. 'slurm' calls sbatch.
        'local' runs the entire command list (without chunking) locally.""")
    group.add_argument(
        "--env", choices=['copied', 'batch', 'none'], default='copied',
        help="""Determines how your environment is propagated when your
              job runs. "copied" records your environment settings in
              the job submission script, "batch" uses the cluster's
              mechanism for propagating your environment, and "none"
              does not propagate any environment variables.""")
    group.add_argument(
        "--shell", default=SHELL,
        help="""Shell to use for spawning jobs
        and launching single commands""")

    args = parser.parse_args(args)
    if not args.command_file:
        parser.print_usage()
        sys.exit("qbatch: error: no command file or command provided")
    qbatchDriver(**vars(args))


if __name__ == "__main__":
    qbatchParser()
