"""Edit model: which parts of the source video are kept, and at what speed.

The timeline is expressed in *source* time. It holds an ordered list of
non-overlapping :class:`Segment` objects. Anything not covered by a segment is
cut out of the final video. Every segment has its own playback speed.

This module has no Qt or FFmpeg dependency so it can be unit-tested directly.
"""

from __future__ import annotations

import copy
from bisect import bisect_right
from dataclasses import dataclass

MIN_SPEED = 0.1
MAX_SPEED = 10.0


@dataclass
class Segment:
    """A kept piece of the source video, ``[start, end)`` in source seconds."""

    start: float
    end: float
    speed: float = 1.0

    @property
    def source_duration(self) -> float:
        return self.end - self.start

    @property
    def output_duration(self) -> float:
        return self.source_duration / self.speed

    def contains(self, t: float) -> bool:
        return self.start <= t < self.end


class Timeline:
    """Kept segments of one source video, with undo/redo."""

    def __init__(self, duration: float = 0.0, fps: float = 30.0) -> None:
        self.reset(duration, fps)

    # ------------------------------------------------------------------ setup
    def reset(self, duration: float, fps: float) -> None:
        """Start over with the whole source kept as a single segment."""
        self.duration = max(0.0, float(duration))
        self.fps = float(fps) if fps and fps > 0 else 30.0
        self.segments: list[Segment] = (
            [Segment(0.0, self.duration)] if self.duration > 0 else []
        )
        self._undo: list[list[Segment]] = []
        self._redo: list[list[Segment]] = []
        self._last_op: object = None

    # -------------------------------------------------------------- utilities
    @property
    def frame_duration(self) -> float:
        return 1.0 / self.fps

    def snap(self, t: float) -> float:
        """Clamp ``t`` to the source and round it to the nearest frame start."""
        t = min(max(t, 0.0), self.duration)
        snapped = round(t * self.fps) / self.fps
        return min(snapped, self.duration)

    def index_at(self, t: float) -> int | None:
        """Index of the segment containing source time ``t``, or None if cut out."""
        starts = [s.start for s in self.segments]
        i = bisect_right(starts, t) - 1
        if i >= 0 and self.segments[i].contains(t):
            return i
        return None

    def next_index_after(self, t: float) -> int | None:
        """Index of the first segment that starts after ``t``."""
        for i, seg in enumerate(self.segments):
            if seg.start > t:
                return i
        return None

    def cut_points(self) -> list[float]:
        """Sorted unique segment boundaries, including source start and end."""
        points = {0.0, self.duration}
        for seg in self.segments:
            points.add(seg.start)
            points.add(seg.end)
        return sorted(points)

    @property
    def output_duration(self) -> float:
        return sum(seg.output_duration for seg in self.segments)

    def source_to_output(self, t: float) -> float:
        """Map a source time to the time it will appear at in the exported video."""
        out = 0.0
        for seg in self.segments:
            if t >= seg.end:
                out += seg.output_duration
            elif t >= seg.start:
                out += (t - seg.start) / seg.speed
                break
            else:
                break
        return out

    # ---------------------------------------------------------------- editing
    def split(self, t: float) -> bool:
        """Split the segment under ``t`` into two. Returns True if it split."""
        t = self.snap(t)
        i = self.index_at(t)
        if i is None:
            return False
        seg = self.segments[i]
        min_len = self.frame_duration * 0.5
        if t - seg.start < min_len or seg.end - t < min_len:
            return False
        self._checkpoint()
        self.segments[i : i + 1] = [
            Segment(seg.start, t, seg.speed),
            Segment(t, seg.end, seg.speed),
        ]
        return True

    def delete(self, index: int) -> bool:
        """Remove a segment (that part of the source is cut out)."""
        if not 0 <= index < len(self.segments):
            return False
        self._checkpoint()
        del self.segments[index]
        return True

    def delete_range(self, a: float, b: float) -> bool:
        """Cut out source time ``[a, b)`` wherever it is still kept."""
        a, b = sorted((self.snap(a), self.snap(b)))
        if b - a < self.frame_duration * 0.5:
            return False
        new = self._without_range(self.segments, a, b)
        if new == self.segments:
            return False
        self._checkpoint()
        self.segments = new
        return True

    def keep_range(self, a: float, b: float) -> bool:
        """Cut out everything outside source time ``[a, b)``."""
        a, b = sorted((self.snap(a), self.snap(b)))
        if b - a < self.frame_duration * 0.5:
            return False
        new = self._without_range(self.segments, 0.0, a)
        new = self._without_range(new, b, self.duration)
        if new == self.segments:
            return False
        self._checkpoint()
        self.segments = new
        return True

    def set_speed(self, index: int, speed: float) -> bool:
        """Change one segment's speed. Repeated changes merge into one undo step."""
        if not 0 <= index < len(self.segments):
            return False
        speed = min(max(float(speed), MIN_SPEED), MAX_SPEED)
        if abs(self.segments[index].speed - speed) < 1e-9:
            return False
        self._checkpoint(op=("speed", index))
        self.segments[index].speed = speed
        return True

    def _without_range(self, segments: list[Segment], a: float, b: float) -> list[Segment]:
        min_len = self.frame_duration * 0.5
        result: list[Segment] = []
        for seg in segments:
            if seg.end <= a or seg.start >= b:
                result.append(copy.copy(seg))
                continue
            if a - seg.start >= min_len:
                result.append(Segment(seg.start, a, seg.speed))
            if seg.end - b >= min_len:
                result.append(Segment(b, seg.end, seg.speed))
        return result

    # ------------------------------------------------------------- undo/redo
    def _checkpoint(self, op: object = None) -> None:
        """Save the current state before a change.

        Consecutive changes with the same non-None ``op`` key (e.g. dragging a
        speed spin box) are merged into a single undo step.
        """
        if op is not None and op == self._last_op:
            return
        self._undo.append(copy.deepcopy(self.segments))
        self._redo.clear()
        self._last_op = op

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(copy.deepcopy(self.segments))
        self.segments = self._undo.pop()
        self._last_op = None
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(copy.deepcopy(self.segments))
        self.segments = self._redo.pop()
        self._last_op = None
        return True
