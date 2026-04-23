#!/usr/bin/env python3
"""
Unified NotebookLM CSV uploads for this repo.

Prefers the **nlm** CLI from **notebooklm-mcp-cli** (same Google auth as the **NLM**
MCP server in Cursor). Falls back to **notebooklm-py** when ``nlm`` is missing or
fails and ``NOTEBOOKLM_UPLOAD_BACKEND`` is ``auto`` (default).

Environment:
  NOTEBOOKLM_UPLOAD_BACKEND   ``auto`` | ``nlm`` | ``py``
                              auto: use nlm if on PATH, else notebooklm-py; if nlm
                              fails, retry with py.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Union

PathsInput = Union[Path, str, Sequence[Union[Path, str]]]


def _backend() -> str:
    v = os.getenv("NOTEBOOKLM_UPLOAD_BACKEND", "auto").strip().lower()
    return v if v in ("auto", "nlm", "py") else "auto"


def _notebooklm_py_available() -> bool:
    try:
        import notebooklm  # noqa: F401
        return True
    except ImportError:
        return False


def notebooklm_upload_available() -> bool:
    """True if either nlm CLI or notebooklm-py can be used."""
    return shutil.which("nlm") is not None or _notebooklm_py_available()


def _list_notebooks_nlm() -> List[dict]:
    r = subprocess.run(
        ["nlm", "notebook", "list", "--json"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"exit {r.returncode}")
    data = json.loads(r.stdout)
    return data if isinstance(data, list) else []


def _find_notebook_id(notebooks: List[dict], title: str) -> str | None:
    for nb in notebooks:
        if nb.get("title") == title:
            nid = nb.get("id")
            return str(nid) if nid else None
    return None


def find_notebook_id_by_title(notebook_title: str) -> Optional[str]:
    """Resolve a notebook's UUID by exact title. Returns None if not found or ``nlm`` missing."""
    if not shutil.which("nlm"):
        return None
    try:
        notebooks = _list_notebooks_nlm()
        return _find_notebook_id(notebooks, notebook_title)
    except Exception:
        return None


def _ensure_notebook_id_nlm(title: str) -> str:
    notebooks = _list_notebooks_nlm()
    nid = _find_notebook_id(notebooks, title)
    if nid:
        return nid
    r = subprocess.run(
        ["nlm", "notebook", "create", title],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}")
    notebooks = _list_notebooks_nlm()
    nid = _find_notebook_id(notebooks, title)
    if not nid:
        raise RuntimeError(f"notebook {title!r} not found after create")
    return nid


def _upload_via_nlm(paths: List[Path], notebook_name: str) -> bool:
    try:
        nid = _ensure_notebook_id_nlm(notebook_name)
    except Exception as e:
        print(f"NotebookLM (nlm): {e}")
        return False

    print(f"Using NotebookLM via nlm → notebook {notebook_name!r} ({nid})")
    for p in paths:
        if not p.is_file():
            print(f"NotebookLM (nlm): file not found: {p}")
            return False
        r = subprocess.run(
            [
                "nlm",
                "source",
                "add",
                nid,
                "--file",
                str(p.resolve()),
                "--wait",
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            print(f"NotebookLM (nlm): failed on {p.name}: {err[:500]}")
            return False
        print(f"Uploaded {p.name} via nlm")
    return True


async def _upload_via_notebooklm_py(paths: List[Path], notebook_name: str) -> bool:
    from notebooklm import NotebookLMClient

    async with await NotebookLMClient.from_storage() as client:
        notebooks = await client.notebooks.list()
        nb = None
        for n in notebooks:
            if n.title == notebook_name:
                nb = n
                break
        if nb is None:
            nb = await client.notebooks.create(notebook_name)
            print(f"Created NotebookLM notebook: {notebook_name}")
        else:
            print(f"Using existing NotebookLM notebook: {notebook_name}")

        for p in paths:
            await client.sources.add_file(nb.id, str(p.resolve()), wait=True)
            print(f"Uploaded {p.name} via notebooklm-py")
    return True


def _normalize_paths(csv_paths: PathsInput) -> List[Path]:
    if isinstance(csv_paths, (str, Path)):
        return [Path(csv_paths)]
    return [Path(x) for x in csv_paths]


def upload_csvs_to_notebook(csv_paths: PathsInput, notebook_name: str) -> bool:
    """
    Upload one or more CSV files to a notebook by title.

    Respects NOTEBOOKLM_UPLOAD_BACKEND (auto / nlm / py).
    """
    paths = _normalize_paths(csv_paths)
    backend = _backend()

    try_nlm = backend == "nlm" or (backend == "auto" and shutil.which("nlm"))
    if try_nlm:
        ok = _upload_via_nlm(paths, notebook_name)
        if ok or backend == "nlm":
            return ok
        print("NotebookLM: nlm upload failed; trying notebooklm-py…")

    if not _notebooklm_py_available():
        print(
            "notebooklm-py not installed. Run: pip install 'notebooklm-py[browser]' "
            "and `notebooklm login`, or install notebooklm-mcp-cli and `nlm login`."
        )
        return False

    try:
        return asyncio.run(_upload_via_notebooklm_py(paths, notebook_name))
    except Exception as e:
        print(f"NotebookLM (notebooklm-py): {e}")
        print("   Try: notebooklm login")
        return False
