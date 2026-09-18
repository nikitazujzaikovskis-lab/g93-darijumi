"""Restore the bundled snapshot once, then run the analytics application."""
import hashlib
import json
import runpy
import sys
import tarfile
import threading
from pathlib import Path
import streamlit as st

BASE = Path(__file__).resolve().parent

@st.cache_resource
def install_lock():
    return threading.Lock()

def restore():
    target = BASE / 'runtime'
    manifest = (BASE / 'snapshot.json').read_bytes()
    version = hashlib.sha256(manifest).hexdigest()
    marker = target / '.ready'
    with install_lock():
        if marker.exists() and marker.read_text() == version:
            return target
        target.mkdir(exist_ok=True)
        archive = BASE / 'snapshot.tmp.tar.gz'
        with archive.open('wb') as output:
            for item in json.loads(manifest)['parts']:
                content = (BASE / item['name']).read_bytes()
                if hashlib.sha256(content).hexdigest() != item['sha256']:
                    raise ValueError('Snapshot checksum mismatch: ' + item['name'])
                output.write(content)
        with tarfile.open(archive) as bundle:
            bundle.extractall(target, filter='data')
        archive.unlink()
        marker.write_text(version)
    return target

app_root = restore()
if str(app_root) not in sys.path:
    sys.path.insert(0, str(app_root))
runpy.run_path(str(app_root / 'app.py'), run_name='__main__')
