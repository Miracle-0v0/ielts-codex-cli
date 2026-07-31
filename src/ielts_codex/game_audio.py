"""Best-effort, dependency-free chiptune background music for ``/game``.

The track is generated locally from a small original score; no music, samples,
or assets are downloaded.  Playback is deliberately optional because a
terminal may be remote, muted, or unable to launch a local audio player.
``ChiptuneBGM`` therefore never raises audio errors into the game loop.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from array import array
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final


BGM_ENV_VAR: Final = "IELTS_CODEX_GAME_BGM"
_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES: Final = frozenset({"0", "false", "no", "off"})
_SAMPLE_RATE: Final = 11_025
_STEP_SECONDS: Final = 0.18
_TRACK_FILENAME: Final = "pocket-lexicon-bgm-v1.wav"

# An original, deliberately short 8-bit loop.  It is not derived from any
# game soundtrack.  ``None`` marks a rest.
_MELODY: Final = (
    "E5", "G5", "A5", "B5", "A5", "G5", "E5", None,
    "D5", "E5", "G5", "A5", "G5", "E5", "D5", None,
    "E5", "G5", "B5", "A5", "G5", "E5", "D5", "E5",
    "C5", "D5", "E5", "G5", "E5", "D5", "C5", None,
    "A4", "C5", "E5", "G5", "E5", "C5", "A4", None,
    "B4", "D5", "G5", "A5", "G5", "D5", "B4", None,
)
_BASS: Final = (
    "A3", "A3", "E3", "E3", "D3", "D3", "A3", "A3",
    "G3", "G3", "D3", "D3", "E3", "E3", "B2", "B2",
)
_NOTE_SEMITONES: Final = {
    "C": -9,
    "C#": -8,
    "D": -7,
    "D#": -6,
    "E": -5,
    "F": -4,
    "F#": -3,
    "G": -2,
    "G#": -1,
    "A": 0,
    "A#": 1,
    "B": 2,
}


@dataclass(frozen=True, slots=True)
class BGMState:
    """Small serialisable view of the optional music state."""

    enabled: bool
    available: bool
    playing: bool
    backend: str | None

    @property
    def label(self) -> str:
        if not self.enabled:
            return "BGM OFF"
        if self.playing:
            return "BGM ON"
        if self.available:
            return "BGM READY"
        return "BGM SILENT"


class ChiptuneBGM:
    """Play a locally generated chiptune loop without blocking terminal I/O.

    macOS uses ``afplay``; Linux and other POSIX platforms use the first
    available one of ``ffplay``, ``mpv``, or ``aplay``.  Windows uses the
    standard-library ``winsound`` module.  If none is available, the game
    stays silent and continues normally.
    """

    def __init__(self, data_dir: Path, *, enabled: bool = True) -> None:
        self.data_dir = Path(data_dir)
        self.enabled = _environment_override(enabled)
        self._backend = _find_backend()
        self._process: subprocess.Popen[bytes] | None = None
        self._started_at: float | None = None
        self._winsound_active = False
        self._last_error: str | None = None

    @property
    def state(self) -> BGMState:
        return BGMState(
            enabled=self.enabled,
            available=self._backend is not None,
            playing=self._is_playing(),
            backend=self._backend,
        )

    @property
    def label(self) -> str:
        return self.state.label

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def start(self) -> BGMState:
        """Start looped background music, returning the safe resulting state."""

        if not self.enabled or self._is_playing():
            return self.state
        if self._backend is None:
            self._last_error = "No compatible local audio player was found."
            return self.state
        try:
            track = self._ensure_track()
            if self._backend == "winsound":
                import winsound  # pragma: no cover - Windows-only branch

                winsound.PlaySound(
                    str(track),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
                )
                self._winsound_active = True
            else:
                self._process = subprocess.Popen(
                    _playback_command(self._backend, track),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._started_at = time.monotonic()
            self._last_error = None
        except (OSError, ValueError, wave.Error) as exc:
            self._last_error = str(exc)
            self._process = None
            self._started_at = None
            self._winsound_active = False
        return self.state

    def keep_alive(self) -> BGMState:
        """Restart a completed backend while an interactive session is live.

        Most players can loop the track themselves.  ALSA's ``aplay`` does not
        expose a portable loop flag, so this inexpensive poll gives it the same
        continuous behaviour without a shell or a busy background thread.
        """

        if not self.enabled or self._backend is None or self._is_playing():
            return self.state
        if self._process is not None:
            elapsed = (
                time.monotonic() - self._started_at
                if self._started_at is not None
                else None
            )
            self._process = None
            self._started_at = None
            if elapsed is not None and elapsed < 0.5:
                # A player that immediately exits normally has no usable
                # sound device.  Disable retrying so a headless system never
                # spawns a failed process ten times per second.
                self._last_error = "Audio player exited before playback began."
                self._backend = None
                return self.state
        if self.enabled and self._backend is not None:
            return self.start()
        return self.state

    def stop(self) -> None:
        """Stop playback promptly; all errors are intentionally suppressed."""

        if self._winsound_active:
            with suppress(OSError):
                import winsound  # pragma: no cover - Windows-only branch

                winsound.PlaySound(None, 0)
            self._winsound_active = False
        process = self._process
        self._process = None
        self._started_at = None
        if process is None or process.poll() is not None:
            return
        with suppress(OSError, subprocess.TimeoutExpired):
            process.terminate()
            process.wait(timeout=0.6)
        if process.poll() is None:
            with suppress(OSError):
                process.kill()

    def set_enabled(self, enabled: bool) -> BGMState:
        self.enabled = bool(enabled)
        if not self.enabled:
            self.stop()
        return self.state

    def toggle(self) -> BGMState:
        return self.set_enabled(not self.enabled)

    def _is_playing(self) -> bool:
        if self._winsound_active:
            return True
        return self._process is not None and self._process.poll() is None

    def _ensure_track(self) -> Path:
        path = self.data_dir / _TRACK_FILENAME
        try:
            if path.is_file() and path.stat().st_size > 1_024:
                return path
        except OSError:
            pass
        self.data_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".pocket-lexicon-", suffix=".wav", dir=self.data_dir
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                _write_original_chiptune(handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        return path


def _environment_override(default: bool) -> bool:
    value = os.environ.get(BGM_ENV_VAR, "").strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def _find_backend() -> str | None:
    if os.name == "nt":
        try:
            import winsound  # noqa: F401  # pragma: no cover - Windows only
        except ImportError:  # pragma: no cover - defensive Windows fallback
            return None
        return "winsound"
    if sys.platform == "darwin" and shutil.which("afplay"):
        return "afplay"
    for executable in ("ffplay", "mpv", "aplay"):
        if shutil.which(executable):
            return executable
    return None


def _playback_command(backend: str, track: Path) -> list[str]:
    if backend == "afplay":
        return [backend, "-v", "0.14", "-r", "9999", str(track)]
    if backend == "ffplay":
        return [
            backend,
            "-nodisp",
            "-loglevel",
            "quiet",
            "-loop",
            "0",
            "-volume",
            "18",
            str(track),
        ]
    if backend == "mpv":
        return [
            backend,
            "--no-video",
            "--loop-file=inf",
            "--volume=15",
            "--really-quiet",
            str(track),
        ]
    if backend == "aplay":
        return [backend, "--quiet", str(track)]
    raise ValueError(f"Unsupported audio backend {backend!r}.")


def _write_original_chiptune(handle: object) -> None:
    """Write the deterministic, original pocket-adventure loop as PCM WAV."""

    sample_count = max(1, round(_STEP_SECONDS * _SAMPLE_RATE))
    samples = array("h")
    noise_state = 0xC0D3
    for step, melody_note in enumerate(_MELODY):
        lead_frequency = _note_frequency(melody_note)
        bass_frequency = _note_frequency(_BASS[step % len(_BASS)])
        for sample_index in range(sample_count):
            local_time = sample_index / _SAMPLE_RATE
            global_time = (step * sample_count + sample_index) / _SAMPLE_RATE
            attack = min(1.0, sample_index / max(1, _SAMPLE_RATE // 100))
            release = min(
                1.0,
                (sample_count - sample_index) / max(1, _SAMPLE_RATE // 45),
            )
            envelope = min(attack, release)
            lead = _square(lead_frequency, global_time, duty=0.25)
            bass = _square(bass_frequency, global_time, duty=0.5)
            noise_state = (noise_state * 1103515245 + 12345) & 0x7FFFFFFF
            noise = 1.0 if noise_state & 0x4000 else -1.0
            percussion_phase = local_time % _STEP_SECONDS
            percussion = (
                noise * (1.0 - percussion_phase / 0.045) * 0.10
                if step % 4 in {0, 2} and percussion_phase < 0.045
                else 0.0
            )
            sample = envelope * (lead * 0.20 + bass * 0.12 + percussion)
            samples.append(max(-32767, min(32767, round(sample * 32767))))

    frames = samples.tobytes()
    if sys.byteorder != "little":  # pragma: no cover - rare big-endian host
        little_endian = array("h", samples)
        little_endian.byteswap()
        frames = little_endian.tobytes()
    with wave.open(handle, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(_SAMPLE_RATE)
        output.writeframes(frames)


def _note_frequency(note: str | None) -> float:
    if note is None:
        return 0.0
    name = note[:-1]
    octave = int(note[-1])
    semitones = _NOTE_SEMITONES[name] + (octave - 4) * 12
    return 440.0 * math.pow(2.0, semitones / 12.0)


def _square(frequency: float, moment: float, *, duty: float) -> float:
    if frequency <= 0:
        return 0.0
    return 1.0 if (moment * frequency) % 1.0 < duty else -1.0


__all__ = ["BGM_ENV_VAR", "BGMState", "ChiptuneBGM"]
