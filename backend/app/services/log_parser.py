import re
from typing import Optional

LOG_PATTERN = re.compile(r"(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})?\s*(?P<level>INFO|WARN|WARNING|ERROR|DEBUG)?\s*(?P<component>\S+)?\s*-?\s*(?P<message>.*)")


def parse_log_line(line: str) -> dict:
    m = LOG_PATTERN.match(line.strip())
    if not m:
        return {"raw": line}
    gd = m.groupdict()
    return {
        "timestamp": gd.get("timestamp"),
        "level": (gd.get("level") or "INFO").upper() if gd.get("level") else None,
        "component": gd.get("component"),
        "message": gd.get("message"),
        "raw": line,
    }
