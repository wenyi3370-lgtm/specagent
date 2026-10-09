"""OS locks shared by host threads and processes using the same lock directory."""
import errno
import hashlib
import os
import threading
import time
import tempfile
from pathlib import Path


def lock_path(config, scope='run'):
    identity = os.path.normcase(str(Path(config).resolve())) if config is not None else ''
    configured = os.getenv('SPECAGENT_LOCK_DIR', '').strip()
    if configured:
        directory = Path(configured)
        if not directory.is_absolute():
            raise RuntimeError('SPECAGENT_LOCK_DIR must be absolute')
    else:
        suffix = '-' + str(os.getuid()) if hasattr(os, 'getuid') else ''
        directory = Path(tempfile.gettempdir()).resolve() / ('specagent-locks' + suffix)
    if directory.is_symlink() or directory.resolve() != directory:
        raise RuntimeError('lock directory must not be a link')
    # Read-only project operations must not create files inside the project.
    # The default private per-user temp directory is shared by host workers.
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256((identity + ':' + scope).encode()).hexdigest()
    return directory / (digest + '.lock')


class ProcessLock:
    def __init__(self, path):
        self.path = Path(path)
        self._thread = threading.Lock()
        self._file = None

    def acquire(self, blocking=True):
        if not self._thread.acquire(blocking=blocking):
            return False
        try:
            if self.path.is_symlink() or self.path.resolve() != self.path:
                raise RuntimeError('lock file must not be a link')
            handle = open(self.path, 'a+b')
            if handle.seek(0, 2) == 0:
                handle.write(b'0')
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._file = handle
                    return True
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if not blocking:
                        handle.close()
                        self._thread.release()
                        return False
                    time.sleep(.02)
        except BaseException:
            if 'handle' in locals():
                handle.close()
            self._thread.release()
            raise

    def release(self):
        handle = self._file
        if handle is None:
            raise RuntimeError('release of unlocked process lock')
        self._file = None
        try:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()
            self._thread.release()

    def locked(self):
        if self._thread.locked():
            return True
        if self.acquire(blocking=False):
            self.release()
            return False
        return True

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()
