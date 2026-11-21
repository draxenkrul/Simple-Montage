"""Formatting helpers for time values (all times in the app are float seconds)."""


def format_time(seconds: float, *, millis: bool = True) -> str:
    """Format seconds as ``H:MM:SS.mmm`` (or ``H:MM:SS`` when ``millis`` is False)."""
    if seconds < 0:
        return "-" + format_time(-seconds, millis=millis)
    total_ms = int(round(seconds * 1000))
    if not millis:
        total_ms = (total_ms // 1000) * 1000
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    text = f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{text}.{ms:03d}" if millis else text


def format_speed(speed: float) -> str:
    """Format a speed factor such as ``2×`` or ``0.25×``."""
    return f"{speed:g}×"
