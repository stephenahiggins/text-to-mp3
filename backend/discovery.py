"""Bonjour advertisement and short-lived, locally approved pairing requests."""
import logging
import secrets
import subprocess
import threading
import time
import uuid


class Pairing:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests = {}

    def clear(self):
        with self.lock:
            self.requests.clear()

    def _expire(self):
        now = time.monotonic()
        self.requests = {key: value for key, value in self.requests.items() if value['expires'] > now}

    def request(self, secret, name):
        if not isinstance(secret, str) or len(secret) != 64 or any(c not in '0123456789abcdef' for c in secret):
            raise ValueError('Invalid pairing request.')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('Invalid device name.')
        with self.lock:
            self._expire()
            if secret not in self.requests:
                if len(self.requests) >= 20:
                    raise ValueError('Too many pairing requests. Try again in five minutes.')
                self.requests[secret] = dict(id=uuid.uuid4().hex, name=name.strip(), code=f'{secrets.randbelow(1000000):06d}', status='pending', expires=time.monotonic()+300)
            value = self.requests[secret]
            return {k: value[k] for k in ('status', 'code', 'token') if k in value}

    def pending(self):
        with self.lock:
            self._expire()
            return [{k: value[k] for k in ('id', 'name', 'code')} for value in self.requests.values() if value['status'] == 'pending']

    def decide(self, ident, token):
        with self.lock:
            self._expire()
            value = next((v for v in self.requests.values() if v['id'] == ident and v['status'] == 'pending'), None)
            if value is None:
                raise ValueError('Pairing request expired. Try again on the phone.')
            value['status'] = 'approved' if token else 'rejected'
            if token:
                value['token'] = token


class Advertisement:
    def __init__(self):
        self.process = None

    def start(self, port, ident):
        self.close()
        try:
            self.process = subprocess.Popen(
                ['/usr/bin/dns-sd', '-R', 'Reading Stacks', '_readingstacks._tcp', 'local.', str(port), 'version=1', 'id='+ident],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            logging.exception('Could not advertise Reading Stacks on Wi-Fi')

    def close(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process = None
