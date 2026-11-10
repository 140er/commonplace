"""Test-only POSIX ancestor ownership view; fixture contents remain real.

Container user namespaces may present system-owned ancestors as an unmapped UID.
Model those fixed ancestors as root-owned without changing modes, symlinks, or
any ownership inside the test directory. Custody still uses real files, crypto,
openat, locks and permissions. This never reads ambient keys or changes the host.
"""

import os
from contextlib import contextmanager
from pathlib import Path

import pytest


class AncestorOwnership:
    def __init__(self, root):
        self.ancestors = set()
        for parent in Path(root).parents:
            info = parent.stat()
            self.ancestors.add((info.st_dev, info.st_ino))

    def __getattr__(self, name):
        return getattr(os, name)

    def fstat(self, fd):
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) in self.ancestors:
            fields = list(info)
            fields[4] = 0
            return os.stat_result(fields)
        return info


@contextmanager
def private_filesystem(root):
    from commonplace.compute import credentials

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(credentials, "os", AncestorOwnership(root))
        yield
