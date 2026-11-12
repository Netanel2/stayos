#!/usr/bin/env python3
"""Consistent STAYOS SQLite + hotel media backups with integrity verification.

Use --now to create a snapshot. Offsite uploads require explicitly configured S3.
Use --verify PATH to test a snapshot without modifying production data.
Use --restore-to DIRECTORY PATH to extract into an *empty* separate directory.
Do not replace a live database or /data tree with restore output.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from pathlib import Path


def verify_archive(filename: Path) -> dict:
    with zipfile.ZipFile(filename, 'r') as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f'Corrupt backup archive member: {bad}')
        names = z.namelist()
        if 'platform.sqlite' not in names or 'manifest.json' not in names:
            raise RuntimeError('Incomplete backup archive')
        metadata = json.loads(z.read('manifest.json'))
        with tempfile.TemporaryDirectory(prefix='stay-verify-') as tmp:
            target = Path(tmp) / 'platform.sqlite'
            with z.open('platform.sqlite') as source, target.open('wb') as dest:
                shutil.copyfileobj(source, dest)
            db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
            try:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup database failed SQLite integrity_check')
                db.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()
            finally:
                db.close()
        media_count = sum(1 for name in names if name.startswith('media/') and not name.endswith('/'))
        if media_count != metadata['media_count']:
            raise RuntimeError('Backup image count mismatch')
        return {'ok': True, 'media_count': media_count, 'created_utc': metadata['created_utc']}


def snapshot(db_file: Path, media_dir: Path, backup_dir: Path) -> tuple[Path, dict]:
    db_file = Path(db_file); media_dir = Path(media_dir); backup_dir = Path(backup_dir)
    if not db_file.is_file():
        raise FileNotFoundError(f'STAYOS database missing: {db_file}')
    backup_dir.mkdir(parents=True, exist_ok=True)
    # SQLite online-backup API reads a consistent snapshot even with WAL writers.
    with tempfile.TemporaryDirectory(prefix='.stage-', dir=backup_dir) as staging:
        temp_db = Path(staging) / 'platform.sqlite'
        source = sqlite3.connect(f'file:{db_file}?mode=ro', uri=True, timeout=30)
        target = sqlite3.connect(temp_db)
        try:
            source.backup(target, pages=100, sleep=0.05)
        finally:
            target.close();source.close()
        checked = sqlite3.connect(temp_db)
        try:
            if checked.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('SQLite source backup failed integrity_check')
        finally:
            checked.close()
        now = dt.datetime.now(dt.timezone.utc)
        images = sorted(x for x in media_dir.rglob('*') if x.is_file() and not x.is_symlink()) if media_dir.exists() else []
        manifest = {'format':1, 'created_utc':now.isoformat(), 'media_count':len(images), 'scope':'all-hotels'}
        candidate = Path(staging) / 'snapshot.zip'
        with zipfile.ZipFile(candidate, 'w') as z:
            z.write(temp_db, 'platform.sqlite', compress_type=zipfile.ZIP_DEFLATED)
            for file in images:
                # WebP photos are already compressed, avoid redundant CPU/disk work.
                z.write(file, 'media/' + file.relative_to(media_dir).as_posix(), compress_type=zipfile.ZIP_STORED)
            z.writestr('manifest.json', json.dumps(manifest), compress_type=zipfile.ZIP_DEFLATED)
        info = verify_archive(candidate)
        filename = f'stayos-{now:%Y%m%dT%H%M%SZ}-{os.urandom(3).hex()}.zip'
        final = backup_dir / filename
        os.replace(candidate, final)
    return final, info


def s3_upload(filename: Path) -> bool:
    bucket = os.environ.get('STAYOS_BACKUP_S3_BUCKET', '').strip()
    if not bucket:
        return False
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError('S3 offsite backup requested, but boto3 is not installed') from exc
    args = {}
    if os.environ.get('STAYOS_BACKUP_S3_ENDPOINT'):
        args['endpoint_url'] = os.environ['STAYOS_BACKUP_S3_ENDPOINT']
    cli = boto3.client('s3', **args)
    key = os.environ.get('STAYOS_BACKUP_S3_PREFIX', 'stayos/backups/').strip('/') + '/' + filename.name
    cli.upload_file(str(filename), bucket, key, ExtraArgs={'ServerSideEncryption': 'AES256'})
    return True


def perform(db: Path, media: Path, dest: Path, keep: int = 7) -> dict:
    dest = Path(dest)
    previous = {}
    try:
        if (dest / 'status.json').exists():
            previous = json.loads((dest / 'status.json').read_text())
    except Exception:
        previous = {}
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        archive, details = snapshot(db, media, dest)
        offsite = s3_upload(archive)
        # Preserve recent local snapshots; NEVER delete a snapshot unless its
        # replacement passed integrity verification and, if configured, upload.
        local = sorted(dest.glob('stayos-*.zip'), reverse=True)
        for older in local[max(1, keep):]:
            older.unlink()
        result = {'status':'success','at':now,'archive':archive.name,
                  'offsite_saved':offsite, 'media_count':details['media_count']}
    except Exception as exc:
        result = {'status':'failed','at':now,'error':str(exc)[:300],
                  'previous_success':previous if previous.get('status')=='success' else previous.get('previous_success')}
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / ('status-' + os.urandom(4).hex() + '.tmp')
    tmp.write_text(json.dumps(result))
    os.replace(tmp, dest / 'status.json')
    return result


def backup_worker(db: Path, media: Path, dest: Path):
    interval = max(1.0, float(os.environ.get('STAYOS_BACKUP_INTERVAL_HOURS','24'))) * 3600
    # Allow the main server to start and accept requests first.
    time.sleep(90)
    while True:
        report = perform(db, media, dest)
        print('STAYOS backup',report['status'],'offsite',report.get('offsite_saved',False), flush=True)
        time.sleep(interval)


def restore_to(filename: Path, dest: Path):
    verify_archive(filename)
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise RuntimeError('Restore destination must be empty. Never restore over live /data.')
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(filename) as z:
        # Explicit safe paths instead of extractall on untrusted archive data.
        for member in z.infolist():
            name = member.filename
            if name in ('manifest.json','platform.sqlite') or (name.startswith('media/') and not name.endswith('/')):
                target = (dest / name).resolve()
                if not target.is_relative_to(dest.resolve()):
                    raise RuntimeError('Invalid backup member path')
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src,dst)
    return dest


if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    data = Path(os.environ.get('STAYOS_DATA_DIR','/data' if os.environ.get('RAILWAY_ENVIRONMENT') else str(root / 'local-data')))
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--now',action='store_true')
    group.add_argument('--verify',type=Path)
    group.add_argument('--restore-to',type=Path)
    parser.add_argument('--archive',type=Path, help='Backup zip required with --restore-to')
    args=parser.parse_args()
    if args.now:
        result=perform(Path(os.environ.get('STAYOS_PLATFORM_DB',data/'platform.sqlite')),
                       Path(os.environ.get('STAYOS_MEDIA_DIR',data/'media')), data/'backups')
        print(json.dumps(result));raise SystemExit(0 if result['status']=='success' else 1)
    if args.verify:
        print(json.dumps(verify_archive(args.verify)));raise SystemExit(0)
    if args.restore_to:
        if not args.archive: parser.error('--archive is required with --restore-to')
        print(f'Restored to isolated directory: {restore_to(args.archive,args.restore_to)}')