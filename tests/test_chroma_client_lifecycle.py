"""Client lifecycle operations never nest locks for different palace files."""

import subprocess
import sys
import textwrap

import pytest

_COMMON = """
import os
import random
import threading
import time
from pathlib import Path
from chromadb.api.client import Client
from chromadb.api.shared_system_client import SharedSystemClient
from mempalace.backends import chroma, _inproc_sqlite
from mempalace.backends.chroma import ChromaBackend
from mempalace.backends.base import PalaceRef

root = Path(sys.argv[1])
paths = [str(root / name) for name in ('p', 'q', 'r')]
options = {'caller_vectors': True}
seed = ChromaBackend()
for path in paths:
    col = seed.get_collection(palace=PalaceRef(id=path, local_path=path), collection_name='mempalace_drawers', create=True, options=options)
    col.add(ids=['a'], documents=['quartz note'], embeddings=[[1., 0.]], metadatas=[{'wing': 'public'}])
seed.close()
closes = []
original_close = Client.close
def checked_close(client):
    if not client._closed:
        key = _inproc_sqlite._key(os.path.join(client._identifier, 'chroma.sqlite3'))
        assert _inproc_sqlite.held_palace_db_paths() == {key}
        closes.append(key)
    return original_close(client)
Client.close = checked_close
errors = []
def check_finished(threads):
    for thread in threads:
        thread.join(15)
    assert not any(thread.is_alive() for thread in threads), 'lifecycle deadlock'
    assert not errors, errors

def check_released():
    assert closes
    for path in paths:
        key = _inproc_sqlite._key(os.path.join(path, 'chroma.sqlite3'))
        assert key not in _inproc_sqlite._anchors
        assert key not in _inproc_sqlite._holders
        assert path not in SharedSystemClient._identifier_to_system
        assert not SharedSystemClient._identifier_to_refcount.get(path, 0)
"""


def _run_probe(tmp_path, body):
    # A deadlock fails the test without leaving blocked threads in pytest.
    result = subprocess.run(
        [sys.executable, "-c", "import sys\n" + textwrap.dedent(_COMMON + body), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_concurrent_reopens_do_not_nest_palace_locks(tmp_path):
    _run_probe(
        tmp_path,
        """
backends = [ChromaBackend(), ChromaBackend()]
for backend in backends:
    for path in paths[:2]:
        backend.get_collection(palace=PalaceRef(id=path, local_path=path), collection_name='mempalace_drawers', options=options)
barrier = threading.Barrier(2, timeout=5)
original_drain = ChromaBackend._drain_clients
local = threading.local()
def synchronized_drain(self, *args, **kwargs):
    if not getattr(local, 'arrived', False):
        local.arrived = True
        barrier.wait()
    return original_drain(self, *args, **kwargs)
ChromaBackend._drain_clients = synchronized_drain
for path in paths[:2]:
    stamp = time.time() + 5
    os.utime(os.path.join(path, 'chroma.sqlite3'), (stamp, stamp))
def reopen(backend, path):
    try:
        backend.get_collection(palace=PalaceRef(id=path, local_path=path), collection_name='mempalace_drawers', options=options)
    except BaseException as error:
        errors.append(repr(error))
threads = [threading.Thread(target=reopen, args=pair, daemon=True) for pair in zip(backends, paths)]
for thread in threads:
    thread.start()
check_finished(threads)
ChromaBackend._drain_clients = original_drain
for backend in backends:
    backend.close()
check_released()
""",
    )


@pytest.mark.skipif(sys.platform == "win32", reason="bounded concurrent POSIX lifecycle probe")
def test_three_palace_random_lifecycle_stress(tmp_path):
    _run_probe(
        tmp_path,
        """
barrier = threading.Barrier(3)
operations = [0, 0, 0]
def exercise(index):
    backend = ChromaBackend()
    rng = random.Random(index)
    try:
        barrier.wait(timeout=5)
        end = time.monotonic() + 10
        while time.monotonic() < end:
            path = rng.choice(paths)
            with _inproc_sqlite.palace_db_lock(os.path.join(path, 'chroma.sqlite3')):
                col = backend.get_collection(palace=PalaceRef(id=path, local_path=path), collection_name='mempalace_drawers', options=options)
                assert col.query(query_embeddings=[[1., 0.]], n_results=1).ids == [['a']]
                if rng.randrange(2):
                    backend.close_palace(path)
            operations[index] += 1
    except BaseException as error:
        errors.append(repr(error))
    finally:
        backend.close()
threads = [threading.Thread(target=exercise, args=(i,), daemon=True) for i in range(3)]
for thread in threads:
    thread.start()
check_finished(threads)
assert min(operations) > 3, operations
check_released()
""",
    )


def test_reopen_preserves_other_palace_live_system(tmp_path):
    import os
    import time

    from mempalace.backends.base import PalaceRef
    from mempalace.backends.chroma import ChromaBackend

    backend = ChromaBackend()
    paths = [str(tmp_path / name) for name in ("p", "q")]
    try:
        for path in paths:
            col = backend.get_collection(
                palace=PalaceRef(id=path, local_path=path),
                collection_name="mempalace_drawers",
                create=True,
                options={"caller_vectors": True},
            )
            col.add(ids=["a"], documents=["quartz"], embeddings=[[1.0, 0.0]])
        other = backend.get_collection(
            palace=PalaceRef(id=paths[1], local_path=paths[1]),
            collection_name="mempalace_drawers",
            options={"caller_vectors": True},
        )
        client = backend._client(paths[1])
        system = client._system
        assert backend._client(paths[0]).get_settings().persist_directory == paths[0]
        assert client.get_settings().persist_directory == paths[1]
        stamp = time.time() + 5
        os.utime(os.path.join(paths[0], "chroma.sqlite3"), (stamp, stamp))
        backend.get_collection(
            palace=PalaceRef(id=paths[0], local_path=paths[0]),
            collection_name="mempalace_drawers",
            options={"caller_vectors": True},
        )
        assert backend._client(paths[1]) is client
        assert client._system is system
        assert other.get(ids=["a"]).documents == ["quartz"]
    finally:
        backend.close()
