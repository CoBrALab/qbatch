"""Tests for the job spec: defaults, validation and argparse names."""

import pytest

from qbatch.schedulers import REGISTRY
from qbatch.spec import SCHEDULERS, JobSpec, QbatchError, parse_mem, parse_walltime


def test_defaults_read_from_environment(monkeypatch):
    monkeypatch.setenv("QBATCH_SYSTEM", "slurm")
    monkeypatch.setenv("QBATCH_PPJ", "12")
    monkeypatch.setenv("QBATCH_QUEUE", "gpu")
    monkeypatch.setenv("QBATCH_MEM", "8G")
    s = JobSpec()
    assert s.scheduler == "slurm"
    assert s.ppj == 12
    assert s.queue == "gpu"
    assert s.mem == "8G"
    assert s.mem_mib == 8192
    # chunk size and cores fall back to ppj
    assert s.chunk_size == 12
    assert s.cores == 12


def test_chunk_size_and_cores_follow_an_explicit_ppj(monkeypatch):
    monkeypatch.delenv("QBATCH_CHUNKSIZE", raising=False)
    monkeypatch.delenv("QBATCH_CORES", raising=False)
    monkeypatch.setenv("QBATCH_PPJ", "2")
    s = JobSpec(ppj=8)
    assert (s.chunk_size, s.cores) == (8, 8)


def test_environment_chunk_size_and_cores_win_over_ppj(monkeypatch):
    monkeypatch.setenv("QBATCH_CHUNKSIZE", "3")
    monkeypatch.setenv("QBATCH_CORES", "50%")
    s = JobSpec(ppj=8)
    assert (s.chunk_size, s.cores) == (3, "50%")


def test_each_construction_rereads_the_environment(monkeypatch):
    monkeypatch.setenv("QBATCH_PPJ", "4")
    first = JobSpec()
    monkeypatch.setenv("QBATCH_PPJ", "8")
    second = JobSpec()
    assert (first.ppj, second.ppj) == (4, 8)


def test_logdir_is_built_from_workdir():
    assert JobSpec(workdir="/scratch/run").logdir == "/scratch/run/logs"


def test_explicit_logdir_is_left_alone():
    assert JobSpec(workdir="/scratch", logdir="/var/log/q").logdir == "/var/log/q"


def test_scheduler_names_come_from_the_registry():
    assert tuple(REGISTRY) == SCHEDULERS


def test_from_kwargs_maps_argparse_names():
    s = JobSpec.from_kwargs(jobname="j", dryrun=True, chunksize=7, system="pbs")
    assert (s.job_name, s.dry_run, s.chunk_size, s.scheduler) == ("j", True, 7, "pbs")


@pytest.mark.parametrize("name", ["mem_mib", "walltime_seconds", "warnings"])
def test_from_kwargs_rejects_fields_set_by_the_spec(name):
    with pytest.raises(QbatchError) as excinfo:
        JobSpec.from_kwargs(**{name: 1})
    assert name in str(excinfo.value)


def test_from_kwargs_rejects_unknown_options():
    with pytest.raises(QbatchError) as excinfo:
        JobSpec.from_kwargs(not_an_option=1)
    assert "not_an_option" in str(excinfo.value)


@pytest.mark.parametrize(
    "bad",
    [
        {"scheduler": "bogus"},
        {"env": "bogus"},
        {"ppj": 0},
        {"chunk_size": -1},
        {"cores": "half"},
    ],
)
def test_validation_rejects_bad_input(bad):
    with pytest.raises(QbatchError):
        JobSpec(**bad)


def test_unknown_scheduler_message_lists_the_choices():
    with pytest.raises(QbatchError) as excinfo:
        JobSpec(scheduler="bogus")
    assert str(excinfo.value) == (
        "qbatch: error: unknown system bogus, expected one of"
        " pbs, sge, slurm, local, container"
    )


# ------------------------------------------------------------------ memory


@pytest.mark.parametrize(
    "text,mib",
    [
        ("4G", 4096),
        ("4g", 4096),
        ("4GB", 4096),
        ("4gb", 4096),
        ("4GiB", 4096),
        ("4 G", 4096),
        (" 4G ", 4096),
        ("1536M", 1536),
        ("1536MB", 1536),
        ("1048576K", 1024),
        ("1T", 1048576),
        ("1P", 1073741824),
        ("1.5G", 1536),
        ("0.5g", 512),
        ("010G", 10240),
        ("2", 2048),
        (2, 2048),
    ],
)
def test_parse_mem_reads_numbers_and_units(text, mib):
    assert parse_mem(text)[0] == mib


@pytest.mark.parametrize(
    "text", [None, "", "  ", "none", "None", "0", "0G", "00", "0.0m"]
)
def test_parse_mem_zero_and_none_mean_no_request(text):
    assert parse_mem(text) == (None, None)


@pytest.mark.parametrize(
    "text", ["4X", "G", "-1G", "1.2.3G", "4GG", "1,5G", "4 G B", ".5G"]
)
def test_parse_mem_rejects_what_it_cannot_read(text):
    with pytest.raises(QbatchError) as excinfo:
        parse_mem(text)
    assert str(excinfo.value).startswith(f"qbatch: error: cannot read --mem {text}")


@pytest.mark.parametrize(
    "text,warning",
    [
        ("4G", None),
        ("1.5G", None),
        ("4", "qbatch: warning: --mem 4 has no unit, using 4G"),
        ("1.5", "qbatch: warning: --mem 1.5 has no unit, using 1536M"),
        ("512K", "qbatch: warning: --mem 512K rounded up to 1M"),
        ("1.1G", "qbatch: warning: --mem 1.1G rounded up to 1127M"),
    ],
)
def test_parse_mem_warns_only_when_the_value_changes(text, warning):
    assert parse_mem(text)[1] == warning


def test_spec_collects_the_memory_warning():
    s = JobSpec(mem="4")
    assert (s.mem_mib, s.warnings) == (
        4096,
        ["qbatch: warning: --mem 4 has no unit, using 4G"],
    )


def test_bad_memory_from_the_environment_is_an_error(monkeypatch):
    monkeypatch.setenv("QBATCH_MEM", "lots")
    with pytest.raises(QbatchError):
        JobSpec()


# ---------------------------------------------------------------- walltime


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("3600", 3600),
        (3600, 3600),
        ("90.5", 91),
        # two fields are MM:SS, but with a day they are HH:MM, as in Slurm
        ("12:30", 750),
        ("1-12:30", 131400),
        ("1:00:00", 3600),
        ("36:00:00", 129600),
        ("90:00", 5400),
        ("0:90:00", 5400),
        ("1-12", 129600),
        ("1-12:30:15", 131415),
        ("2h30m", 9000),
        ("90m", 5400),
        ("1d12h", 129600),
        ("1h 30m", 5400),
        ("1.5h", 5400),
        ("2H", 7200),
        ("45s", 45),
        ("1.5s", 2),
    ],
)
def test_parse_walltime_reads_every_form(text, seconds):
    assert parse_walltime(text)[0] == seconds


@pytest.mark.parametrize(
    "text", [None, "", "none", "NONE", "0", "0:00:00", "0h", "0-0", "0.0"]
)
def test_parse_walltime_zero_and_none_mean_no_limit(text):
    assert parse_walltime(text) == (None, None)


@pytest.mark.parametrize(
    "text",
    [
        "1::1",
        "1.5:00:00",
        "1-",
        "2m1h",
        "abc",
        "1:2:3:4",
        "-1",
        "h",
        ":30",
        "1:30:",
        "10:00.5",
        "1-2-3",
    ],
)
def test_parse_walltime_rejects_what_it_cannot_read(text):
    with pytest.raises(QbatchError) as excinfo:
        parse_walltime(text)
    assert str(excinfo.value).startswith(
        f"qbatch: error: cannot read --walltime {text}"
    )


@pytest.mark.parametrize(
    "text,warning",
    [
        ("1:00:00", None),
        ("1.5h", None),
        (
            "3600",
            "qbatch: warning: --walltime 3600 has no unit, using seconds (1:00:00)",
        ),
        ("1.5s", "qbatch: warning: --walltime 1.5s rounded up to 0:00:02"),
    ],
)
def test_parse_walltime_warns_only_when_the_value_changes(text, warning):
    assert parse_walltime(text)[1] == warning


def test_spec_collects_memory_and_walltime_warnings():
    s = JobSpec(mem="4", walltime="60")
    assert s.warnings == [
        "qbatch: warning: --mem 4 has no unit, using 4G",
        "qbatch: warning: --walltime 60 has no unit, using seconds (0:01:00)",
    ]
