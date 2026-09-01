"""Run-bound single-page HTML deliverable tools.

The model supplies document content, never a filesystem path.  Both tools
derive exactly one target from the harness-owned ``run_id`` and configured
deliverable directory.  Writes are UTF-8 text-only, bounded to 256 KiB, and
atomic within the destination directory.  Every validation or I/O failure is
returned in-band and leaves the prior document untouched.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Callable

from langchain_core.tools import StructuredTool

MAX_DOCUMENT_BYTES = 256 * 1024
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _target_path(run_id: str, deliverable_dir: str | os.PathLike[str]) -> Path:
    """Derive the sole allowed target, rejecting unsafe run identifiers."""

    clean_run_id = str(run_id).strip()
    if not _RUN_ID_PATTERN.fullmatch(clean_run_id):
        raise ValueError(
            "run_id must be 1-128 ASCII letters, digits, '_' or '-', "
            "starting with a letter or digit"
        )
    root = Path(deliverable_dir)
    target = root / f"{clean_run_id}.html"
    # The filename whitelist already excludes separators. Keep an explicit
    # containment assertion so later filename changes cannot weaken the gate.
    resolved_root = root.resolve(strict=False)
    derived_target = resolved_root / target.name
    if derived_target.parent != resolved_root:
        raise ValueError("derived deliverable path escapes configured directory")
    return target


def _html_bytes(html: str) -> bytes:
    if not isinstance(html, str):
        raise ValueError("html must be text")
    try:
        payload = html.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("html must be valid UTF-8 text") from exc
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise ValueError(
            f"html exceeds {MAX_DOCUMENT_BYTES} byte limit "
            f"({len(payload)} bytes)"
        )
    return payload


def _write_temp(root: Path, run_id: str, payload: bytes) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{run_id}.", suffix=".tmp", dir=root
    )
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _discard_temp(path: Path) -> None:
    """Best-effort cleanup that never changes the truthful write outcome."""

    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _create_document(target: Path, run_id: str, payload: bytes) -> None:
    root = target.parent
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise OSError("deliverable destination is not a directory")
    temporary = _write_temp(root, run_id, payload)
    try:
        # A hard link gives create-if-absent semantics without following an
        # existing symlink and without exposing partially written content.
        os.link(temporary, target)
    finally:
        _discard_temp(temporary)


def _replace_document(target: Path, run_id: str, payload: bytes) -> None:
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        raise FileNotFoundError("deliverable does not exist; use write_document first")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise OSError("deliverable target must be a regular file, not a symlink")
    temporary = _write_temp(target.parent, run_id, payload)
    try:
        # os.replace swaps the directory entry itself, so even a concurrent
        # symlink replacement is never followed outside the configured root.
        os.replace(temporary, target)
    finally:
        _discard_temp(temporary)


def make_deliverable_tools(
    run_id: str,
    deliverable_dir: str | os.PathLike[str] = "outputs/deliverables",
    *,
    on_success: Callable[[str], None] | None = None,
) -> list[StructuredTool]:
    """Build ``write_document``/``update_document`` bound to one run."""

    def _notify(path: Path) -> None:
        if on_success is not None:
            on_success(str(path))

    def write_document(
        title: str,
        html: str,
        intent: str = "",
        note: str | None = None,
    ) -> str:
        """Create this run's self-contained single-page HTML deliverable.

        Pass the complete HTML document in ``html`` and a human-readable
        ``title``. The path is derived by the harness; never provide a path.
        Use embedded CSS/assets and keep the UTF-8 content within 256 KiB.
        If this run already has a document, call ``update_document`` instead.
        Always pass ``intent``; ``note`` is an optional discovery.
        """

        try:
            target = _target_path(run_id, deliverable_dir)
            payload = _html_bytes(html)
            _create_document(target, str(run_id).strip(), payload)
        except FileExistsError:
            return "error: deliverable already exists; use update_document"
        except (OSError, ValueError) as exc:
            return f"error: document was not written ({exc})"
        try:
            _notify(target)
        except Exception:
            # The document is already durable. Optional bookkeeping must not
            # turn a truthful success receipt into a false write failure.
            pass
        clean_title = str(title).strip() or "untitled"
        return f"OK. 已创建文档「{clean_title}」：{target}（{len(payload)} bytes）"

    def update_document(
        html: str,
        intent: str = "",
        note: str | None = None,
    ) -> str:
        """Replace this run's existing single-page HTML deliverable.

        Pass the complete replacement HTML in ``html``. The harness reuses the
        current run's derived path; the document must already exist. Keep the
        UTF-8 content within 256 KiB. Always pass ``intent``; ``note`` is an
        optional discovery.
        """

        try:
            target = _target_path(run_id, deliverable_dir)
            payload = _html_bytes(html)
            _replace_document(target, str(run_id).strip(), payload)
        except (OSError, ValueError) as exc:
            return f"error: document was not updated ({exc})"
        try:
            _notify(target)
        except Exception:
            pass
        return f"OK. 已更新文档：{target}（{len(payload)} bytes）"

    return [
        StructuredTool.from_function(write_document, parse_docstring=True),
        StructuredTool.from_function(update_document, parse_docstring=True),
    ]


__all__ = ["MAX_DOCUMENT_BYTES", "make_deliverable_tools"]
