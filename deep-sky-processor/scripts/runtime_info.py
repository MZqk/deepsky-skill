"""Runtime provenance and parameters consumed by completed processing stages."""

import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import platform
import subprocess
import sys


def source_fingerprint(root):
    root = Path(root)
    files = [root / 'SKILL.md', root / 'requirements.txt']
    files += sorted(root.glob('requirements*.lock'))
    files += sorted((root / 'scripts').glob('*.py'))
    files += sorted(p for p in (root / 'references').rglob('*') if p.is_file())
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode() + b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def runtime_info(script_path, policy_version=3):
    script = Path(script_path).resolve()
    root = script.parent.parent
    receipt = root / 'runtime_source.json'
    origin = json.loads(receipt.read_text()) if receipt.exists() else {}
    if not origin:
        try:
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], text=True,
                                               stderr=subprocess.DEVNULL).strip()
            origin = {'source_commit': git('rev-parse', 'HEAD'),
                      'source_dirty': bool(git('status', '--porcelain', '--', '.'))}
        except (OSError, subprocess.CalledProcessError):
            origin = {'source_commit': None, 'source_dirty': None}
    version = re.search(r'^\s*version:\s*["\']?([^"\'\s]+)',
                        (root / 'SKILL.md').read_text(), re.MULTILINE).group(1)
    dependencies = {}
    for name in ('numpy', 'scipy', 'scikit-image', 'opencv-python', 'PyWavelets', 'astropy', 'xisf', 'tifffile', 'Pillow'):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    fingerprint = source_fingerprint(root)
    return {'skill_version': version, 'source_commit': origin.get('source_commit'),
            'source_dirty': origin.get('source_dirty'), 'source_sha256': fingerprint,
            'installed_snapshot_matches': (fingerprint == origin['source_sha256']
                                           if origin.get('source_sha256') else None),
            'script_path': str(script), 'skill_root': str(root),
            'python_executable': sys.executable, 'python_version': sys.version.split()[0],
            'dependencies': dependencies, 'stretch_policy_version': policy_version,
            'platform': platform.platform(), 'architecture': platform.machine(),
            'dependency_lock_sha256': (hashlib.sha256((root / 'requirements.lock').read_bytes()).hexdigest()
                                       if (root / 'requirements.lock').exists() else None)}


class ExecutionConfig(dict):
    """Track configuration reads inside operations, excluding preset/safety edits."""

    def __init__(self, values):
        super().__init__(values)
        self.phase = None
        self.used = {}
        self.completed = []

    def _read(self, key, value):
        if self.phase is not None:
            self.used.setdefault(self.phase, {})[key] = value
        return value

    def __getitem__(self, key):
        return self._read(key, super().__getitem__(key))

    def get(self, key, default=None):
        return self._read(key, super().get(key, default))
