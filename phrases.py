"""Phrase list for meeting segments, read from OpenWhispr's own database.

OpenWhispr sends dictation the custom dictionary as a prompt, but a meeting's
realtime session gets no prompt at all. So the shim reads the dictionary itself,
plus the participant names of the note being recorded (copied from the calendar
invite, or added by hand), and sends both to Azure as a phrase list.

The database doesn't say which note is recording. The recording note saves its
transcript every 30 s, so it's taken to be the most recently updated note that
has participants, updated in the last RECENT_NOTE_MINUTES. Editing another note
with participants mid-meeting would pick that one. That costs some phrase bias,
nothing more.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from foundry import log

DATA_DIR = os.path.expanduser(
    os.environ.get("OPENWHISPR_DATA_DIR", "~/Library/Application Support/open-whispr")
)
ENABLED = os.environ.get("FOUNDRY_MEETING_PHRASES", "1") != "0"
REFRESH_S = 60             # participants added mid-meeting show up within a minute
RECENT_NOTE_MINUTES = 3
MAX_PHRASES = 500


def participant_names(raw: str | None) -> list[str]:
    """Display names from a note's participants JSON, without yourself. Each
    name also adds its first word: people say "Liisa", not "Liisa Korhonen"."""
    try:
        attendees = json.loads(raw or "[]")
    except ValueError:
        return []
    names: list[str] = []
    for attendee in attendees if isinstance(attendees, list) else []:
        if not isinstance(attendee, dict) or attendee.get("self"):
            continue
        name = (attendee.get("displayName") or "").strip()
        if not name or "@" in name:
            continue
        names.append(name)
        first = name.split()[0]
        if first != name and len(first) > 2:
            names.append(first)
    return names


def read_phrases(db_path: str) -> list[str]:
    """Participant names first, then the dictionary, deduplicated and capped."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    try:
        row = conn.execute(
            "SELECT participants FROM notes WHERE deleted_at IS NULL"
            " AND participants IS NOT NULL AND participants NOT IN ('', '[]')"
            " AND updated_at >= datetime('now', ?) ORDER BY updated_at DESC LIMIT 1",
            (f"-{RECENT_NOTE_MINUTES} minutes",),
        ).fetchone()
        words = [w for (w,) in conn.execute(
            "SELECT word FROM custom_dictionary WHERE deleted_at IS NULL ORDER BY id")]
    finally:
        conn.close()
    seen: set[str] = set()
    out: list[str] = []
    for phrase in participant_names(row[0] if row else None) + words:
        phrase = phrase.strip()
        # Commas would split the phrase: stt.parse_phrases reads "a, b, c".
        if phrase and "," not in phrase and phrase.lower() not in seen:
            seen.add(phrase.lower())
            out.append(phrase)
    return out[:MAX_PHRASES]


class PhraseCache:
    """Shared by the mic and system sessions, re-read every REFRESH_S."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._phrases: list[str] = []
        self._read_at = float("-inf")
        self._warned = False

    def get(self) -> list[str]:
        with self._lock:
            if time.monotonic() - self._read_at < REFRESH_S:
                return self._phrases
            self._read_at = time.monotonic()
            try:
                phrases = read_phrases(self.db_path)
            except sqlite3.Error as exc:
                if not self._warned:
                    log(f"phrases: can't read {self.db_path}: {exc}")
                    self._warned = True
                return self._phrases  # keep the last good list
            if phrases != self._phrases:
                log(f"phrases: {len(phrases)} for meetings")
            self._phrases = phrases
            return phrases


CACHE = PhraseCache(os.path.join(DATA_DIR, "transcriptions.db"))


def meeting_prompt() -> str | None:
    """The phrase list as the comma-separated prompt stt.recognize() takes."""
    if not ENABLED:
        return None
    return ", ".join(CACHE.get()) or None
