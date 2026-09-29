"""Directory-relative file operations with no symlink traversal, including races."""
import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .common import Rejected, text
from .privacy import inspect

TEXT_EXTENSIONS = {".txt", ".md", ".py", ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".sh", ".drawio", ".xml", ".ini", ".cfg", ".rst", ".html", ".css", ".js", ".ts"}
DENIED_NAMES = {"secrets", "keys", "credentials", "id_rsa", "id_ed25519"}


class Workspace:
    def __init__(self, project="/workspace/project", playground="/workspace/playground"):
        self.roots = {"project": Path(project), "playground": Path(playground)}

    def parts(self, value):
        value = text(value, 1024)
        path = PurePosixPath(value)
        if path.is_absolute() or "\\" in value or not path.parts or path.parts[0] not in self.roots:
            raise Rejected("Use project/... or playground/... relative paths.")
        if any(p in ("..", "") or p.startswith(".") or p.lower() in DENIED_NAMES for p in path.parts):
            raise Rejected("Hidden, parent, or credential paths are not permitted.")
        return path.parts

    @contextmanager
    def directory(self, parts):
        descriptors = []
        try:
            fd = os.open(self.roots[parts[0]], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(fd)
            for part in parts[1:]:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                descriptors.append(fd)
            yield fd
        except OSError:
            raise Rejected("Path is unavailable or contains a symlink; only real workspace directories are allowed.") from None
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def listing(self, path):
        parts = self.parts(path)
        with self.directory(parts) as fd:
            result = []
            for name in sorted(os.listdir(fd))[:500]:
                if name.startswith(".") or name.lower() in DENIED_NAMES:
                    continue
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode):
                    result.append({"name": name, "directory": stat.S_ISDIR(info.st_mode), "bytes": info.st_size})
            return result

    def read(self, path, max_bytes=2048, start_line=1):
        parts = self.parts(path)
        if len(parts) < 2 or (Path(parts[-1]).suffix.lower() not in TEXT_EXTENSIONS and parts[-1] not in ("LICENSE", "Dockerfile", "Makefile")):
            raise Rejected("Only supported UTF-8 text files can be read.")
        with self.directory(parts[:-1]) as directory:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 262144:
                    raise Rejected("Only regular text files up to 256 KiB are supported.")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    data = stream.read(262145)
            finally:
                os.close(fd)
            if len(data) > 262144:
                raise Rejected("File changed or exceeds the size limit.")
            try:
                content = data.decode("utf-8")
            except UnicodeError:
                raise Rejected("Binary files are not supported.") from None
            text(content, 262144)
            finding = inspect(content, restricted="restricted" in [p.lower() for p in parts],
                              confidential="confidential" in [p.lower() for p in parts])
            lines = content.splitlines(keepends=True)
            if type(start_line) is not int or start_line < 1:
                raise Rejected("start_line must be a positive integer.")
            excerpt = "".join(lines[start_line - 1:]).encode()[:max_bytes].decode("utf-8", errors="ignore")
            return {"path": path, "text": excerpt, "start_line": start_line,
                    "total_lines": len(lines), "truncated": len("".join(lines[start_line - 1:]).encode()) > max_bytes,
                    "restricted": finding.decision == "block", "reasons": finding.reasons,
                    "confidential": "confidential or NDA material" in finding.reasons}

    def write(self, path, content, overwrite=False):
        parts = self.parts(path)
        if parts[0] != "playground" or len(parts) < 2:
            raise Rejected("File writes are confined to playground; runtime code is read-only.")
        if Path(parts[-1]).suffix.lower() not in TEXT_EXTENSIONS:
            raise Rejected("Unsupported output file type.")
        content = text(content, 65536)
        if inspect(content).decision == "block":
            raise Rejected("Refusing to save detected credentials or no-export markers through a model tool.")
        with self.directory(parts[:-1]) as directory:
            temporary = ".sebastian-" + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    info = None
                if info and (not overwrite or not stat.S_ISREG(info.st_mode)):
                    raise Rejected("Destination exists or is not a regular file; explicit overwrite is required.")
                if not overwrite:
                    os.link(temporary, parts[-1], src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                    os.unlink(temporary, dir_fd=directory)
                else:
                    os.replace(temporary, parts[-1], src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
        return {"saved": path, "bytes": len(content.encode())}

    def mkdir(self, path):
        parts = self.parts(path)
        if parts[0] != "playground" or len(parts) < 2:
            raise Rejected("Directories may only be created under playground.")
        with self.directory(parts[:-1]) as directory:
            os.mkdir(parts[-1], mode=0o700, dir_fd=directory)
        return {"created": path}
