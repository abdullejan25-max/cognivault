"""Bounded filesystem and SQLite evidence primitives used inside the Gateway."""
from contextlib import closing, contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import shutil
import tempfile
from ..adapters.documents import _path_has_reparse_point, _stream_signature
from ..contracts import GatewayError
from ..migration.inventory import _open_verified_source

MAX_FILES = 200_000
MAX_MANIFEST = 32 * 1024 * 1024


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def fail(code='CONFLICT'):
    raise GatewayError(code,'Recovery verification failed')


def safe(path, *, directory=False, private=False):
    path=Path(path)
    if not path.is_absolute() or _path_has_reparse_point(physical(path)): fail('OUTSIDE_ALLOWLIST')
    resolved=path.resolve()
    if private and any((p/'.git').exists() for p in (resolved,*resolved.parents)): fail('OUTSIDE_ALLOWLIST')
    if physical(path).exists():
        info=physical(path).stat(follow_symlinks=False)
        if directory:
            if not stat.S_ISDIR(info.st_mode): fail('OUTSIDE_ALLOWLIST')
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink!=1: fail('OUTSIDE_ALLOWLIST')
    return resolved


def relative(value):
    if type(value) is not str or not value or len(value)>1000 or '\\' in value or ':' in value \
            or any(ord(c)<32 for c in value) or any(p in {'','..','.'} for p in value.split('/')):
        fail('INVALID_ARGUMENT')
    return value


def signature(path):
    info=physical(path).stat(follow_symlinks=False)
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)


def physical(path):
    value=str(Path(path).absolute())
    if os.name=='nt' and not value.startswith('\\\\?\\'):
        value='\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value
    return Path(value)


def files(root, *, excluded=()):
    root=safe(root,directory=True)
    if not root.is_dir(): fail('STORAGE_UNAVAILABLE')
    exclusions={relative(p) for p in excluded}
    out=[]
    def error(_): fail('STORAGE_UNAVAILABLE')
    for current,dirs,names in os.walk(physical(root),topdown=True,followlinks=False,onerror=error):
        base=Path(current)
        if os.name=='nt' and str(base).startswith('\\\\?\\'):
            value=str(base);base=Path('\\\\'+value[8:] if value.startswith('\\\\?\\UNC\\') else value[4:])
        for name in sorted(dirs):
            p=base/name;rel=p.relative_to(root).as_posix()
            if any(rel==x or rel.startswith(x+'/') for x in exclusions): dirs.remove(name);continue
            if name=='.git': fail('OUTSIDE_ALLOWLIST')
            safe(p,directory=True)
        for name in sorted(names):
            p=base/name;rel=p.relative_to(root).as_posix()
            if any(rel==x or rel.startswith(x+'/') for x in exclusions): continue
            safe(p);out.append((rel,p,signature(p)))
            if len(out)>MAX_FILES: fail('PAYLOAD_TOO_LARGE')
    return sorted(out)


def digest_file(path,root):
    digest=hashlib.sha256();size=0
    with _open_verified_source(path,root) as stream:
        if os.fstat(stream.fileno()).st_nlink!=1: fail('OUTSIDE_ALLOWLIST')
        before=_stream_signature(stream)
        while data:=stream.read(1024*1024): digest.update(data);size+=len(data)
        if before!=_stream_signature(stream): fail()
    return digest.hexdigest(),size


def copy_file(source,source_root,target):
    safe(target);physical(target.parent).mkdir(parents=True,exist_ok=True);safe(target.parent,directory=True)
    digest=hashlib.sha256();size=0
    with _open_verified_source(source,source_root) as stream, physical(target).open('xb') as destination:
        if os.fstat(stream.fileno()).st_nlink!=1: fail('OUTSIDE_ALLOWLIST')
        before=_stream_signature(stream)
        while data:=stream.read(1024*1024): destination.write(data);digest.update(data);size+=len(data)
        destination.flush();os.fsync(destination.fileno())
        if before!=_stream_signature(stream): fail()
    return digest.hexdigest(),size


def sqlite_proof(path):
    safe(path)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or list(c.execute('PRAGMA foreign_key_check')): fail()
        schema=list(c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
        tables={}
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            quote=lambda value:'"'+value.replace('"','""')+'"'
            quoted=quote(name);digests=[]
            columns=[r for r in c.execute('PRAGMA table_xinfo('+quoted+')') if r[6]!=1]
            names=[r[1] for r in columns]
            rowid=next((v for v in ('rowid','_rowid_','oid') if v not in {n.casefold() for n in names}),None)
            try:c.execute('SELECT '+str(rowid)+' FROM '+quoted+' LIMIT 0')
            except sqlite3.OperationalError:rowid=None
            pk=[r[1] for r in sorted(columns,key=lambda r:r[5]) if r[5]]
            order=' ORDER BY '+','.join(map(quote,pk)) if pk else ''
            if rowid is None and not pk: fail()
            can_blob=bool(rowid) and not any(r[6] in {2,3} for r in columns) \
                and not any(r[1]==name and isinstance(r[3],str) and r[3].upper().startswith('CREATE VIRTUAL TABLE') for r in schema)
            fields=[]
            for col in names:
                q=quote(col)
                fields.extend(['typeof('+q+')','CASE WHEN typeof('+q+")='blob' THEN NULL ELSE "+q+' END','length('+q+')'])
            query='SELECT '+((rowid+',') if rowid else '')+','.join(fields)+' FROM '+quoted+order
            for position,row in enumerate(c.execute(query)):
                values=[]
                native=row[0] if rowid else None;payload=row[1:] if rowid else row
                for index,col in enumerate(names):
                    kind,value,length=payload[index*3:index*3+3]
                    if kind=='blob':
                        digest=hashlib.sha256()
                        if can_blob:
                            with c.blobopen(name,col,native,readonly=True) as blob:
                                if len(blob)!=length: fail()
                                while chunk:=blob.read(1024*1024):digest.update(chunk)
                        else:
                            for offset in range(0,length,1024*1024):
                                query='SELECT substr('+quote(col)+',?,?) FROM '+quoted
                                if rowid:
                                    query+=' WHERE '+rowid+'=?';parameters=(offset+1,1024*1024,native)
                                else:
                                    query+=order+' LIMIT 1 OFFSET ?';parameters=(offset+1,1024*1024,position)
                                chunk=c.execute(query,parameters).fetchone()[0]
                                if len(chunk)!=min(length-offset,1024*1024): fail()
                                digest.update(chunk)
                        values.append(['bytes',length,digest.hexdigest()])
                    elif isinstance(value,float) and not math.isfinite(value):values.append(['float',repr(value)])
                    else:values.append([type(value).__name__,value])
                digests.append(hashlib.sha256(encoded(values)).digest())
            tables[name]={'rows':len(digests),'sha256':hashlib.sha256(b''.join(sorted(digests))).hexdigest()}
        return {'schema_sha256':hashlib.sha256(encoded(schema)).hexdigest(),'tables':tables,
                'user_version':c.execute('PRAGMA user_version').fetchone()[0]}


def sqlite_copy(source,target):
    safe(source);safe(target);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists(): fail()
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst);dst.commit();dst.execute('PRAGMA journal_mode=DELETE')


def sqlite_size(path):
    safe(path)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
        c.execute('BEGIN')
        return c.execute('PRAGMA page_count').fetchone()[0]*c.execute('PRAGMA page_size').fetchone()[0]


def restore_capacity(root,payload_bytes,qmd_index_bytes=0,*,reuse=False):
    """Conservative additional space, including the disposable QMD runtime volume."""
    ancestor=Path(root)
    while not ancestor.exists(): ancestor=ancestor.parent
    temporary=Path(tempfile.gettempdir())
    same_volume=ancestor.stat().st_dev==temporary.stat().st_dev
    reserve_bytes=max(4*1024**3,(payload_bytes+9)//10)
    # Restored History/Assets are read-only. Only the copied QMD index is writable;
    # allow another full index for its WAL plus bounded cache/receipt overhead.
    temporary_bytes=qmd_index_bytes+64*1024**2
    wal_bytes=max(64*1024**2,qmd_index_bytes)
    copy_bytes=0 if reuse else payload_bytes
    required=copy_bytes+temporary_bytes+wal_bytes+reserve_bytes
    temporary_required=0 if same_volume else temporary_bytes+wal_bytes+reserve_bytes
    free=shutil.disk_usage(ancestor).free;temporary_free=shutil.disk_usage(temporary).free
    return {'restore_payload_bytes':payload_bytes,'restore_copy_bytes':copy_bytes,
            'restore_temp_bytes':temporary_bytes,'restore_wal_bytes':wal_bytes,
            'restore_reserve_bytes':reserve_bytes,'restore_required_bytes':required,'restore_free_bytes':free,
            'temporary_volume_required_bytes':temporary_required,'temporary_volume_free_bytes':temporary_free,
            'temporary_volume_same_as_restore':same_volume,
            'sufficient':free>=required and temporary_free>=temporary_required}


@contextmanager
def reserve(databases):
    held=[]
    try:
        for path in sorted(set(databases)):
            safe(path)
            c=sqlite3.connect(path,timeout=10);held.append(c);c.execute('BEGIN IMMEDIATE')
        yield
    finally:
        for c in reversed(held): c.rollback();c.close()


def write_json(path,value):
    safe(path);path.parent.mkdir(parents=True,exist_ok=True);data=encoded(value)
    if len(data)>MAX_MANIFEST: fail('PAYLOAD_TOO_LARGE')
    with path.open('xb') as stream: stream.write(data);stream.flush();os.fsync(stream.fileno())
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    safe(path)
    if not path.is_file() or path.stat().st_size>MAX_MANIFEST: fail()
    def unique(pairs):
        value={}
        for k,v in pairs:
            if k in value: fail()
            value[k]=v
        return value
    return json.loads(path.read_bytes(),object_pairs_hook=unique,parse_constant=lambda _:fail())
