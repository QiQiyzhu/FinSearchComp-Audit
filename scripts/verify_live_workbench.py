"""Explicit opt-in live model verification. One selected mode, one submitted job."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Submit one potentially billed model request')
    parser.add_argument('--base-url', default='http://127.0.0.1:8090')
    parser.add_argument('--mode', choices=['snapshot', 'live'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.execute:
        parser.error('Actual provider use requires --execute. Offline checks use smoke_workbench.py.')
    if args.output.exists():
        parser.error('Choose a new output filename; verification evidence is never overwritten.')
    headers = {}
    if os.getenv('FINAGENT_API_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['FINAGENT_API_TOKEN']
    payload = {'question': '分析微软FY2024经营现金流和资本支出，计算自由现金流。', 'ticker': 'MSFT', 'as_of': '2024-11-01', 'mode': args.mode}
    start = time.monotonic()
    with httpx.Client(base_url=args.base_url.rstrip('/'), headers=headers, timeout=20, trust_env=False) as client:
        response = client.post('/api/research', json=payload)
        response.raise_for_status()
        identifier = response.json()['id']
        deadline = time.monotonic() + 150
        while True:
            response = client.get('/api/research/' + identifier)
            response.raise_for_status()
            job = response.json()
            if job['status'] in {'completed', 'failed'} or time.monotonic() >= deadline:
                break
            time.sleep(.5)
        report = job.get('result') or {}
        model = report.get('model', {})
        receipt = {
            'checked_at': datetime.now(timezone.utc).isoformat(), 'source': 'actual local HTTP service; no mocks',
            'submitted_jobs': 1, 'request': payload, 'job_id': identifier, 'job_status': job['status'],
            'elapsed_seconds': round(time.monotonic() - start, 3), 'model': model,
            'checks': {
                'job_completed': job['status'] == 'completed',
                'real_model_completed': bool(model.get('used')) and model.get('status') == 'completed',
                'cutoff_respected': bool(report.get('evidence')) and all(e['published_at'] <= payload['as_of'] for e in report.get('evidence', [])),
                'free_cash_flow_value': next((m['value'] for m in report.get('metrics', []) if m['id'] == 'free_cash_flow'), None),
            },
            'error': job.get('error'), 'report': report,
            'limits': 'One authored smoke case; confirms transport and evidence pipeline, not finance research quality or trading performance.'
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({k: receipt[k] for k in ['job_status', 'elapsed_seconds', 'model', 'checks', 'error']}, ensure_ascii=True))
        assert receipt['checks']['real_model_completed'], 'Actual model call did not complete; inspect preserved receipt.'
        assert receipt['checks']['cutoff_respected']
        assert receipt['checks']['free_cash_flow_value'] == '74071000000'


if __name__ == '__main__':
    main()
