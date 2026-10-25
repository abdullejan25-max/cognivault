"""Private metadata journal, atomic replacement and crash-released OS locks.

This is task bookkeeping only, never a parallel business store. Callers choose
an access-controlled directory (normally ignored var/orchestration).
"""
from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import tempfile

from .models import TRANSITIONS, fingerprint, identifier, finite


class FileJournal:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, task_id):
        return self.root / (fingerprint(task_id) + ".json")

    @contextmanager
    def lock(self, task_id):
        # Never hold a blocking OS lock on the event loop.
        path = self._path(task_id).with_suffix(".lock")
        with path.open("a+b") as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("TASK_BUSY") from None
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def load(self, task_id):
        path = self._path(task_id)
        try:
            if path.stat().st_size > 100_000:
                raise ValueError("JOURNAL_CORRUPT")
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (ValueError, UnicodeError):
            raise ValueError("JOURNAL_CORRUPT") from None
        if (type(record) is not dict or type(record.get("schema")) is not int or record.get("schema") != 1
                or record.get("task_id") != task_id
                or not isinstance(record.get("events"), list)
                or type(record.get('state')) is not str or record.get("state") not in TRANSITIONS):
            raise ValueError("JOURNAL_CORRUPT")
        events = record['events']
        if (not events or events[0] != 'RECEIVED' or events[-1] != record['state']
                or any(type(e) is not str or e not in TRANSITIONS for e in events)
                or any(b not in TRANSITIONS[a] for a, b in zip(events, events[1:]))
                or type(record.get('attempts')) is not int or not 0 <= record['attempts'] <= 5
                or type(record.get('verified')) is not bool or type(record.get('commit_intent')) is not bool
                or not identifier(record.get('correlation_id')) or not identifier(record.get('adapter_id'))
                or record.get('execution_scope') not in {'unknown', 'synthetic', 'local', 'external'}
                or type(record.get('request_hash')) is not str
                or re.fullmatch(r'[0-9a-f]{64}', record['request_hash']) is None):
            raise ValueError('JOURNAL_CORRUPT')
        if record['commit_intent']:
            if ('COMMITTING' not in events or type(record.get('result_hash')) is not str
                    or re.fullmatch(r'[0-9a-f]{64}', record['result_hash']) is None):
                raise ValueError('JOURNAL_CORRUPT')
        elif any(e in {'COMMITTING', 'VERIFYING', 'COMPLETED', 'PARTIAL'} for e in events):
            raise ValueError('JOURNAL_CORRUPT')
        if ((record['state'] == 'COMPLETED' and not record['verified'])
                or record['verified'] and (not record['commit_intent'] or 'VERIFYING' not in events)):
            raise ValueError('JOURNAL_CORRUPT')
        review = record.get('review')
        if review is not None:
            if (type(review) is not dict or set(review) != {'reasons', 'confidence', 'evidence_count',
                                                            'uncertainty_count', 'candidate_sha256'}
                    or type(review['reasons']) is not list or not 1 <= len(review['reasons']) <= 3
                    or any(type(reason) is not str or reason not in {
                        'low_confidence', 'unresolved_uncertainty', 'missing_evidence'} for reason in review['reasons'])
                    or len(set(review['reasons'])) != len(review['reasons'])
                    or not finite(review['confidence'], 0, 1)
                    or any(type(review[key]) is not int or not 0 <= review[key] <= 32
                           for key in ('evidence_count', 'uncertainty_count'))
                    or type(review['candidate_sha256']) is not str
                    or re.fullmatch(r'[0-9a-f]{64}', review['candidate_sha256']) is None):
                raise ValueError('JOURNAL_CORRUPT')
        return record

    def save(self, task_id, record):
        data = json.dumps(record, sort_keys=True, allow_nan=False).encode("utf-8")
        if len(data) > 100_000:
            raise ValueError('JOURNAL_CAPACITY')
        fd, temp = tempfile.mkstemp(prefix=".task-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self._path(task_id))
            if os.name != "nt":
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
