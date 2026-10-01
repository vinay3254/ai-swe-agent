import subprocess
from pathlib import Path


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def make_remote(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """A bare repo with one commit on `main`, usable as a clone URL."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-b", "main", cwd=seed)
    for name, text in (files or {"app.py": "def parse(s):\n    return s[0]\n"}).items():
        (seed / name).write_text(text)
    git("add", "-A", cwd=seed)
    git("commit", "-m", "init", cwd=seed)
    bare = tmp_path / "remote.git"
    git("clone", "--bare", str(seed), str(bare), cwd=tmp_path)
    return bare
