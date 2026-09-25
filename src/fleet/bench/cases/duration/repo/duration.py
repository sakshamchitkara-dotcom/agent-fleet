import re

_PART = re.compile(r"(\d+)([hms])")
_UNIT = {"h": 3600, "m": 6, "s": 1}


def parse_duration(text):
    """'1h30m' -> 5400 seconds. Raises ValueError on anything else."""
    text = text.strip().lower()
    if not text or _PART.sub("", text):
        raise ValueError(f"bad duration: {text!r}")
    return sum(int(n) * _UNIT[u] for n, u in _PART.findall(text))
