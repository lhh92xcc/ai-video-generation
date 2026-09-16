from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/download-local-target-models-mac.sh'


def test_interrupted_model_download_resumes_before_publishing(tmp_path: Path) -> None:
    """Exercise the real shell helper with an interrupted transfer, without network."""
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    curl = fake_bin / 'curl'
    curl.write_text(
        f'#!{sys.executable}\n'
        'import os, pathlib, sys\n'
        'args = sys.argv[1:]\n'
        'target = pathlib.Path(args[args.index("-o") + 1])\n'
        'assert args[args.index("--continue-at") + 1] == "-"\n'
        'if os.environ["TEST_TRANSFER"] == "interrupt":\n'
        '    target.write_bytes(b"first-half")\n'
        '    sys.exit(18)\n'
        'assert target.read_bytes() == b"first-half", "partial bytes were not resumed"\n'
        'target.write_bytes(target.read_bytes() + b"-second-half")\n',
        encoding='utf-8',
    )
    curl.chmod(0o755)
    helper = SCRIPT.read_text(encoding='utf-8').split('# Flux Schnell Q4')[0]
    command = ['bash', '-c', helper + '\ndownload_file "$TEST_MODEL_URL" "$TEST_MODEL_TARGET"\n']
    target = tmp_path / 'models' / 'model.gguf'
    partial = target.with_name(target.name + '.part')
    env = {**os.environ, 'PATH': str(fake_bin) + os.pathsep + os.environ['PATH'],
           'TEST_MODEL_URL': 'https://example.invalid/model.gguf',
           'TEST_MODEL_TARGET': str(target), 'TEST_TRANSFER': 'interrupt'}
    interrupted = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
    assert interrupted.returncode != 0
    assert not target.exists(), 'an incomplete download must not appear as an installed model'
    assert partial.read_bytes() == b'first-half'

    env['TEST_TRANSFER'] = 'finish'
    resumed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
    assert resumed.returncode == 0, resumed.stderr
    assert target.read_bytes() == b'first-half-second-half'
    assert not partial.exists()

    # A completed file must be reused without invoking curl again.
    env['TEST_TRANSFER'] = 'interrupt'
    reused = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
    assert reused.returncode == 0
    assert target.read_bytes() == b'first-half-second-half'
