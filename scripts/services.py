"""Start/stop only this checkout's managed development services."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.data' / 'services'
SERVICES = {
    'local': (ROOT, [str(ROOT / '.venv/bin/python'), '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', '8765'], 8765),
    'expo': (ROOT / 'mobile', [str(ROOT / 'mobile/node_modules/.bin/expo'), 'start', '--dev-client', '--port', '8081'], 8081),
}


def identity(pid):
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'args='], capture_output=True, text=True)
    if result.returncode != 0 and result.stderr.strip():
        raise SystemExit('Cannot inspect service processes: ' + result.stderr.strip() +
                         '\nService tracking was preserved. Run this command from a terminal with process access.')
    return result.stdout.strip() if result.returncode == 0 else ''


def read_state(name):
    path = STATE / (name + '.json')
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if value['identity'] and identity(value['pid']) == value['identity']:
        return value
    path.unlink()
    return None


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(.2)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def start(name):
    cwd, command, port = SERVICES[name]
    if read_state(name):
        print(f'{name} is already running; log: {STATE / (name + ".log")}')
        return
    if listening(port):
        raise SystemExit(f'Port {port} is already in use by an unmanaged process. Stop its original terminal with Ctrl-C before retrying. No process was stopped.')
    if not Path(command[0]).exists():
        raise SystemExit('Run make setup' + ('-mobile' if name == 'expo' else '') + ' first.')
    STATE.mkdir(parents=True, exist_ok=True)
    log = STATE / (name + '.log')
    with log.open('ab') as stream:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    # The launcher can exec into Node; record its stable command after startup.
    for _ in range(120):
        if process.poll() is not None:
            raise SystemExit(f'{name} failed to start. See {log}')
        if listening(port):
            break
        time.sleep(.25)
    record = {'pid': process.pid, 'identity': identity(process.pid)}
    (STATE / (name + '.json')).write_text(json.dumps(record))
    if not listening(port):
        raise SystemExit(f'{name} is still starting. See {log}; use make stop to stop it.')
    print(f'{name} started on port {port}. Log: {log}')
    if name == 'local':
        print('Open http://127.0.0.1:8765')


def stop(name):
    record = read_state(name)
    if not record:
        print(f'{name}: no managed service running')
        return
    os.killpg(record['pid'], signal.SIGINT)
    for _ in range(80):
        if identity(record['pid']) != record['identity']:
            (STATE / (name + '.json')).unlink(missing_ok=True)
            print(f'{name} stopped')
            return
        time.sleep(.25)
    raise SystemExit(f'{name} is still shutting down. Check its log and retry make status. No forced termination was used.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status'])
    parser.add_argument('service', nargs='?', choices=SERVICES)
    args = parser.parse_args()
    for name in ([args.service] if args.service else SERVICES):
        if args.action == 'start':
            start(name)
        elif args.action == 'stop':
            stop(name)
        else:
            record = read_state(name)
            state = f'running (PID {record["pid"]})' if record else 'not managed/running'
            print(f'{name}: {state}; port {SERVICES[name][2]} ' + ('in use' if listening(SERVICES[name][2]) else 'free'))
