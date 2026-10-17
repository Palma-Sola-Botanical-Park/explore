#!/usr/bin/env python3
"""Build a verified, resumable Groovin backup OUTSIDE Git. Never writes to R2.

First run groovin_inventory.cjs with Node, then pass its output with --inventory.
Use the same complete output folder on each display computer. An incomplete
build has no manifest.json and must not be activated. Requires Pillow.
"""
import argparse
import concurrent.futures
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from PIL import Image

PUBLIC = 'https://palma-sola-botanical-park.github.io/explore/'
FONT_CSS = 'https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;1,400&family=Source+Sans+3:wght@300;400;600;700&display=swap'
R2 = 'https://pub-895c4e39efa04b698caca4bce36ba281.r2.dev'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.part')
    temporary.write_bytes(data)
    temporary.replace(path)

def encoded(value):
    return json.dumps(value, indent=2, ensure_ascii=False).encode('utf-8')

def get(url):
    # urllib preserves platform/system certificate verification.
    request = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 PSBP-Groovin-Backup/1.0'})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()

def reject_repo_output(root, output):
    for parent in [output, *output.parents]:
        if (parent / '.git').exists():
            raise SystemExit('Backup media must be OUTSIDE every Git checkout: ' + str(output))
    if root == output or root in output.parents:
        raise SystemExit('Backup output cannot be inside the source repository.')

def build(args):
    root, output = Path(args.repo).resolve(), Path(args.output).resolve()
    reject_repo_output(root, output)
    inventory = json.loads(Path(args.inventory).read_text())
    staging = output.with_name(output.name + '.building')
    staging.mkdir(parents=True, exist_ok=True)
    progress_path = staging / 'download-progress.json'
    previous = json.loads(progress_path.read_text()) if progress_path.exists() else {}
    manifest = {'schema':1, 'complete':False, 'event':'groovin-2026-10-18',
                'built_at':datetime.now(timezone.utc).isoformat(), 'stats':inventory['stats'],
                'policy':'online-first, bounded requests, hard-drive fallback',
                'assets':{}, 'feeds':{}, 'files':{}, 'source_files':{}}
    urls = set(inventory['urls'])
    expected = {}
    library = json.loads((root / 'data/sources/media_library.json').read_text())
    # Mirror all public finished park media, including every newly tagged image.
    for record in library['items']:
        if record.get('public') != 'yes' or record.get('state') != 'done':
            continue
        for item in record.get('files', []):
            if item.get('role') in ('original', 'web') and item.get('url'):
                urls.add(item['url'])
                expected[item['url']] = item.get('sha256')
    for name in inventory['feeds']:
        data = (root / name).read_bytes()
        dest = 'feeds/' + name
        atomic(staging / dest, data)
        manifest['feeds'][name] = dest
        manifest['files'][dest] = {'sha256':digest(data), 'bytes':len(data)}
        manifest['source_files'][name] = digest(data)
    for name in ['screens/events/screen-groovin.html','screens/events/groovin-local-cache.js']:
        data = (root / name).read_bytes()
        dest = 'code/' + Path(name).name
        atomic(staging / dest, data)
        manifest['files'][dest] = {'sha256':digest(data), 'bytes':len(data)}
        manifest['source_files'][name] = digest(data)
    # Keep fonts fully local, including all Latin/extended subsets in the CSS.
    css_file = staging / 'fonts-source.css'
    css = css_file.read_text() if css_file.exists() else get(FONT_CSS).decode('utf-8')
    atomic(css_file, css.encode())
    font_urls = set(re.findall(r'url\((https://[^)]+)\)', css))
    urls.update(font_urls)
    def job(url):
        extension = Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).suffix.lower()
        if not extension or len(extension) > 6: extension = '.png' if 'qrserver' in url else '.jpg'
        relative = 'assets/' + digest(url.encode()) + extension
        file = staging / relative
        data = None
        record = previous.get(url)
        for candidate in [file, output / relative]:
            if candidate.is_file():
                raw = candidate.read_bytes()
                wanted = expected.get(url) or (record or {}).get('sha256')
                if wanted and digest(raw) == wanted:
                    data = raw
                    break
        if data is None and url.startswith(PUBLIC):
            source = root / urllib.parse.unquote(url[len(PUBLIC):])
            if source.is_file(): data = source.read_bytes()
        for attempt in range(3):
            try:
                if data is None: data = get(url)
                if not data: raise ValueError('Empty asset')
                if expected.get(url) and digest(data) != expected[url]:
                    raise ValueError('Registry checksum does not match')
                if url not in font_urls:
                    with Image.open(io.BytesIO(data)) as image:
                        image.verify()
                    with Image.open(io.BytesIO(data)) as image:
                        image.load()
                elif data[:4] not in (b'wOF2', b'wOFF', b'\x00\x01\x00\x00'):
                    raise ValueError('Invalid font')
                atomic(file, data)
                return url, {'path':relative,'sha256':digest(data),'bytes':len(data)}
            except Exception:
                data = None
                if attempt == 2: raise
                time.sleep(attempt + 1)
    failures=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(job,url):url for url in sorted(urls)}
        done=0
        for future in concurrent.futures.as_completed(futures):
            done += 1
            try:
                url, record = future.result()
                previous[url] = record
                manifest['assets'][url] = record
                manifest['files'][record['path']] = {k:v for k,v in record.items() if k != 'path'}
            except Exception as error:
                failures.append({'url':futures[future],'error':str(error)})
                print('FAILED', futures[future], str(error), flush=True)
            if done % 20 == 0 or done == len(futures):
                atomic(progress_path, encoded(previous))
                print(f'{done}/{len(futures)} verified; {len(failures)} failed', flush=True)
    if failures:
        atomic(staging / 'failures.json', encoded(failures))
        raise SystemExit('Incomplete build preserved for retry; no active manifest was written.')
    # iNaturalist large-image fallbacks resolve to the exact same local photo.
    credits=json.loads((root / 'data/sources/photo_credits.json').read_text())['photos']
    for row in credits:
        primary=R2 + '/inat/' + str(row.get('photo_id','')) + '/v1/original.jpg'
        if primary in manifest['assets'] and row.get('photo_url'):
            manifest['assets'][row['photo_url']]=manifest['assets'][primary]
    for url in font_urls:
        css=css.replace(url, manifest['assets'][url]['path'])
    atomic(staging / 'fonts.css', css.encode())
    manifest['files']['fonts.css']={'sha256':digest(css.encode()),'bytes':len(css.encode())}
    # Recheck everything immediately before publishing the completion marker.
    for name, record in manifest['files'].items():
        if digest((staging / name).read_bytes()) != record['sha256']:
            raise SystemExit('Final verification failed: '+name)
    manifest['complete']=True
    manifest['total_bytes']=sum(r['bytes'] for r in manifest['files'].values())
    atomic(staging / 'manifest.json', encoded(manifest))
    if output.exists():
        preserved=output.with_name(output.name+'.previous-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
        output.rename(preserved)
    staging.rename(output)
    print(json.dumps({'complete':True,'directory':str(output),'assets':len(manifest['assets']),
                      'files':len(manifest['files']),'bytes':manifest['total_bytes'],'stats':manifest['stats']},indent=2))

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument('--inventory',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--workers',type=int,choices=range(1,9),default=4)
    build(parser.parse_args())
