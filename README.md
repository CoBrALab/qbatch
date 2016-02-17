# qbatch
--------------------------------------------------------------------------------

A script for generating array and standard jobs in arbitrary chunks to SGE/PBS clusters.
Can also run jobs locally on systems with no batch capability.

```
usage: qbatch [-h] [-w W] [-i] [-c C] [-j J] [-b {pbs,sge}] [--nodes NODES]
              [--ppn PPN] [--highmem] [-n] [--afterok_pattern AFTEROK_PATTERN]
              [--jobname JOBNAME]
              command_file [opts [opts ...]]

Submit commands to a queueing system

positional arguments:
  command_file          An input file containing a list of commands. Use - for
                        STDIN
  opts                  Quoted options to insert as header, in order, to job scripts. 
                        Can be qsub options, prepended with prefix characters, or commands
                        to run at the begining of all jobs (default: None)

optional arguments:
  -h, --help            show this help message and exit
  -w W                  Job working directory (default:
                        /data/chamal/projects/gabriel/qbatch)
  -i                    Use individual jobs instead of an array job (default:
                        False)
  -c C                  Number of input lines to chunk into a single job
                        (default: 1)
  -j J                  Number of commands to run in parallel per job
                        (default: 12)
  -b {pbs,sge,local}    Queueing system to use (default: pbs)
  --nodes NODES         Nodes to request per job (default: 1)
  --ppn PPN             Processors per node (ppn on PBS, parallel environment number on SGE) (default: 12)
  --highmem             (Scinet-only) Submit to high memory nodes (default:
                        False)
  -n                    Dry run; nothing is submitted or run (default: False)
  --afterok_pattern AFTEROK_PATTERN
                        Wait for successful completion of job with name(s)
                        matching glob pattern before starting (default: None)
  --jobname JOBNAME     Override default job name generated from command_file,
                        set name for STDIN jobs (default: None)
  --logdir LOGDIR       Directory to save store log files from batch system or
                        local processes (default:
                        your current directory/logs)
```

## Dependencies
qbatch requires at least ``python`` 2.7 and GNU ``parallel`` to run jobs locally.
For Torque/PBS qbatch requires access to ``qsub``, similarly for SGE/SoGE qbatch
requires access to ``qsub``. For Torque/PBS job dependency support the ``pbs_jobnames``
command included in this repository must be found somewhere in the ``$PATH``.


## Environment variable defaults
qbatch supports several environment variables to customize defaults for your
local system

```sh
$ export QBATCH_PPN=12
$ export QBATCH_NODES=1
$ export QBATCH_SYSTEM="pbs"
$ export QBATCH_CORES=$QBATCH_PPN
```

These correspond to the same named options in the qbatch help output above.


## Some examples:
```sh
# Submit an array job from a list of commands (one per line), default settings
$ qbatch commands.txt
# Generates job files in .scripts/, stores logs in logs/

# Submit an array job for SGE
$ qbatch -b sge commands.txt

# set the walltime 
$ qbatch commands.txt -- '#PBS -l walltime=3:00:00'

# Chunk 24 commands per array job
$ qbatch -c24 commands.txt

# Chunk 24 commands per array job, running 12 in parallel
$ qbatch -c25 -j12 commands.txt

# Start jobs after successful completion of all existing jobs with names starting with "2015-11-02-mb_register"
$ qbatch --afterok_pattern '2015-11-02-mb_register*' commands.txt

# Start jobs after successful completion of all existing jobs with names starting with "2015-11-02-mb_register" and "2015-11-02-mb_resample"
$ qbatch --afterok_pattern '2015-11-02-mb_register*' --afterok_pattern '2015-11-02-mb_resample*' commands.txt

# Dynamically generate a list of commands and submit them to batch system
$ for file in /path/to/some/files/*.mnc; echo do_something $file; done | qbatch -N do_something_jobs
# If the loop runs zero times, qbatch will report a warning to STDERR but exit successfully

# Run jobs locally with GNU Parallel, 12 commands in parallel
$ qbatch -b local -j12 commands.txt
# Many options don't make sense locally: chunking, individual vs array, nodes, ppn, highmem, and afterok_pattern are ignored
```
