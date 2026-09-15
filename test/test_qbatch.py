import os
import shlex
import shutil
import stat
import tempfile
from subprocess import PIPE, STDOUT, Popen

import pytest

from qbatch import schedulers
from qbatch.errors import QbatchError
from qbatch.qbatch import qbatchDriver, submit_scripts

tempdir = None

# set this to folder that all nodes on the cluster have access to
SHARED_FOLDER = os.getcwd()


def setup_module():
    global tempdir
    global myenv
    tempdir = tempfile.mkdtemp(dir=SHARED_FOLDER)
    myenv = os.environ.copy()
    myenv["QBATCH_SCRIPT_FOLDER"] = tempdir


def teardown_module():
    shutil.rmtree(tempdir)


def command_pipe(command):
    return Popen(
        shlex.split(command), stdin=PIPE, stdout=PIPE, stderr=STDOUT, env=myenv
    )


def test_qbatch_help():
    p = command_pipe("qbatch --help")
    _out, _ = p.communicate(b"")
    assert p.returncode == 0, p.returncode


def test_qbatch_help_no_queue_binary():
    """Tests that --help works when there is no queuing binary available.

    If QBATCH_SYSTEM is not 'local', using --help should still work.

    This tests for the following issue:
        https://github.com/CoBrALab/qbatch/issues/177
    """

    myenv["QBATCH_SYSTEM"] = "slurm"
    try:
        p = command_pipe("qbatch --help")
        _out, _ = p.communicate(b"")
        assert p.returncode == 0, p.returncode
    finally:
        del myenv["QBATCH_SYSTEM"]


def test_python_import():
    p = command_pipe('python -c "from qbatch import qbatchParser"')
    _out, _ = p.communicate(b"")

    assert p.returncode == 0


def test_python_help_launch():
    p = command_pipe(
        """python -c "from qbatch import qbatchParser; """
        + """qbatchParser(['-h'])" """
    )
    _out, _ = p.communicate(b"")

    assert p.returncode == 0


def test_run_qbatch_dryrun_single_output_exists():
    cmds = "echo hello"
    p = command_pipe("qbatch -N test_run_qbatch_dryrun_single_output_exists -n -")
    _out, _ = p.communicate(cmds.encode("utf-8"))

    assert p.returncode == 0
    assert os.path.exists(
        os.path.join(tempdir, "test_run_qbatch_dryrun_single_output_exists.0")
    )


def test_run_qbatch_sge_dryrun_array_piped_chunks():
    chunk_size = 10
    chunks = 5
    outputs = list(range(chunk_size * chunks))

    cmds = "\n".join([f"echo {x}" for x in outputs])
    p = command_pipe(
        f"qbatch -N test_run_qbatch_sge_dryrun_array_piped_chunks --env none -n -j2 \
                     -b sge -c {chunk_size} -"
    )
    out, _ = p.communicate(cmds.encode("utf-8"))

    array_script = os.path.join(
        tempdir, "test_run_qbatch_sge_dryrun_array_piped_chunks.array"
    )
    assert p.returncode == 0
    assert os.path.exists(array_script)

    for chunk in range(1, chunks + 1):
        myenv["SGE_TASK_ID"] = str(chunk)
        expected = (
            "\n".join(
                [
                    f"echo {x}\t{x}"
                    for x in outputs[(chunk - 1) * chunk_size : chunk * chunk_size]
                ]
            )
            + "\n"
        )
        array_pipe = command_pipe(array_script)
        out, _ = array_pipe.communicate()

        assert array_pipe.returncode == 0, (
            f"Chunk {chunk}: return code = {array_pipe.returncode}"
        )
        assert set(out.decode().splitlines()) == set(expected.splitlines()), (
            f"Chunk {chunk}: Expected {expected} but got {out}"
        )


def test_run_qbatch_pbs_dryrun_array_piped_chunks():
    chunk_size = 10
    chunks = 5
    outputs = list(range(chunk_size * chunks))

    cmds = "\n".join([f"echo {x}" for x in outputs])
    p = command_pipe(
        f"qbatch -N test_run_qbatch_pbs_dryrun_array_piped_chunks --env none -n -j2 \
                     -b pbs -c {chunk_size} -"
    )
    out, _ = p.communicate(cmds.encode("utf-8"))

    array_script = os.path.join(
        tempdir, "test_run_qbatch_pbs_dryrun_array_piped_chunks.array"
    )
    assert p.returncode == 0
    assert os.path.exists(array_script)

    for chunk in range(1, chunks + 1):
        myenv["PBS_ARRAYID"] = str(chunk)
        expected = (
            "\n".join(
                [
                    f"echo {x}\t{x}"
                    for x in outputs[(chunk - 1) * chunk_size : chunk * chunk_size]
                ]
            )
            + "\n"
        )
        array_pipe = command_pipe(array_script)
        out, _ = array_pipe.communicate()

        assert array_pipe.returncode == 0, (
            f"Chunk {chunk}: return code = {array_pipe.returncode}"
        )
        assert set(out.decode().splitlines()) == set(expected.splitlines()), (
            f"Chunk {chunk}: Expected {expected} but got {out}"
        )


def test_run_qbatch_slurm_dryrun_array_piped_chunks():
    chunk_size = 10
    chunks = 5
    outputs = list(range(chunk_size * chunks))

    cmds = "\n".join([f"echo {x}" for x in outputs])
    p = command_pipe(
        f"qbatch -N test_run_qbatch_slurm_dryrun_array_piped_chunks --env none -n -j2 \
                     -b slurm -c {chunk_size} -"
    )
    out, _ = p.communicate(cmds.encode("utf-8"))

    array_script = os.path.join(
        tempdir, "test_run_qbatch_slurm_dryrun_array_piped_chunks.array"
    )
    assert p.returncode == 0
    assert os.path.exists(array_script)

    for chunk in range(1, chunks + 1):
        myenv["SLURM_ARRAY_TASK_ID"] = str(chunk)
        expected = (
            "\n".join(
                [
                    f"echo {x}\t{x}"
                    for x in outputs[(chunk - 1) * chunk_size : chunk * chunk_size]
                ]
            )
            + "\n"
        )
        array_pipe = command_pipe(array_script)
        out, _ = array_pipe.communicate()

        assert array_pipe.returncode == 0, (
            f"Chunk {chunk}: return code = {array_pipe.returncode}"
        )
        assert set(out.decode().splitlines()) == set(expected.splitlines()), (
            f"Chunk {chunk}: Expected {expected} but got {out}"
        )


def test_run_qbatch_local_piped_commands():
    cmds = "\n".join(["echo hello"] * 24)
    p = command_pipe(
        "qbatch -N test_run_qbatch_local_piped_commands --env none -j2 -b local -"
    )
    out, _ = p.communicate(cmds.encode("utf-8"))

    expected, _ = command_pipe("parallel --tag --line-buffer -j2").communicate(
        cmds.encode("utf-8")
    )

    print(out)

    assert p.returncode == 0, f"Return code = {p.returncode}"
    assert set(out.splitlines()) == set(expected.splitlines()), (
        f"Expected {expected} but got {out}"
    )


def test_run_qbatch_local_piped_commands_utf8():
    cmds = "\n".join(["echo hëllo"] * 24)
    p = command_pipe(
        "qbatch -N tëst_run_qbatch_local_piped_commands --env none -j2 -b local -"
    )
    out, _ = p.communicate(cmds.encode("utf-8"))

    expected, _ = command_pipe("parallel --tag --line-buffer -j2").communicate(
        cmds.encode("utf-8")
    )

    print(out)

    assert p.returncode == 0, f"Return code = {p.returncode}"
    assert set(out.splitlines()) == set(expected.splitlines()), (
        f"Expected {expected} but got {out}"
    )


def test_memory_warning_is_printed_once():
    myenv["QBATCH_MEM"] = "4"
    try:
        p = command_pipe(
            "qbatch -n -b slurm -N test_memory_warning_is_printed_once -- echo hi"
        )
        out, _ = p.communicate(b"")
    finally:
        del myenv["QBATCH_MEM"]
    assert p.returncode == 0, out
    assert out.decode("utf-8").count("--mem 4 has no unit, using 4G") == 1
    with open(os.path.join(tempdir, "test_memory_warning_is_printed_once.0")) as f:
        assert "#SBATCH --mem=4G\n" in f.read()


# ------------------------------------------------------------------ driver


def test_empty_task_list_returns_without_scripts(make_spec, tmp_path):
    result = qbatchDriver(
        make_spec(
            task_list=["# comment only\n"],
            workdir=str(tmp_path),
            script_folder=str(tmp_path / "scripts"),
            dry_run=True,
        )
    )
    assert result is None
    assert not (tmp_path / "scripts").exists()


def test_driver_does_not_mutate_the_callers_task_list(make_spec, tmp_path):
    tasks = ["# a comment\n", "echo hi\n"]
    qbatchDriver(
        make_spec(
            task_list=tasks,
            logdir=str(tmp_path / "logs"),
            script_folder=str(tmp_path / "scripts"),
            dry_run=True,
        )
    )
    assert tasks == ["# a comment\n", "echo hi\n"]


@pytest.mark.parametrize(
    "scheduler,walltime,warned",
    [
        ("slurm", "1:00:30", True),
        ("slurm", "1:00:00", False),
        ("pbs", "1:00:30", False),
    ],
)
def test_driver_warns_when_the_scheduler_rounds_walltime(
    make_spec, tmp_path, capsys, scheduler, walltime, warned
):
    qbatchDriver(
        make_spec(
            scheduler=scheduler,
            walltime=walltime,
            logdir=str(tmp_path / "logs"),
            script_folder=str(tmp_path / "scripts"),
            dry_run=True,
        )
    )
    message = (
        f"qbatch: warning: slurm counts whole minutes, --walltime {walltime}"
        " rounded up to 1:01:00"
    )
    assert (message in capsys.readouterr().err) is warned


def test_dependency_errors_are_reported_the_same_way(make_spec, monkeypatch):
    def check_output(command):
        raise OSError("squeue exploded")

    monkeypatch.setattr(schedulers.subprocess, "check_output", check_output)
    with pytest.raises(QbatchError) as excinfo:
        qbatchDriver(make_spec(scheduler="slurm", depend=["prep"], dry_run=True))
    assert str(excinfo.value) == (
        "qbatch: error: Error matching depend pattern squeue exploded"
    )


# ------------------------------------------------------------------ submit


def submit_one(make_spec, tmp_path, name="testjob.array", **overrides):
    return submit_scripts(
        [(name, "#!/bin/sh\necho hi\n")],
        make_spec(
            logdir=str(tmp_path / "logs"),
            script_folder=str(tmp_path / "scripts"),
            **overrides,
        ),
    )


def test_submit_reports_missing_scheduler_binary(make_spec, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(QbatchError) as excinfo:
        submit_one(make_spec, tmp_path, scheduler="slurm")
    assert str(excinfo.value) == "qbatch: error: system is slurm but sbatch not found"


def test_missing_binary_message_names_the_real_system(make_spec, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(QbatchError) as excinfo:
        submit_one(make_spec, tmp_path, scheduler="sge")
    assert str(excinfo.value) == "qbatch: error: system is sge but qsub not found"


def test_submit_rejects_parallel_that_is_not_gnu(make_spec, tmp_path, monkeypatch):
    # moreutils parallel does not know --version and prints its usage
    fake = tmp_path / "bin" / "parallel"
    fake.parent.mkdir()
    fake.write_text(
        "#!/bin/sh\necho 'parallel [OPTIONS] command -- arguments'\nexit 1\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake.parent))
    with pytest.raises(QbatchError) as excinfo:
        submit_one(make_spec, tmp_path, scheduler="local")
    assert str(excinfo.value) == "qbatch: error: parallel on PATH is not GNU parallel"


def test_dry_run_needs_no_scheduler_binaries(make_spec, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    written = submit_one(make_spec, tmp_path, scheduler="slurm", dry_run=True)
    assert written == [str(tmp_path / "scripts" / "testjob.array")]
    assert (tmp_path / "scripts" / "testjob.array").exists()


def test_container_needs_no_binaries_and_files_are_executable(
    make_spec, tmp_path, monkeypatch
):
    monkeypatch.setenv("PATH", "")
    (path,) = submit_one(
        make_spec, tmp_path, name="testjob.meta", scheduler="container"
    )
    assert os.stat(path).st_mode & stat.S_IXUSR
