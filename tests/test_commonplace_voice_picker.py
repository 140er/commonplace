"""Browser dictation state and PCM conversion, exercised without microphones."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_voice_picker_pcm_and_capture_lifecycle():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed for voice controller checks")
    script = Path(__file__).with_name("commonplace_voice_picker.mjs")
    subprocess.run([node, str(script)], check=True, timeout=15)
