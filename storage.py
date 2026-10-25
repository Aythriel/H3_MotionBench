import json
import tempfile
from pathlib import Path


def atomic_json(path: Path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def local_folder(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Enter an absolute local folder path.")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute() or str(path).startswith(("//", chr(92) * 2)):
        raise ValueError("Choose an absolute local folder, not a relative or network path.")
    return path.resolve()


def writable_folder(path):
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=path):
        pass



def clip_name(value):
    if not isinstance(value, str):
        raise ValueError("Enter a clip name.")
    name = value.strip()
    if not name or len(name) > 100 or any(ord(c) < 32 for c in name):
        raise ValueError("Use a name of 1 to 100 characters without line breaks.")
    return name
