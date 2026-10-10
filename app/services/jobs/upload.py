"""Upload jobs: stream a multipart body straight into ``app_data/staging/<job_id>/files/`` (SPEC §5.1, §7).
TR-ING-01.

The body is a sequence of ``path`` (relative path, text) and ``file`` parts. Each path is checked
(``paths.sanitise_relative``) before anything is written. Bytes go to ``staging/<job_id>/tmp/<n>`` and are
moved into place when the part ends. The parser writes only into staging, never into the OS temp folder
(Starlette's form parser would spool there). Limits: per file, per job, number of files, free disk space. Any
problem removes the whole staging folder and fails the job; errors give a part number, never a name.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, cast

from python_multipart.multipart import MultipartParser, parse_options_header

from app.auth.errors import Actor, AppError
from app.config import Settings
from app.services.context import ServiceContext
from app.services.jobs import create
from app.services.jobs.paths import PathRejected, sanitise_relative

MIN_FREE = 2 * 1024**3
MAX_PATH_FIELD = 4096


class UploadError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class Limits:
    max_file: int
    max_total: int
    max_files: int

    @classmethod
    def of(cls, settings: Settings | None) -> Limits:
        s = settings or Settings(Path("."), Path("."), Path("."))
        return cls(s.upload_max_file_mb * 1024**2, s.upload_max_total_gb * 1024**3, s.upload_max_files)


@dataclass
class _Sink:
    root: Path  # staging/<job_id>
    limits: Limits
    part: int = 0
    files: int = 0
    total: int = 0
    seen: set[str] = field(default_factory=set)
    _headers: dict[bytes, bytes] = field(default_factory=dict)
    _field: bytes = b""
    _value: bytes = b""
    _name: str = ""
    _path_text: bytearray = field(default_factory=bytearray)
    _pending: tuple[str, ...] | None = None
    _fh: IO[bytes] | None = None
    _size: int = 0

    def _bad(self, reason: str) -> UploadError:
        return UploadError(400, "bad_path", f"Part {self.part} was refused ({reason}).")

    def callbacks(self) -> dict[str, Any]:
        return {
            "on_part_begin": self.begin,
            "on_header_field": lambda d, a, b: setattr(self, "_field", self._field + d[a:b]),
            "on_header_value": lambda d, a, b: setattr(self, "_value", self._value + d[a:b]),
            "on_header_end": self.header_end,
            "on_headers_finished": self.headers_done,
            "on_part_data": self.data,
            "on_part_end": self.end,
        }

    def begin(self) -> None:
        self.part += 1
        self._headers, self._field, self._value, self._size = {}, b"", b"", 0

    def header_end(self) -> None:
        self._headers[self._field.lower()] = self._value
        self._field, self._value = b"", b""

    def headers_done(self) -> None:
        _, opts = parse_options_header(self._headers.get(b"content-disposition", b""))
        self._name = opts.get(b"name", b"").decode("utf-8", "replace")
        if self._name == "path":
            self._path_text = bytearray()
        elif self._name == "file":
            if self._pending is None:
                raise self._bad("no path part before the file")
            if self.files >= self.limits.max_files:
                raise UploadError(413, "too_large", "Too many files in one upload.")
            tmp = self.root / "tmp"
            tmp.mkdir(parents=True, exist_ok=True)
            self._fh = (tmp / str(self.part)).open("wb")
        else:
            raise self._bad("unknown part")

    def data(self, d: bytes, a: int, b: int) -> None:
        chunk = d[a:b]
        if self._name == "path":
            self._path_text += chunk
            if len(self._path_text) > MAX_PATH_FIELD:
                raise self._bad("too_long")
            return
        assert self._fh is not None
        self._size += len(chunk)
        self.total += len(chunk)
        if self._size > self.limits.max_file or self.total > self.limits.max_total:
            raise UploadError(413, "too_large", "The upload is larger than the limit.")
        self._fh.write(chunk)

    def end(self) -> None:
        if self._name == "path":
            self._accept_path(self._path_text.decode("utf-8", "strict"))
            return
        assert self._fh is not None and self._pending is not None
        self._fh.close()
        src, self._fh = Path(self._fh.name), None
        target = self.root.joinpath("files", *self._pending)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise self._bad("duplicate")
            os.replace(src, target)
        except (FileExistsError, NotADirectoryError, IsADirectoryError):
            raise self._bad("path_clash") from None
        self.files += 1
        self._pending = None

    def _accept_path(self, raw: str) -> None:
        try:
            parts = sanitise_relative(raw)
        except PathRejected as exc:
            raise self._bad(exc.reason) from None
        folded = "/".join(parts).casefold()
        if folded in self.seen:
            raise self._bad("duplicate")
        self.seen.add(folded)
        self._pending = parts

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def _boundary(content_type: str) -> bytes:
    ctype, opts = parse_options_header(content_type)
    boundary = opts.get(b"boundary")
    if ctype != b"multipart/form-data" or not boundary:
        raise UploadError(415, "content_type", "Send multipart/form-data.")
    return boundary


def _check_space(ctx: ServiceContext, limits: Limits, declared: int | None) -> None:
    if declared is not None and declared > limits.max_total + 64 * 1024**2:
        raise UploadError(413, "too_large", "The upload is larger than the limit.")
    ctx.paths.staging_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(ctx.paths.staging_dir).free
    if free - (declared or 0) < MIN_FREE:
        raise UploadError(507, "insufficient_storage", "Not enough free disk space for this upload.")


async def receive(
    ctx: ServiceContext,
    actor: Actor,
    content_type: str,
    declared: int | None,
    chunks: AsyncIterator[bytes],
) -> dict[str, object]:
    """Create an upload job, stream the body into staging, then queue the job."""
    job_id = create.begin_upload(ctx, actor)
    limits = Limits.of(ctx.settings)
    sink = _Sink(ctx.paths.staging_dir / job_id, limits)
    try:
        parser = MultipartParser(_boundary(content_type), cast(Any, sink.callbacks()))
        _check_space(ctx, limits, declared)
        sink.root.mkdir(parents=True, exist_ok=True)
        async for chunk in chunks:
            parser.write(chunk)
        parser.finalize()
        if sink._pending is not None or sink.files == 0:
            raise UploadError(400, "bad_path", "The upload has no files, or a path without a file.")
    except UploadError as exc:
        sink.close()
        create.upload_failed(ctx, job_id, exc.code)
        raise AppError(exc.status, exc.code, exc.message) from None
    except Exception:  # noqa: BLE001 - malformed body or a dropped connection; never echo the content
        sink.close()
        create.upload_failed(ctx, job_id, "upload_aborted")
        raise AppError(400, "upload_aborted", "The upload could not be read.") from None
    shutil.rmtree(sink.root / "tmp", ignore_errors=True)
    return create.upload_done(ctx, actor, job_id, sink.files, sink.total)
