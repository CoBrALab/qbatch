"""Tests for the scheduler adapters.

Each test builds an adapter from a spec and checks what it returns. Only
the run_command tests start a process (sh) and write to a tempdir.
"""

import re
import subprocess

import pytest

from qbatch import schedulers
from qbatch.errors import QbatchError
from qbatch.schedulers import (
    REGISTRY,
    ContainerScheduler,
    LocalScheduler,
    PbsScheduler,
    SgeScheduler,
    SlurmScheduler,
    compute_threads,
    run_command,
    scheduler_for,
)


def build(make_spec, **overrides):
    return scheduler_for(make_spec(**overrides)).build_scripts()


# ---------------------------------------------------------------- registry


def test_registry_lists_schedulers_in_help_order():
    assert tuple(REGISTRY) == ("pbs", "sge", "slurm", "local", "container")


@pytest.mark.parametrize(
    "name,adapter",
    [
        ("pbs", PbsScheduler),
        ("sge", SgeScheduler),
        ("slurm", SlurmScheduler),
        ("local", LocalScheduler),
        ("container", ContainerScheduler),
    ],
)
def test_scheduler_for_picks_the_adapter(make_spec, name, adapter):
    assert type(scheduler_for(make_spec(scheduler=name))) is adapter


@pytest.mark.parametrize(
    "name,binaries",
    [
        ("pbs", ("qsub", "qstat", "parallel")),
        ("sge", ("qsub", "qstat", "parallel")),
        ("slurm", ("sbatch", "squeue", "parallel")),
        ("local", ("parallel",)),
        ("container", ()),
    ],
)
def test_required_binaries(make_spec, name, binaries):
    assert scheduler_for(make_spec(scheduler=name)).required_binaries == binaries


# -------------------------------------------------------------- directives


def test_pbs_header_directives(make_spec):
    ((name, text),) = build(
        make_spec,
        scheduler="pbs",
        walltime="1:00:00",
        queue="fast",
        depend_job_ids=["1", "2"],
        depend_array_ids=["3[]"],
    )
    assert name == "testjob.0"
    assert "#PBS -N testjob" in text
    assert "#PBS -l walltime=1:00:00" in text
    assert "#PBS -q fast" in text
    assert "-W depend=afterok:1:2,afterokarray:3[]" in text
    assert "ARRAY_IND=$PBS_ARRAYID" in text


def test_pbs_dependencies_array_only(make_spec):
    scripts = build(make_spec, scheduler="pbs", depend_array_ids=["9[]"])
    assert "-W depend=afterokarray:9[]" in scripts[0][1]


def test_pbs_memory_request(make_spec):
    scripts = build(make_spec, scheduler="pbs", mem="4G", memvars="mem,vmem")
    assert "#PBS -l mem=4G,vmem=4G" in scripts[0][1]


def test_no_memory_request_when_mem_is_zero(make_spec):
    scripts = build(make_spec, scheduler="pbs", mem="0")
    assert "mem=" not in scripts[0][1]


def test_sge_hold_jid_from_patterns(make_spec):
    scripts = build(make_spec, scheduler="sge", depend=["prejob*"])
    assert "-hold_jid 'prejob*'" in scripts[0][1]


def test_sge_parallel_environment(make_spec):
    scripts = build(make_spec, scheduler="sge", ppj=8, sge_pe="smp")
    assert "#$ -pe smp 8" in scripts[0][1]


def test_slurm_walltime_colons_passthrough(make_spec):
    scripts = build(make_spec, scheduler="slurm", walltime="1:00:00")
    assert "#SBATCH --time=1:00:00" in scripts[0][1]


def test_slurm_walltime_seconds_to_minutes(make_spec):
    scripts = build(make_spec, scheduler="slurm", walltime="3600")
    assert "#SBATCH --time=60" in scripts[0][1]


def test_slurm_dependency_ids(make_spec):
    scripts = build(make_spec, scheduler="slurm", depend_job_ids=["11", "22"])
    assert "--dependency=afterok:11:22" in scripts[0][1]


def test_slurm_output_file_for_array_jobs(make_spec):
    tasks = [f"echo {i}\n" for i in range(4)]
    scripts = build(make_spec, scheduler="slurm", task_list=tasks, chunk_size=2)
    assert "#SBATCH --output=/work/logs/slurm-testjob-%A_%a.out" in scripts[0][1]


# ------------------------------------------------------------- script body


def test_individual_chunk_slicing(make_spec):
    tasks = [f"echo {i}\n" for i in range(4)]
    scripts = build(
        make_spec, scheduler="sge", task_list=tasks, chunk_size=2, individual=True
    )
    assert [name for name, _ in scripts] == ["testjob.0", "testjob.1"]
    assert "echo 0\necho 1\n" in scripts[0][1]
    assert "echo 2" not in scripts[0][1]
    assert "echo 2\necho 3\n" in scripts[1][1]


def test_array_script_body(make_spec):
    tasks = [f"echo {i}\n" for i in range(4)]
    ((name, text),) = build(
        make_spec, scheduler="sge", task_list=tasks, chunk_size=2, cores="2"
    )
    assert name == "testjob.array"
    assert "CHUNK_SIZE=2" in text
    assert "CORES=2" in text
    assert "export THREADS_PER_COMMAND=" in text
    assert "sed -n" in text
    assert "ARRAY_IND=$SGE_TASK_ID" in text


def test_chunk_size_zero_puts_every_task_in_one_job(make_spec):
    tasks = [f"echo {i}\n" for i in range(5)]
    scripts = build(make_spec, scheduler="pbs", task_list=tasks, chunk_size=0)
    assert [name for name, _ in scripts] == ["testjob.0"]


def test_local_runs_every_task_in_one_job(make_spec):
    tasks = [f"echo {i}\n" for i in range(5)]
    scripts = build(make_spec, scheduler="local", task_list=tasks, chunk_size=2)
    assert [name for name, _ in scripts] == ["testjob.0"]
    assert "sed -n" not in scripts[0][1]
    assert "cd /work" in scripts[0][1]


def test_footer_appended(make_spec):
    scripts = build(make_spec, scheduler="pbs", footer=["echo done"])
    assert scripts[0][1].endswith("echo done")


def test_container_meta_from_data(make_spec):
    scripts = build(
        make_spec,
        scheduler="container",
        task_list=["echo a\n", "echo b\n"],
        container_meta="-b container --",
    )
    assert dict(scripts) == {
        "testjob.joblist": "echo a\necho b\n",
        "testjob.meta": "-b container --",
    }


def test_env_copied_uses_spec_environ_not_os_environ(make_spec):
    scripts = build(
        make_spec, scheduler="pbs", env="copied", environ={"MYVAR": "myvalue"}
    )
    assert 'export MYVAR="myvalue"' in scripts[0][1]
    assert "PATH=" not in scripts[0][1]


def test_env_copied_skips_ignored_vars(make_spec):
    scripts = build(
        make_spec,
        scheduler="pbs",
        env="copied",
        environ={"PWD": "/somewhere", "SGE_TASK_ID": "3", "KEEP": "yes"},
    )
    text = scripts[0][1]
    assert 'export KEEP="yes"' in text
    assert "PWD" not in text
    assert "SGE_TASK_ID" not in text


@pytest.mark.parametrize("name", list(REGISTRY))
def test_env_copied_never_replaces_the_array_index(make_spec, name):
    # The copied exports come before the ARRAY_IND= line. If qbatch runs
    # inside an array job, an exported index from that job would replace
    # the index of the new job, and every element would run the same chunk.
    tasks = [f"echo {i}\n" for i in range(4)]
    header = build(make_spec, scheduler=name, task_list=tasks, chunk_size=2)[0][1]
    match = re.search(r"^ARRAY_IND=\$(\w+)$", header, re.MULTILINE)
    if match is None:
        pytest.skip(f"{name} scripts read no array index variable")
    index_var = match.group(1)

    scripts = build(
        make_spec,
        scheduler=name,
        task_list=tasks,
        chunk_size=2,
        env="copied",
        environ={index_var: "3", "KEEP": "yes"},
    )
    text = scripts[0][1]
    assert 'export KEEP="yes"' in text
    assert f"export {index_var}=" not in text


def test_adapter_sees_spec_changes_made_after_construction(make_spec):
    spec = make_spec(scheduler="slurm")
    scheduler = scheduler_for(spec)
    spec.depend_job_ids = ["77"]
    assert "--dependency=afterok:77" in scheduler.build_scripts()[0][1]


@pytest.mark.parametrize(
    "cores,expected",
    [
        ("2", 4),
        (2, 4),
        ("50%", 4),
        (8, 1),
    ],
)
def test_compute_threads_accepts_int_or_percent(make_spec, cores, expected):
    assert compute_threads(make_spec(ppj=8, cores=cores)) == expected


@pytest.mark.parametrize(
    "tasks,chunk_size,expected",
    [
        (10, 40, 8),  # one job with 10 tasks runs 10 at once, not 40
        (1, 40, 80),  # a single task gets every processor
        (80, 20, 4),  # array elements hold 20 tasks
        (80, 40, 2),  # full jobs are unchanged
    ],
)
def test_threads_follow_the_tasks_a_job_can_run(make_spec, tasks, chunk_size, expected):
    scripts = build(
        make_spec,
        scheduler="slurm",
        task_list=[f"echo {i}\n" for i in range(tasks)],
        chunk_size=chunk_size,
        cores=40,
        ppj=80,
    )
    assert f"export THREADS_PER_COMMAND={expected}" in scripts[0][1]


def test_cores_as_int_reaches_the_script(make_spec):
    scripts = build(
        make_spec, scheduler="sge", cores=4, ppj=4, task_list=["a\n", "b\n"]
    )
    assert "CORES=4" in scripts[0][1]


# ------------------------------------------------------------ dependencies

QSTAT_XML = b"""<Data>
<Job><Job_Id>101.server</Job_Id><Job_Name>prep</Job_Name>
<job_state>R</job_state></Job>
<Job><Job_Id>102[].server</Job_Id><Job_Name>prep_array</Job_Name>
<job_state>Q</job_state></Job>
<Job><Job_Id>103.server</Job_Id><Job_Name>prep_done</Job_Name>
<job_state>C</job_state></Job>
<Job><Job_Id>104.server</Job_Id><Job_Name>other</Job_Name>
<job_state>R</job_state></Job>
</Data>"""


def fake_output(monkeypatch, output):
    calls = []

    def check_output(command):
        calls.append(command)
        return output

    monkeypatch.setattr(schedulers.subprocess, "check_output", check_output)
    return calls


def test_pbs_dependencies_split_array_and_regular_jobs(make_spec, monkeypatch):
    fake_output(monkeypatch, QSTAT_XML)
    scheduler = scheduler_for(make_spec(scheduler="pbs", depend=["prep*"]))
    assert scheduler.find_dependencies() == (["102[].server"], ["101.server"])


def test_pbs_dependencies_match_job_ids_too(make_spec, monkeypatch):
    fake_output(monkeypatch, QSTAT_XML)
    scheduler = scheduler_for(make_spec(scheduler="pbs", depend=["104*"]))
    assert scheduler.find_dependencies() == ([], ["104.server"])


def test_slurm_dependencies(make_spec, monkeypatch):
    calls = fake_output(monkeypatch, b"prep 201\nother 202\n")
    scheduler = scheduler_for(make_spec(scheduler="slurm", depend=["prep"]))
    assert scheduler.find_dependencies() == ([], ["201"])
    assert calls[0][0] == "squeue"


@pytest.mark.parametrize("name", ["pbs", "slurm"])
def test_no_patterns_means_no_scheduler_query(make_spec, monkeypatch, name):
    calls = fake_output(monkeypatch, b"")
    assert scheduler_for(make_spec(scheduler=name)).find_dependencies() == ([], [])
    assert calls == []


@pytest.mark.parametrize("name", ["pbs", "slurm"])
def test_no_running_jobs_warns(make_spec, monkeypatch, capsys, name):
    fake_output(monkeypatch, b"")
    scheduler = scheduler_for(make_spec(scheduler=name, depend=["prep"]))
    assert scheduler.find_dependencies() == ([], [])
    assert "no running jobs found" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["sge", "local", "container"])
def test_other_schedulers_resolve_nothing(make_spec, name):
    scheduler = scheduler_for(make_spec(scheduler=name, depend=["prep"]))
    assert scheduler.find_dependencies() == ([], [])


# ------------------------------------------------------------------ submit


def fake_call(monkeypatch, return_code):
    calls = []

    def call(command):
        calls.append(command)
        return return_code

    monkeypatch.setattr(schedulers.subprocess, "call", call)
    return calls


@pytest.mark.parametrize("name,command", [("pbs", "qsub"), ("slurm", "sbatch")])
def test_submit_hands_the_script_to_the_scheduler(
    make_spec, monkeypatch, name, command
):
    calls = fake_call(monkeypatch, 0)
    scheduler_for(make_spec(scheduler=name)).submit("/scripts/job.array")
    assert calls == [[command, "/scripts/job.array"]]


def test_submit_raises_on_error_code(make_spec, monkeypatch):
    fake_call(monkeypatch, 3)
    with pytest.raises(QbatchError) as excinfo:
        scheduler_for(make_spec(scheduler="sge")).submit("/scripts/job.array")
    assert str(excinfo.value) == "qbatch: error: qsub call returned error code 3"


def test_dry_run_submits_nothing(make_spec, monkeypatch, capsys):
    calls = fake_call(monkeypatch, 0)
    scheduler = scheduler_for(make_spec(scheduler="slurm", dry_run=True, verbose=True))
    scheduler.submit("/scripts/job.array")
    assert calls == []
    assert "Running: sbatch /scripts/job.array" in capsys.readouterr().out


def test_local_submit_runs_the_script_with_a_log(make_spec, monkeypatch):
    runs = []
    monkeypatch.setattr(
        schedulers,
        "run_command",
        lambda command, logfile=None: runs.append((command, logfile)) or 0,
    )
    scheduler_for(make_spec(scheduler="local")).submit("/scripts/testjob.0")
    assert runs == [("/scripts/testjob.0", "/work/logs/testjob.log")]


def test_local_submit_raises_on_error_code(make_spec, monkeypatch):
    monkeypatch.setattr(schedulers, "run_command", lambda command, logfile=None: 2)
    with pytest.raises(QbatchError) as excinfo:
        scheduler_for(make_spec(scheduler="local")).submit("/scripts/testjob.0")
    assert "local run call returned error code 2" in str(excinfo.value)


def test_container_submit_does_nothing(make_spec, monkeypatch):
    calls = fake_call(monkeypatch, 0)
    scheduler_for(make_spec(scheduler="container")).submit("/scripts/testjob.meta")
    assert calls == []


# ------------------------------------------------------------- run_command


def test_run_command_prints_and_logs_the_output(tmp_path, capsys):
    log = tmp_path / "run.log"
    assert run_command(["sh", "-c", "echo one; echo two"], logfile=str(log)) == 0
    assert capsys.readouterr().out == "one\ntwo\n"
    assert log.read_text() == "one\ntwo\n"


def test_run_command_returns_the_exit_code():
    assert run_command(["sh", "-c", "exit 3"]) == 3


def test_run_command_stops_at_end_of_output_not_at_exit(tmp_path, capsys):
    # The child closes its output, then runs for a moment longer. Reading
    # must stop at the end of the output, without printing empty lines
    # while the child is still running.
    log = tmp_path / "run.log"
    command = ["sh", "-c", "echo hi; exec >/dev/null 2>&1; sleep 0.3"]
    assert run_command(command, logfile=str(log)) == 0
    assert capsys.readouterr().out == "hi\n"
    assert log.read_text() == "hi\n"


def test_run_command_keeps_output_after_a_blank_line(tmp_path, capsys, monkeypatch):
    # The child exits before its output is read. Output after a blank
    # line must not be lost.
    real_popen = subprocess.Popen

    def exited_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        process.wait()
        return process

    monkeypatch.setattr(schedulers.subprocess, "Popen", exited_popen)
    log = tmp_path / "run.log"
    assert run_command(["sh", "-c", "printf 'a\\n\\nb\\n'"], logfile=str(log)) == 0
    assert capsys.readouterr().out == "a\n\nb\n"
    assert log.read_text() == "a\nb\n"
