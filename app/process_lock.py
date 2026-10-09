"""OS locks shared by threads and processes on the same project filesystem."""
import errno
import hashlib
import os
import threading
import time
from pathlib import Path


def lock_path(config, scope='run'):
    config = Path(config).resolve()
    state = config.parent / '.specagent'
    if state.exists() and state.resolve() != config.parent / '.specagent':
        raise RuntimeError('project state directory must not be a link')
    directory = state / 'locks'
    directory.mkdir(parents=True, exist_ok=True)
    if directory.resolve() != state / 'locks':
        raise RuntimeError('project lock directory must not be a link')
    marker = state / '.gitignore'
    if marker.is_symlink() or marker.resolve() != marker:
        raise RuntimeError('project state marker must not be a link')
    marker.touch(exist_ok=True)
    if not marker.read_bytes():
        marker.write_text('*\n', encoding='utf-8')
    digest = hashlib.sha256((os.path.normcase(str(config)) + ':' + scope).encode()).hexdigest()
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
