import os
import stat
import subprocess
import sys
from pathlib import Path

import nbformat


def get_changed_notebooks(base_ref: str = "origin/main") -> list[Path]:
    """
    Returns a list of changed notebook paths in the current git branch
    compared to the specified base reference.
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", base_ref, "--", "*.ipynb"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]


def is_valid_notebook(path: Path) -> bool:
    """
    Checks if the notebook at the given path is valid by attempting to read it
    with nbformat.
    """
    try:
        limit = 32 * 1024**2
        info = path.lstat()
        if path.resolve() != path.absolute() or not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Expected a bounded regular notebook file")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as f:
            if not os.path.samestat(info, os.fstat(f.fileno())):
                raise ValueError("Notebook file changed")
            raw = f.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("Notebook exceeds size limit")
        nbformat.reads(raw.decode("utf-8"), as_version=4)
        return True
    except Exception as e:
        print(f"{path}: INVALID - {e}")
        return False


def main() -> None:
    """
    Main function to validate the format of changed notebooks.
    """
    changed_notebooks = get_changed_notebooks()
    if not changed_notebooks:
        print("No changed .ipynb files to validate.")
        sys.exit(0)

    print(f"Validating {len(changed_notebooks)} notebook(s)...")
    errors = 0
    for path in changed_notebooks:
        if not path.exists():
            continue  # skip deleted files
        if not is_valid_notebook(path):
            errors += 1

    if errors:
        print(f"{errors} invalid notebook(s) found.")
        sys.exit(1)
    else:
        print("All changed notebooks are valid.")


if __name__ == "__main__":
    main()
