from os.path import join, exists
import os
import shutil
import shlex
from subprocess import Popen, PIPE
import tempfile

tempdir = None

# set this to folder that all nodes on the cluster have access to
SHARED_FOLDER = os.getcwd()


def setup_module():
    global tempdir
    tempdir = tempfile.mkdtemp(dir=SHARED_FOLDER)
    os.environ["QBATCH_SCRIPT_FOLDER"] = tempdir
    os.environ["QBATCH_PPN"] = "1"
    os.environ["PATH"] = "./scripts:" + os.environ["PATH"]  # HACK


def teardown_module():
    shutil.rmtree(tempdir)


def command_pipe(command):
    return Popen(shlex.split(command), stdin=PIPE, stdout=PIPE, stderr=PIPE)


def test_run_qbatch_dryrun_array_output_exists():
    cmds = "\n".join(["echo hello"])
    p = command_pipe('qbatch -n')
    out, err = p.communicate(cmds)

    assert p.returncode == 0
    assert exists(join(tempdir, 'STDIN.array'))


def test_run_qbatch_sge_dryrun_array_piped_chunks():
    chunk_size = 10
    chunks = 5
    outputs = range(chunk_size * chunks)

    cmds = "\n".join(map(lambda x: 'echo {}'.format(x), outputs))
    p = command_pipe('qbatch -n -b sge -c {}'.format(chunk_size))
    out, err = p.communicate(cmds)

    array_script = join(tempdir, 'STDIN.array')
    assert p.returncode == 0
    assert exists(array_script)

    for chunk in range(1, chunks + 1):
        os.environ['SGE_TASK_ID'] = str(chunk)
        expected = '\n'.join(
            map(str,
                outputs[(chunk - 1) * chunk_size:chunk * chunk_size])) + '\n'
        array_pipe = command_pipe(array_script)
        out, _ = array_pipe.communicate()

        assert array_pipe.returncode == 0, \
            "Chunk {}: return code = {}".format(chunk, array_pipe.returncode)
        assert out == expected, \
            "Chunk {}: Expected {} but got {}".format(chunk, expected, out)


def test_run_qbatch_pbs_dryrun_array_piped_chunks():
    chunk_size = 10
    chunks = 5
    outputs = range(chunk_size * chunks)

    cmds = "\n".join(map(lambda x: 'echo {}'.format(x), outputs))
    p = command_pipe('qbatch -n -b pbs -c {}'.format(chunk_size))
    out, err = p.communicate(cmds)

    array_script = join(tempdir, 'STDIN.array')
    assert p.returncode == 0
    assert exists(array_script)

    for chunk in range(1, chunks + 1):
        os.environ['PBS_ARRAYID'] = str(chunk)
        expected = '\n'.join(
            map(str,
                outputs[(chunk - 1) * chunk_size:chunk * chunk_size])) + '\n'
        array_pipe = command_pipe(array_script)
        out, _ = array_pipe.communicate()

        assert array_pipe.returncode == 0, \
            "Chunk {}: return code = {}".format(chunk, array_pipe.returncode)
        assert out == expected, \
            "Chunk {}: Expected {} but got {}".format(chunk, expected, out)


def test_run_qbatch_local_piped_commands():
    cmds = "\n".join(["echo hello"] * 24)
    p = command_pipe('qbatch -b local')
    out, err = p.communicate(cmds)

    expected, _ = command_pipe('bash').communicate(cmds)

    assert p.returncode == 0, err
    assert out == expected, out
