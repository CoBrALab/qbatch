"""Tests for the job spec: defaults, validation and argparse names."""

import pytest

from qbatch.schedulers import REGISTRY
from qbatch.spec import SCHEDULERS, JobSpec, QbatchError


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
    # chunk size and cores fall back to ppj
    assert s.chunk_size == 12
    assert s.cores == "12"


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
