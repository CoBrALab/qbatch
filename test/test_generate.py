#!/usr/bin/env python
"""Unit tests for the job spec and the generate phase.

These build a JobSpec and call generate_scripts/submit_scripts
in-process, asserting on the returned script text. No tempdir, no PATH
requirements, no CLI.
"""
import pytest

from qbatch.qbatch import (compute_threads, generate_scripts, qbatchDriver,
                           submit_scripts)
from qbatch.spec import JobSpec, QbatchError


def spec(**overrides):
    """A spec with every environment-derived field pinned, so the
    developer's own QBATCH_* settings cannot change the result."""
    values = {
        'task_list': ['echo hello\n'],
        'job_name': 'testjob',
        'chunk_size': 1,
        'cores': '1',
        'ppj': 1,
        'mem': '0',
        'memvars': 'mem',
        'queue': None,
        'nodes': 1,
        'sge_pe': 'smp',
        'options': [],
        'workdir': '/work',
        'logdir': '/work/logs',
        'script_folder': '.qbatch/',
        'scheduler': 'local',
        'env': 'none',
        'shell': '/bin/sh',
        'environ': {},
    }
    values.update(overrides)
    return JobSpec(**values)


# ---------------------------------------------------------------- generate

def test_pbs_header_directives():
    scripts = generate_scripts(spec(
        scheduler='pbs', walltime='1:00:00', queue='fast',
        depend_job_ids=['1', '2'], depend_array_ids=['3[]']))
    (name, text), = scripts
    assert name == 'testjob.0'
    assert '#PBS -N testjob' in text
    assert '#PBS -l walltime=1:00:00' in text
    assert '#PBS -q fast' in text
    assert '-W depend=afterok:1:2,afterokarray:3[]' in text
    assert 'ARRAY_IND=$PBS_ARRAYID' in text


def test_pbs_dependencies_array_only():
    scripts = generate_scripts(spec(
        scheduler='pbs', depend_array_ids=['9[]']))
    assert '-W depend=afterokarray:9[]' in scripts[0][1]


def test_sge_hold_jid_from_patterns():
    scripts = generate_scripts(spec(scheduler='sge', depend=['prejob*']))
    assert "-hold_jid 'prejob*'" in scripts[0][1]


def test_sge_parallel_environment():
    scripts = generate_scripts(spec(scheduler='sge', ppj=8, sge_pe='smp'))
    assert '#$ -pe smp 8' in scripts[0][1]


def test_slurm_walltime_colons_passthrough():
    scripts = generate_scripts(spec(scheduler='slurm', walltime='1:00:00'))
    assert '#SBATCH --time=1:00:00' in scripts[0][1]


def test_slurm_walltime_seconds_to_minutes():
    scripts = generate_scripts(spec(scheduler='slurm', walltime='3600'))
    assert '#SBATCH --time=60' in scripts[0][1]


def test_slurm_dependency_ids():
    scripts = generate_scripts(spec(
        scheduler='slurm', depend_job_ids=['11', '22']))
    assert '--dependency=afterok:11:22' in scripts[0][1]


def test_individual_chunk_slicing():
    tasks = ['echo {0}\n'.format(i) for i in range(4)]
    scripts = generate_scripts(spec(
        scheduler='sge', task_list=tasks, chunk_size=2, individual=True))
    names = [name for name, _ in scripts]
    assert names == ['testjob.0', 'testjob.1']
    assert 'echo 0\necho 1\n' in scripts[0][1]
    assert 'echo 2' not in scripts[0][1]
    assert 'echo 2\necho 3\n' in scripts[1][1]


def test_array_script_body():
    tasks = ['echo {0}\n'.format(i) for i in range(4)]
    scripts = generate_scripts(spec(
        scheduler='sge', task_list=tasks, chunk_size=2, cores='2'))
    (name, text), = scripts
    assert name == 'testjob.array'
    assert 'CHUNK_SIZE=2' in text
    assert 'CORES=2' in text
    assert 'export THREADS_PER_COMMAND=' in text
    assert 'sed -n' in text
    assert 'ARRAY_IND=$SGE_TASK_ID' in text


def test_footer_appended():
    scripts = generate_scripts(spec(scheduler='pbs', footer=['echo done']))
    assert scripts[0][1].endswith('echo done')


def test_container_meta_from_data():
    scripts = generate_scripts(spec(
        scheduler='container', task_list=['echo a\n', 'echo b\n'],
        container_meta='-b container --'))
    assert dict(scripts) == {
        'testjob.joblist': 'echo a\necho b\n',
        'testjob.meta': '-b container --'}


def test_env_copied_uses_spec_environ_not_os_environ():
    scripts = generate_scripts(spec(
        scheduler='pbs', env='copied', environ={'MYVAR': 'myvalue'}))
    text = scripts[0][1]
    assert 'export MYVAR="myvalue"' in text
    assert 'PATH=' not in text


def test_env_copied_skips_ignored_vars():
    scripts = generate_scripts(spec(
        scheduler='pbs', env='copied',
        environ={'PWD': '/somewhere', 'KEEP': 'yes'}))
    text = scripts[0][1]
    assert 'export KEEP="yes"' in text
    assert 'PWD' not in text


# ------------------------------------------------------------------ driver

def test_empty_task_list_returns_without_scripts(tmp_path):
    result = qbatchDriver(spec(
        task_list=['# comment only\n'],
        workdir=str(tmp_path),
        script_folder=str(tmp_path / 'scripts'),
        dry_run=True))
    assert result is None
    assert not (tmp_path / 'scripts').exists()


def test_driver_does_not_mutate_the_callers_task_list(tmp_path):
    tasks = ['# a comment\n', 'echo hi\n']
    qbatchDriver(spec(
        task_list=tasks,
        workdir=str(tmp_path),
        logdir=str(tmp_path / 'logs'),
        script_folder=str(tmp_path / 'scripts'),
        dry_run=True))
    assert tasks == ['# a comment\n', 'echo hi\n']


def test_dry_run_needs_no_scheduler_binaries(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', '')
    written = submit_scripts(
        [('testjob.array', '#!/bin/sh\necho hi\n')],
        spec(scheduler='slurm', dry_run=True,
             logdir=str(tmp_path / 'logs'),
             script_folder=str(tmp_path / 'scripts')))
    assert written == [str(tmp_path / 'scripts' / 'testjob.array')]
    assert (tmp_path / 'scripts' / 'testjob.array').exists()


# -------------------------------------------------------------------- spec

def test_defaults_read_from_environment(monkeypatch):
    monkeypatch.setenv('QBATCH_SYSTEM', 'slurm')
    monkeypatch.setenv('QBATCH_PPJ', '12')
    monkeypatch.setenv('QBATCH_QUEUE', 'gpu')
    monkeypatch.setenv('QBATCH_MEM', '8G')
    s = JobSpec()
    assert s.scheduler == 'slurm'
    assert s.ppj == 12
    assert s.queue == 'gpu'
    assert s.mem == '8G'
    # chunk size and cores fall back to ppj
    assert s.chunk_size == 12
    assert s.cores == '12'


def test_each_construction_rereads_the_environment(monkeypatch):
    monkeypatch.setenv('QBATCH_PPJ', '4')
    first = JobSpec()
    monkeypatch.setenv('QBATCH_PPJ', '8')
    second = JobSpec()
    assert (first.ppj, second.ppj) == (4, 8)


def test_logdir_is_built_from_workdir():
    assert JobSpec(workdir='/scratch/run').logdir == '/scratch/run/logs'


def test_explicit_logdir_is_left_alone():
    assert JobSpec(workdir='/scratch', logdir='/var/log/q').logdir == \
        '/var/log/q'


def test_from_kwargs_maps_argparse_names():
    s = JobSpec.from_kwargs(jobname='j', dryrun=True, chunksize=7,
                            system='pbs')
    assert (s.job_name, s.dry_run, s.chunk_size, s.scheduler) == \
        ('j', True, 7, 'pbs')


def test_from_kwargs_rejects_unknown_options():
    with pytest.raises(QbatchError) as excinfo:
        JobSpec.from_kwargs(not_an_option=1)
    assert 'not_an_option' in str(excinfo.value)


@pytest.mark.parametrize('bad', [
    {'scheduler': 'bogus'},
    {'env': 'bogus'},
    {'ppj': 0},
    {'chunk_size': -1},
    {'cores': 'half'},
])
def test_validation_rejects_bad_input(bad):
    with pytest.raises(QbatchError):
        JobSpec(**bad)


@pytest.mark.parametrize('cores,expected', [
    ('2', 4), (2, 4), ('50%', 4), (8, 1),
])
def test_compute_threads_accepts_int_or_percent(cores, expected):
    assert compute_threads(spec(ppj=8, cores=cores)) == expected


def test_cores_as_int_reaches_the_script():
    scripts = generate_scripts(spec(scheduler='sge', cores=4, ppj=4,
                                    task_list=['a\n', 'b\n'], chunk_size=1))
    assert 'CORES=4' in scripts[0][1]
