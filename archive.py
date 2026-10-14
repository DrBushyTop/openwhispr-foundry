"""Keeps each realtime session's audio as MP3, and the shim's transcript of it
as text, so a segment Azure failed on, or a note OpenWhispr lost, can be
recovered. OpenWhispr itself keeps no meeting audio.

    meetings/2026-10-14/093012-mic-sess_ab12.mp3   the whole stream, silences included
    meetings/2026-10-14/093012-mic-sess_ab12.txt   [00:12:34] text, one line per segment

Timestamps in the .txt are offsets into the .mp3, so `ffplay -ss 00:12:34`
plays the moment a line came from. The MP3 is 16 kHz mono 48 kbps, about
22 MB an hour per stream, which MAI-Transcribe takes as is. ffmpeg writes MP3
frame by frame, so a file cut short by a crash still plays.
"""

from __future__ import annotations

import os
import subprocess
import time

from foundry import log

AUDIO_DIR = os.path.expanduser(
    os.environ.get("FOUNDRY_MEETING_AUDIO_DIR", "~/Library/Application Support/openwhispr-foundry/meetings")
)
KEEP_DAYS = float(os.environ.get("FOUNDRY_MEETING_AUDIO_DAYS", "14"))  # 0: save nothing


def enabled() -> bool:
    return KEEP_DAYS > 0


def clock(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def prune(root: str | None = None, now: float | None = None) -> int:
    """Delete files older than KEEP_DAYS and the day folders they leave empty."""
    root = root or AUDIO_DIR
    cutoff = (now or time.time()) - KEEP_DAYS * 86400
    removed = 0
    if not os.path.isdir(root):
        return 0
    for day in os.listdir(root):
        day_dir = os.path.join(root, day)
        if not os.path.isdir(day_dir):
            continue
        for name in os.listdir(day_dir):
            path = os.path.join(day_dir, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
        try:
            os.rmdir(day_dir)  # only succeeds when empty
        except OSError:
            pass
    return removed


class SessionArchive:
    """Audio is written from the socket reader thread and transcript lines from
    the worker thread; they touch different files. Any failure here is logged
    and turns the archive off. It must never end a meeting."""

    def __init__(self, stream: str, session_id: str, sample_rate: int, root: str | None = None) -> None:
        started = time.localtime()
        day_dir = os.path.join(root or AUDIO_DIR, time.strftime("%Y-%m-%d", started))
        base = os.path.join(day_dir, f"{time.strftime('%H%M%S', started)}-{stream}-{session_id[-8:]}")
        self.audio_path = base + ".mp3"
        self.text_path = base + ".txt"
        self.proc: subprocess.Popen | None = None
        try:
            os.makedirs(day_dir, exist_ok=True)
            with open(self.text_path, "w", encoding="utf-8") as f:
                f.write(f"# {stream} stream, started {time.strftime('%Y-%m-%d %H:%M:%S', started)}\n")
            self.proc = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(sample_rate), "-ac", "1",
                 "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-b:a", "48k", "-f", "mp3", self.audio_path],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log(f"archive: can't save {self.audio_path}: {exc}")

    def write(self, pcm: bytes) -> None:
        if not self.proc:
            return
        try:
            self.proc.stdin.write(pcm)
        except (BrokenPipeError, OSError) as exc:
            log(f"archive: ffmpeg stopped, no more audio saved to {self.audio_path}: {exc}")
            self.proc = None

    def line(self, start_ms: int, text: str) -> None:
        try:
            with open(self.text_path, "a", encoding="utf-8") as f:
                f.write(f"[{clock(start_ms)}] {text}\n")
        except OSError:
            pass

    def close(self) -> None:
        if not self.proc:
            return
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
        self.proc = None
