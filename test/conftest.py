import pytest

from qbatch.spec import JobSpec


@pytest.fixture
def make_spec():
    """Build a spec with every environment-derived field pinned, so the
    developer's own QBATCH_* settings cannot change the result."""

    def make(**overrides):
        values = {
            "task_list": ["echo hello\n"],
            "job_name": "testjob",
            "chunk_size": 1,
            "cores": "1",
            "ppj": 1,
            "mem": "0",
            "memvars": "mem",
            "queue": None,
            "nodes": 1,
            "sge_pe": "smp",
            "options": [],
            "workdir": "/work",
            "logdir": "/work/logs",
            "script_folder": ".qbatch/",
            "scheduler": "local",
            "env": "none",
            "shell": "/bin/sh",
            "environ": {},
        }
        values.update(overrides)
        return JobSpec(**values)

    return make
