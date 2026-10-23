import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from apply_patch import (
    DiffError,
    WorkspaceFilesystem,
    open_file,
    process_patch,
    remove_file,
    write_file,
)


def run_patch(workspace: Path, patch: str) -> None:
    fs = WorkspaceFilesystem(workspace)
    process_patch(patch, fs.open, fs.write, fs.remove, fs.validate)


def test_valid_add_update_move_and_delete(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("old", encoding="utf-8")
    run_patch(
        tmp_path,
        """*** Begin Patch
*** Add File: added.txt
+added
*** Update File: source.txt
@@
-old
+new
*** End Patch""",
    )
    assert (tmp_path / "added.txt").read_text(encoding="utf-8") == "added"
    assert (tmp_path / "source.txt").read_text(encoding="utf-8") == "new"

    run_patch(
        tmp_path,
        """*** Begin Patch
*** Update File: source.txt
*** Move to: moved.txt
@@
-new
+moved
*** End Patch""",
    )
    assert not (tmp_path / "source.txt").exists()
    assert (tmp_path / "moved.txt").read_text(encoding="utf-8") == "moved"

    run_patch(
        tmp_path,
        """*** Begin Patch
*** Delete File: added.txt
*** End Patch""",
    )
    assert not (tmp_path / "added.txt").exists()


@pytest.mark.parametrize(
    "bad_path",
    ["../victim.txt", "/tmp/victim.txt", "C:\\victim.txt", "\\\\server\\share\\victim.txt"],
)
def test_rejects_absolute_and_traversal_paths_before_mutation(tmp_path: Path, bad_path: str) -> None:
    victim = tmp_path.parent / "victim.txt"
    victim.write_text("unchanged", encoding="utf-8")
    patch = f"""*** Begin Patch
*** Add File: created.txt
+created
*** Add File: {bad_path}
+must not be written
*** End Patch"""

    with pytest.raises(DiffError):
        run_patch(tmp_path, patch)

    assert not (tmp_path / "created.txt").exists()
    assert victim.read_text(encoding="utf-8") == "unchanged"


def test_rejects_symlink_escape_before_mutation(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside"
    outside.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        if sys.platform == "win32":
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                pytest.skip(f"symlink or junction creation unavailable: {exc}")
        else:
            pytest.skip(f"symlink creation unavailable: {exc}")

    patch = """*** Begin Patch
*** Add File: created.txt
+created
*** Add File: linked/escaped.txt
+must not be written
*** End Patch"""
    with pytest.raises(DiffError):
        run_patch(tmp_path, patch)

    assert not (tmp_path / "created.txt").exists()
    assert not (outside / "escaped.txt").exists()


def test_rejects_invalid_move_before_mutation(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("unchanged", encoding="utf-8")
    victim = tmp_path.parent / "move-victim.txt"
    victim.write_text("unchanged", encoding="utf-8")
    patch = """*** Begin Patch
*** Add File: created.txt
+created
*** Update File: source.txt
*** Move to: ../move-victim.txt
@@
-unchanged
+must not move
*** End Patch"""

    with pytest.raises(DiffError):
        run_patch(tmp_path, patch)

    assert not (tmp_path / "created.txt").exists()
    assert (tmp_path / "source.txt").read_text(encoding="utf-8") == "unchanged"
    assert victim.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize("operation", ["Add", "Update", "Delete"])
def test_rejects_hard_link_aliases_before_mutation(tmp_path: Path, operation: str) -> None:
    victim = tmp_path.parent / "hard-link-victim.txt"
    victim.write_text("unchanged", encoding="utf-8")
    alias = tmp_path / "linked.txt"
    alias.hardlink_to(victim)
    content = "\n+changed" if operation == "Add" else ""
    if operation == "Update":
        content = "\n@@\n-unchanged\n+changed"
    patch = (
        "*** Begin Patch\n*** Add File: created.txt\n+must not be written\n"
        f"*** {operation} File: linked.txt{content}\n*** End Patch"
    )
    with pytest.raises(DiffError):
        run_patch(tmp_path, patch)
    assert not (tmp_path / "created.txt").exists()
    assert victim.read_text(encoding="utf-8") == "unchanged"
    assert alias.read_text(encoding="utf-8") == "unchanged"


def test_custom_callbacks_remain_supported() -> None:
    reads = {"file.txt": "old"}
    writes = []
    removals = []

    def read(path: str) -> str:
        return reads[path]

    def write(path: str, content: str) -> None:
        writes.append((path, content))

    def remove(path: str) -> None:
        removals.append(path)

    process_patch(
        """*** Begin Patch
*** Update File: file.txt
@@
-old
+new
*** End Patch""",
        read,
        write,
        remove,
    )
    assert writes == [("file.txt", "new")]
    assert removals == []


def test_public_filesystem_callbacks_remain_supported(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_file("nested/file.txt", "old")
    assert open_file("nested/file.txt") == "old"
    process_patch(
        """*** Begin Patch
*** Update File: nested/file.txt
@@
-old
+new
*** End Patch""",
        open_file,
        write_file,
        remove_file,
    )
    assert open_file("nested/file.txt") == "new"
    remove_file("nested/file.txt")
    assert not (tmp_path / "nested/file.txt").exists()


def test_public_callback_patch_prevalidates_all_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    victim = tmp_path.parent / "callback-victim.txt"
    victim.write_text("unchanged", encoding="utf-8")
    with pytest.raises(DiffError):
        process_patch(
            """*** Begin Patch
*** Add File: created.txt
+must not be written
*** Delete File: ../callback-victim.txt
*** End Patch""",
            open_file,
            write_file,
            remove_file,
        )
    assert not (tmp_path / "created.txt").exists()
    assert victim.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize("operation", ["Update", "Delete"])
def test_outside_sources_are_rejected(tmp_path: Path, operation: str) -> None:
    victim = tmp_path.parent / "source-victim.txt"
    victim.write_text("unchanged", encoding="utf-8")
    suffix = "\n@@\n-unchanged\n+changed" if operation == "Update" else ""
    patch = f"*** Begin Patch\n*** {operation} File: ../source-victim.txt{suffix}\n*** End Patch"
    with pytest.raises(DiffError):
        run_patch(tmp_path, patch)
    assert victim.read_text(encoding="utf-8") == "unchanged"


def test_workspace_stays_bound_after_current_directory_changes(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    fs = WorkspaceFilesystem(workspace)
    monkeypatch.chdir(elsewhere)
    fs.write("created.txt", "inside workspace")
    assert (workspace / "created.txt").read_text() == "inside workspace"
    assert not (elsewhere / "created.txt").exists()


def test_cli_freezes_current_workspace_root(tmp_path: Path) -> None:
    module = Path(__file__).parents[1] / "apply_patch.py"
    patch = """*** Begin Patch
*** Add File: cli-created.txt
+created by cli
*** End Patch"""
    result = subprocess.run(
        [sys.executable, str(module)],
        cwd=tmp_path,
        input=patch,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "Done!"
    assert (tmp_path / "cli-created.txt").read_text(encoding="utf-8") == "created by cli"
