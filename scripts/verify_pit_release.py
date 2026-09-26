"""Re-score the published PIT pilot from raw responses; no API configuration needed."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pit_benchmark.pilot import load_fixture
from pit_benchmark.run import replay


def main() -> None:
    receipt_path = ROOT / 'docs/verification/pit-pilot-live-20260926.json'
    published_path = ROOT / 'site/workbench/data/pit_pilot.json'
    if receipt_path.read_bytes() != published_path.read_bytes():
        raise ValueError('Published data differs from the first-run receipt')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    trace_name = receipt['trace_file']
    if Path(trace_name).name != trace_name:
        raise ValueError('Trace must be beside the receipt')
    fixture, manifest, audit = load_fixture()
    reconstructed = replay(receipt_path.with_name(trace_name), fixture, manifest, audit)
    fields = ('dataset_sha256', 'frozen_at', 'protocol', 'cases', 'evidence',
              'provenance', 'fixture_audit', 'execution', 'started_at',
              'completed_at', 'trace_sha256', 'runs', 'summary')
    for field in fields:
        if receipt[field] != reconstructed[field]:
            raise ValueError(f'Published field differs from raw-response replay: {field}')
    print(json.dumps({'status': 'verified', 'network_calls': 0, 'model_calls': 0,
                      'planned_runs': receipt['summary']['planned_runs'],
                      'scored_runs': receipt['summary']['scored_runs'],
                      'dataset_sha256': receipt['dataset_sha256']}))


if __name__ == '__main__':
    main()
