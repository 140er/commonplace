"""Private, descriptor-relative owned files; no path follows a symlink."""

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from .install_host import InstallError

STATE = ".commonplace-runner"
MANIFEST = STATE + "/manifest.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


class Files:
    def __init__(self, home):
        self.home = Path(home).absolute()
        if self.home.resolve() != self.home or any(ord(c) < 32 for c in str(self.home)):
            raise InstallError("unsafe installation home")
        self.uid = os.getuid()
        self._check(self.home.stat(), directory=True)

    def _check(self, info, *, directory=False):
        valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        mode = stat.S_IMODE(info.st_mode)
        if (
            not valid_type
            or info.st_uid != self.uid
            or (mode & 0o022 if directory else mode != 0o600)
            or (not directory and info.st_nlink != 1)
        ):
            raise InstallError("unsafe installation ownership, type or permissions")

    @contextmanager
    def parent(self, relative):
        parts = Path(relative).parts
        if not parts or Path(relative).is_absolute() or any(p in ("..", ".") for p in parts):
            raise InstallError("unsafe installation path")
        fd = os.open(self.home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self._check(os.fstat(fd), directory=True)
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
                self._check(os.fstat(fd), directory=True)
            yield fd, parts[-1]
        finally:
            os.close(fd)

    def info(self, path):
        try:
            with self.parent(path) as (fd, name):
                return os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def read(self, path):
        with self.parent(path) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                self._check(os.fstat(fd))
                # Runtime artifacts have a separate bounded release budget;
                # credentials and journals retain their smaller existing limit.
                limit = 32 * 1024 * 1024
                if path.startswith(STATE + "/runtime/") and path.endswith(".artifact"):
                    from .runtime_manifest import MAX_ARTIFACT

                    limit = MAX_ARTIFACT
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    data = stream.read(limit + 1)
                if len(data) > limit:
                    raise InstallError("installation file is too large")
                return data
            finally:
                os.close(fd)

    def mkdir(self, path):
        with self.parent(path) as (fd, name):
            os.mkdir(name, mode=0o700, dir_fd=fd)
            os.fsync(fd)

    def empty(self, path):
        with self.parent(path) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                self._check(os.fstat(fd), directory=True)
                return not os.listdir(fd)
            finally:
                os.close(fd)

    def write(self, path, data, *, replace=False):
        with self.parent(path) as (parent, name):
            if replace:
                self.read(path)  # validate before replacing an owned file
            temporary = name + ".pending"
            if self.info(path + ".pending") is not None:
                self.remove(path + ".pending")
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            try:
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(fd)
                if replace:
                    os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                else:
                    # Refuse a concurrently occupied destination before atomic publication.
                    if self.info(path) is not None:
                        raise InstallError("installation path is already occupied")
                    os.rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            except BaseException:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
                raise
            finally:
                os.close(fd)

    def remove(self, path, *, directory=False):
        with self.parent(path) as (fd, name):
            if directory:
                self._check(os.stat(name, dir_fd=fd, follow_symlinks=False), directory=True)
                os.rmdir(name, dir_fd=fd)
            else:
                self.read(path)
                os.unlink(name, dir_fd=fd)
            os.fsync(fd)

    def check_link(self, info):
        """Validate the link itself, never its destination, for private cleanup."""
        if not stat.S_ISLNK(info.st_mode) or info.st_uid != self.uid or info.st_nlink != 1:
            raise InstallError("unsafe installation link ownership or type")

    def remove_link(self, path):
        with self.parent(path) as (fd, name):
            self.check_link(os.stat(name, dir_fd=fd, follow_symlinks=False))
            os.unlink(name, dir_fd=fd)
            os.fsync(fd)
