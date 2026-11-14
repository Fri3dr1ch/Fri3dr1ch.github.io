"""Detect changed and additional Grötzingen PDF editions without approving them."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urldefrag

from import_groetzingen import PdfLinks, SOURCE_PAGE

DEFAULT_FILES = ['groetzingen-jahresprogramm-2026.json', 'groetzingen-kultur-natur-2026.json']


def load_extracts(root):
    folder = root / 'data/groetzingen'
    manifest = folder / 'manifest.json'
    filenames = json.loads(manifest.read_text()) if manifest.exists() else DEFAULT_FILES
    if not isinstance(filenames, list) or len(filenames) != 2:
        raise ValueError('PDF-Manifest muss genau zwei geprüfte Extrakte nennen')
    if any(not isinstance(name, str) or Path(name).name != name or not name.endswith('.json') for name in filenames):
        raise ValueError('Ungültiger Extraktpfad im PDF-Manifest')
    paths = [folder / name for name in filenames]
    extracts = [json.loads(p.read_text()) for p in paths]
    if {s['id'] for s in extracts} != {'groetzingen-jahresprogramm', 'groetzingen-kultur-natur'}:
        raise ValueError('PDF-Manifest enthält nicht die erwarteten Quellen')
    return extracts


def check(root, fetch, old_sources, previous_checks=None):
    extracts = load_extracts(root)
    previous_checks = previous_checks or {}
    old = {s['id']: s for s in old_sources}
    attempted = datetime.now(timezone.utc).isoformat(timespec='seconds')
    try:
        parser = PdfLinks()
        parser.feed(fetch(SOURCE_PAGE).decode('utf-8'))
        linked = sorted({urldefrag(url)[0] for url in parser.links})
        if not linked:
            raise ValueError('Grötzingen: keine PDF-Links mehr erkannt')
        page_error = None
    except Exception as error:
        linked = []
        page_error = error
    results = {}
    for source in extracts:
        sid = source['id']
        retained = {k: old.get(sid, {}).get(k) for k in ('lastSuccessAt', 'reviewRequired', 'reviewReason', 'candidateUrls') if k in old.get(sid, {})}
        retained.update(previous_checks.get(sid, {}))
        try:
            if page_error:
                raise page_error
            kind = 'Jahresprogramm' if sid.endswith('jahresprogramm') else 'Kulturbroschuere'
            approved_year = max(map(int, re.findall(r'20\d{2}', source.get('edition', '') + ' ' + source['pdfUrl'])))
            candidates = []
            for url in linked:
                if kind.lower() not in unquote(url).lower():
                    continue
                years = list(map(int, re.findall(r'20\d{2}', unquote(url))))
                if not years or max(years) >= approved_year:
                    candidates.append(url)
            changed = source['pdfUrl'] not in candidates
            reasons = ['Bisherige PDF-Ausgabe ist nicht mehr verlinkt.'] if changed else []
            if source['pdfUrl'] in candidates:
                changed_bytes = hashlib.sha256(fetch(source['pdfUrl'])).hexdigest() != source['sha256']
                changed |= changed_bytes
                if changed_bytes: reasons.append('Der Inhalt der bisher importierten PDF-Datei wurde geändert.')
            additional = [url for url in candidates if url != source['pdfUrl']]
            if additional:
                changed = True
                reasons.append('Zusätzliche oder neuere PDF-Ausgabe verlinkt.')
            checked = datetime.now(timezone.utc).isoformat(timespec='seconds')
            results[sid] = {'lastAttemptAt': attempted, 'lastCheckedAt': checked,
                            'lastSuccessAt': retained.get('lastSuccessAt') if changed else checked,
                            'lastReviewedAt': source['reviewedOn'], 'refreshHours': 24,
                            'status': 'review-required' if changed else 'ok', 'reviewRequired': bool(changed),
                            'reviewReason': ' '.join(reasons), 'candidateUrls': candidates}
        except Exception as error:
            results[sid] = {**retained, 'status': 'error', 'lastAttemptAt': attempted,
                            'lastError': type(error).__name__ + ': ' + str(error)}
    return results
