"""Frozen source facts for historical regressions, never today's rolling feed."""
import json
from pathlib import Path

from calendar_sources import combine_local_sources


def historical_dataset(exclude=(), stamp=None):
    path=Path(__file__).parent/'fixtures/regional-merge-scenarios-2026-11.json'
    data=json.loads(path.read_text())
    if stamp is not None: data['base']['generatedAt']=stamp
    return combine_local_sources(data['base'],[s for s in data['snapshots'] if s['source']['id'] not in exclude])
