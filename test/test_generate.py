#!/usr/bin/env python
"""Unit tests for the generate phase.

These call generate_scripts/submit_scripts in-process and assert on the
returned script text. No tempdir, no PATH requirements, no CLI.
"""
import pytest

from qbatch.qbatch import (QbatchError, generate_scripts, qbatchDriver,
                           submit_scripts)


def base_kwargs(**overrides):
    kwargs = {
        'task_list': ['echo hello\n'],
        'jobname': 'testjob',
        'walltime': None,
        'chunksize': 1,
        'cores': '1',
        'ppj': 1,
        'mem': '0',
        'queue': None,
        'verbose': False,
        'depend': None,
        'workdir': '/work',
        'logdir': '/work/logs',
        'options': [],
        'header': None,
        'footer': None,
        'nodes': 1,
        'sge_pe': 'smp',
        'memvars': 'mem',
        'pbs_nodes_spec': None,
        'individual': False,
        'system': 'local',
        'env': 'none',
        'shell': '/bin/sh',
        'block': False,
    }
    kwargs.update(overrides)
    return kwargs


def test_pbs_header_directives():
    scripts = generate_scripts(base_kwargs(
        system='pbs', walltime='1:00:00', queue='fast',
        depend_job_ids=['1', '2'], depend_array_ids=['3[]']))
    (name, text), = scripts
    assert name == 'testjob.0'
    assert '#PBS -N testjob' in text
    assert '#PBS -l walltime=1:00:00' in text
    assert '#PBS -q fast' in text
    assert '-W depend=afterok:1:2,afterokarray:3[]' in text
    assert 'ARRAY_IND=$PBS_ARRAYID' in text


def test_sge_hold_jid_from_patterns():
    scripts = generate_scripts(base_kwargs(
        system='sge', depend=['prejob*']))
    _, text = scripts[0]
    assert "-hold_jid 'prejob*'" in text


def test_slurm_walltime_colons_passthrough():
    scripts = generate_scripts(base_kwargs(system='slurm',
                                           walltime='1:00:00'))
    assert '#SBATCH --time=1:00:00' in scripts[0][1]


def test_slurm_walltime_seconds_to_minutes():
    scripts = generate_scripts(base_kwargs(system='slurm', walltime='3600'))
    assert '#SBATCH --time=60' in scripts[0][1]


def test_slurm_dependency_ids():
    scripts = generate_scripts(base_kwargs(
        system='slurm', depend_job_ids=['11', '22']))
    assert '--dependency=afterok:11:22' in scripts[0][1]


def test_individual_chunk_slicing():
    tasks = ['echo {0}\n'.format(i) for i in range(4)]
    scripts = generate_scripts(base_kwargs(
        system='sge', task_list=tasks, chunksize=2, individual=True))
    names = [name for name, _ in scripts]
    assert names == ['testjob.0', 'testjob.1']
    assert 'echo 0\necho 1\n' in scripts[0][1]
    assert 'echo 2' not in scripts[0][1]
    assert 'echo 2\necho 3\n' in scripts[1][1]


def test_array_script_body():
    tasks = ['echo {0}\n'.format(i) for i in range(4)]
    scripts = generate_scripts(base_kwargs(
        system='sge', task_list=tasks, chunksize=2, cores='2'))
    (name, text), = scripts
    assert name == 'testjob.array'
    assert 'CHUNK_SIZE=2' in text
    assert 'CORES=2' in text
    assert 'export THREADS_PER_COMMAND=' in text
    assert 'sed -n' in text
    assert 'ARRAY_IND=$SGE_TASK_ID' in text


def test_container_meta_from_data():
    scripts = generate_scripts(base_kwargs(
        system='container', task_list=['echo a\n', 'echo b\n'],
        container_meta='-b container --'))
    assert dict(scripts) == {
        'testjob.joblist': 'echo a\necho b\n',
        'testjob.meta': '-b container --'}


def test_empty_task_list_returns_without_scripts(tmp_path):
    result = qbatchDriver(**base_kwargs(
        task_list=['# comment only\n'],
        workdir=str(tmp_path),
        logdir='{workdir}/logs',
        script_folder=str(tmp_path / 'scripts'),
        dryrun=True))
    assert result is None
    assert not (tmp_path / 'scripts').exists()


def test_unknown_scheduler_raises():
    with pytest.raises(QbatchError):
        generate_scripts(base_kwargs(system='bogus'))


def test_dry_run_needs_no_scheduler_binaries(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', '')
    written = submit_scripts(
        [('testjob.array', '#!/bin/sh\necho hi\n')],
        base_kwargs(system='slurm', dryrun=True,
                    logdir=str(tmp_path / 'logs'),
                    script_folder=str(tmp_path / 'scripts')))
    assert written == [str(tmp_path / 'scripts' / 'testjob.array')]
    assert (tmp_path / 'scripts' / 'testjob.array').exists()
