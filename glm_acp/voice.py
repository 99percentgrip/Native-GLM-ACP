"""Local voice capture, transcription, and notification helpers.

All features are opt-in and degrade gracefully when optional dependencies
or system tools are missing:

- Push-to-talk: requires ``faster-whisper`` (``pip install glm-acp[voice]``)
  and ``arecord`` (Linux) / ``afrecord`` (macOS).
- Notification sounds: terminal bell by default (zero deps); opt-in via
  ``GLM_ACP_SOUND=1``.
- Desktop notifications: ``notify-send`` (Linux), ``osascript`` (macOS),
  or PowerShell (Windows); enabled by default, disable via
  ``GLM_ACP_NOTIFY=0``.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time

WHISPER_MODEL_SIZE = os.environ.get("GLM_ACP_WHISPER_MODEL", "base")
_SOUND_ENABLED = os.environ.get("GLM_ACP_SOUND", "0") == "1"
_NOTIFY_ENABLED = os.environ.get("GLM_ACP_NOTIFY", "1") != "0"
_SOUND_COOLDOWN = 5.0
_NOTIFY_COOLDOWN = 30.0
_NOTIFY_MIN_TURN_SECONDS = 10.0
_last_sound = 0.0
_last_notify = 0.0
_whisper_model: object | None = None


# ---------------------------------------------------------------------------
# Voice: microphone capture + local Whisper transcription
# ---------------------------------------------------------------------------


def is_voice_available() -> bool:
    """Return True if faster-whisper is installed and importable."""
    try:
        import faster_whisper  # noqa: F401

        return True
    except ImportError:
        return False


def _get_whisper_model() -> object:
    """Lazy-load the Whisper model (downloads on first use, then cached)."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        _whisper_model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device="cpu",
            compute_type="int8",
        )
    return _whisper_model


def _recorder_command(output_path: str) -> list[str] | None:
    """Return the platform-specific recorder command, or None if unsupported."""
    if sys.platform.startswith("linux"):
        if _which("arecord"):
            return ["arecord", "-q", "-f", "cd", "-t", "wav", output_path]
    elif sys.platform == "darwin":
        if _which("afrecord"):
            return ["afrecord", "-f", "WAVE", output_path]
    return None


def _which(tool: str) -> bool:
    """Check if a command exists on PATH."""
    try:
        subprocess.run(
            ["which", tool],
            capture_output=True,
            check=False,
            timeout=3,
        )
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


class VoiceRecorder:
    """Manage a background microphone recording via system subprocess."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._path: str = ""

    @property
    def recording(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        """Start recording. Returns True if recording started."""
        if self.recording:
            return False
        command = _recorder_command("")  # check platform support
        if command is None and not _which("arecord") and sys.platform.startswith("linux"):
            return False
        tmp = tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False, prefix="glm-acp-voice-"
        )
        tmp.close()
        self._path = tmp.name
        full_command = _recorder_command(self._path)
        if full_command is None:
            return False
        try:
            self._process = subprocess.Popen(
                full_command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (OSError, FileNotFoundError):
            return False

    def stop(self) -> str | None:
        """Stop recording. Returns the WAV path, or None on failure."""
        if self._process is None:
            return None
        self._process.terminate()
        try:
            self._process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)
        self._process = None
        path = self._path
        self._path = ""
        if path and os.path.exists(path) and os.path.getsize(path) > 44:
            return path
        return None

    def cleanup(self) -> None:
        """Ensure recording is stopped and temp file removed."""
        if self.recording:
            self.stop()
        if self._path and os.path.exists(self._path):
            try:
                os.unlink(self._path)
            except OSError:
                pass


async def transcribe_audio(wav_path: str) -> str:
    """Transcribe a WAV file using local Whisper. Returns empty string on error."""
    if not is_voice_available():
        return ""
    try:
        model = await asyncio.to_thread(_get_whisper_model)
        segments, _info = await asyncio.to_thread(model.transcribe, wav_path)
        text = " ".join(segment.text for segment in segments).strip()
        return text
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Notification sounds (opt-in, not annoying)
# ---------------------------------------------------------------------------


def play_sound(tone: str = "success") -> None:
    """Play a short notification sound.

    Rules that make this ``not annoying``:
    - Opt-in only (GLM_ACP_SOUND=1, default off)
    - Cooldown: max one sound per 5 seconds
    - Duration: terminal bell is <50ms
    - Suppressed during voice recording (caller checks)
    """
    if not _SOUND_ENABLED:
        return
    global _last_sound
    now = time.monotonic()
    if now - _last_sound < _SOUND_COOLDOWN:
        return
    _last_sound = now
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


def suppress_sound_during_recording() -> None:
    """Reset the sound cooldown so the next eligible sound fires immediately after recording."""
    global _last_sound
    _last_sound = time.monotonic()


# ---------------------------------------------------------------------------
# Desktop notifications (smart, rate-limited)
# ---------------------------------------------------------------------------


def send_notification(
    title: str,
    message: str,
    *,
    error: bool = False,
    turn_duration: float = 0.0,
) -> None:
    """Send a desktop notification if smart rules allow it.

    Rules that make this ``smart``:
    - Only for turns > 10 seconds (skip instant responses)
    - Rate limit: max one notification per 30 seconds
    - Enabled by default, disable via GLM_ACP_NOTIFY=0
    """
    if not _NOTIFY_ENABLED:
        return
    if turn_duration > 0 and turn_duration < _NOTIFY_MIN_TURN_SECONDS:
        return
    global _last_notify
    now = time.monotonic()
    if now - _last_notify < _NOTIFY_COOLDOWN:
        return
    _last_notify = now
    try:
        if sys.platform.startswith("linux"):
            _notify_linux(title, message, error)
        elif sys.platform == "darwin":
            _notify_macos(title, message)
        elif sys.platform == "win32":
            _notify_windows(title, message)
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        pass


def _notify_linux(title: str, message: str, error: bool) -> None:
    expire = "10000" if error else "5000"
    urgency = "critical" if error else "normal"
    subprocess.run(
        [
            "notify-send",
            f"--expire-time={expire}",
            f"--urgency={urgency}",
            "--app-name=GLM ACP",
            title,
            message,
        ],
        capture_output=True,
        timeout=3,
        check=False,
    )


def _notify_macos(title: str, message: str) -> None:
    subprocess.run(
        [
            "osascript",
            "-e",
            f'display notification "{message}" with title "{title}"',
        ],
        capture_output=True,
        timeout=3,
        check=False,
    )


def _notify_windows(title: str, message: str) -> None:
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"[System.Reflection.Assembly]::LoadWithPartialName("
            f"'System.Windows.Forms'); "
            f"$balloon = New-Object System.Windows.Forms.NotifyIcon; "
            f"$balloon.Icon = [System.Drawing.SystemIcons]::Information; "
            f"$balloon.BalloonTipTitle = '{title}'; "
            f"$balloon.BalloonTipText = '{message}'; "
            f"$balloon.Visible = $true; "
            f"$balloon.ShowBalloonTip(5000)",
        ],
        capture_output=True,
        timeout=5,
        check=False,
    )
