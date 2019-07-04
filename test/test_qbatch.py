#!/usr/bin/env python
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
from future import standard_library
from builtins import *
from builtins import str
from builtins import range
import os
import shutil
import shlex
from subprocess import Popen, PIPE, STDOUT
import tempfile
import sys
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
    return Popen(shlex.split(command), stdin=PIPE, stdout=PIPE, stderr=STDOUT, env=myenv)


def test_qbatch_help():
    p = command_pipe('qbatch --help')
    out, _ = p.communicate(''.encode('utf-8'))
    assert p.returncode == 0, p.returncode


def test_qbatch_help_no_queue_binary():
    """Tests that --help works when there is no queuing binary available.

    If QBATCH_SYSTEM is not 'local', using --help should still work.

    This tests for the following issue:
        https://github.com/pipitone/qbatch/issues/177
    """

    myenv['QBATCH_SYSTEM'] = 'slurm'
    try:
        p = command_pipe('qbatch --help')
        out, _ = p.communicate(''.encode('utf-8'))
        assert p.returncode == 0, p.returncode
    finally:
        del myenv['QBATCH_SYSTEM']


def test_python_import():
    p = command_pipe('python -c "from qbatch import qbatchParser"')
    out, _ = p.communicate(''.encode('utf-8'))

    assert p.returncode == 0


def test_python_help_launch():
    p = command_pipe("""python -c "from qbatch import qbatchParser; """ +
                     """qbatchParser(['-h'])" """)
    out, _ = p.communicate(''.encode('utf-8'))

    assert p.returncode == 0


def test_run_qbatch_dryrun_single_output_exists():
    cmds = "\n".join(["echo hello"])
    p = command_pipe('qbatch -N test_run_qbatch_dryrun_single_output_exists -n -')
    out, _ = p.communicate(cmds.encode('utf-8'))

    assert p.returncode == 0
    assert os.path.exists(os.path.join(tempdir, 'test_run_qbatch_dryrun_single_output_exists.0'))


def test_run_qbatch_sge_dryrun_array_piped_chunks():
    chunk_size = 10
    chunks = 5
    outputs = list(range(chunk_size * chunks))

    cmds = "\n".join(['echo {0}'.format(x) for x in outputs])
    p = command_pipe('qbatch -N test_run_qbatch_sge_dryrun_array_piped_chunks --env none -n -j2 \
                     -b sge -c {0} -'.format(chunk_size))
    out, _ = p.communicate(cmds.encode('utf-8'))

    array_script = os.path.join(tempdir, 'test_run_qbatch_sge_dryrun_array_piped_chunks.array')
    assert p.returncode == 0
    assert os.path.exists(array_script)

    for chunk in range(1, chunks + 1):
        myenv['SGE_TASK_ID'] = str(chunk)
        expected = '\n'.join(['echo {0}\t{0}'.format(x) for x in outputs[(
            chunk - 1) * chunk_size:chunk * chunk_size]]) + '\n'
        array_pipe = command_pipe(array_script)
        out, _ = array_pipe.communicate()

        assert array_pipe.returncode == 0, \
            "Chunk {0}: return code = {1}".format(chunk, array_pipe.returncode)
        assert set(out.decode().splitlines()) == set(expected.splitlines()), \
            "Chunk {0}: Expected {1} but got {2}".format(chunk, expected, out)


def test_run_qbatch_pbs_dryrun_array_piped_chunks():
    chunk_size = 10
    chunks = 5
    outputs = list(range(chunk_size * chunks))

    cmds = "\n".join(['echo {0}'.format(x) for x in outputs])
    p = command_pipe('qbatch -N test_run_qbatch_pbs_dryrun_array_piped_chunks --env none -n -j2 \
                     -b pbs -c {0} -'.format(chunk_size))
    out, _ = p.communicate(cmds.encode('utf-8'))

    array_script = os.path.join(tempdir, 'test_run_qbatch_pbs_dryrun_array_piped_chunks.array')
    assert p.returncode == 0
    assert os.path.exists(array_script)

    for chunk in range(1, chunks + 1):
        myenv['PBS_ARRAYID'] = str(chunk)
        expected = '\n'.join(['echo {0}\t{0}'.format(x) for x in outputs[(
            chunk - 1) * chunk_size:chunk * chunk_size]]) + '\n'
        array_pipe = command_pipe(array_script)
        out, _ = array_pipe.communicate()

        assert array_pipe.returncode == 0, \
            "Chunk {0}: return code = {1}".format(chunk, array_pipe.returncode)
        assert set(out.decode().splitlines()) == set(expected.splitlines()), \
            "Chunk {0}: Expected {1} but got {2}".format(chunk, expected, out)


def test_run_qbatch_slurm_dryrun_array_piped_chunks():
    chunk_size = 10
    chunks = 5
    outputs = list(range(chunk_size * chunks))

    cmds = "\n".join(['echo {0}'.format(x) for x in outputs])
    p = command_pipe('qbatch -N test_run_qbatch_slurm_dryrun_array_piped_chunks --env none -n -j2 \
                     -b slurm -c {0} -'.format(chunk_size))
    out, _ = p.communicate(cmds.encode('utf-8'))

    array_script = os.path.join(tempdir, 'test_run_qbatch_slurm_dryrun_array_piped_chunks.array')
    assert p.returncode == 0
    assert os.path.exists(array_script)

    for chunk in range(1, chunks + 1):
        myenv['SLURM_ARRAY_TASK_ID'] = str(chunk)
        expected = '\n'.join(['echo {0}\t{0}'.format(x) for x in outputs[(
            chunk - 1) * chunk_size:chunk * chunk_size]]) + '\n'
        array_pipe = command_pipe(array_script)
        out, _ = array_pipe.communicate()

        assert array_pipe.returncode == 0, \
            "Chunk {0}: return code = {1}".format(chunk, array_pipe.returncode)
        assert set(out.decode().splitlines()) == set(expected.splitlines()), \
            "Chunk {0}: Expected {1} but got {2}".format(chunk, expected, out)


def test_run_qbatch_local_piped_commands():
    cmds = "\n".join(["echo hello"] * 24)
    p = command_pipe('qbatch -N test_run_qbatch_local_piped_commands --env none -j2 -b local -')
    out, _ = p.communicate(cmds.encode('utf-8'))

    expected, _ = command_pipe(
        'parallel --tag --line-buffer -j2').communicate(cmds.encode('utf-8'))

    print(out)

    assert p.returncode == 0, \
        "Return code = {0}".format(p.returncode)
    assert set(out.splitlines()) == set(expected.splitlines()), \
        "Expected {0} but got {1}".format(expected, out)
