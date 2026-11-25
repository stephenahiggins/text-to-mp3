import json
import subprocess
import pytest
from scripts import services


def test_inspection_failure_preserves_service_tracking(tmp_path, monkeypatch):
    monkeypatch.setattr(services, 'STATE', tmp_path)
    record = tmp_path / 'local.json'
    original = json.dumps({'pid': 123, 'identity': 'running backend'})
    record.write_text(original)
    monkeypatch.setattr(services.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 1, '', 'ps: Operation not permitted'))
    with pytest.raises(SystemExit, match='tracking was preserved'):
        services.read_state('local')
    assert record.read_text() == original


def test_exited_service_tracking_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(services, 'STATE', tmp_path)
    record = tmp_path / 'local.json'
    record.write_text(json.dumps({'pid': 123, 'identity': 'old backend'}))
    monkeypatch.setattr(services.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 1, '', ''))
    assert services.read_state('local') is None
    assert not record.exists()
