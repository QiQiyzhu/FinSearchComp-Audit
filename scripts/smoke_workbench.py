"""Real HTTP smoke, always demo-only; does not call a paid provider."""
import argparse
import json
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8090')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    def request(path, data=None):
        payload = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(base + path, payload, {'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)
    assert request('/api/health')
    config = request('/api/config')
    assert 'DEEPSEEK_API_KEY' not in json.dumps(config)
    run = request('/api/research', {'question': '分析微软年度营收、利润与现金流', 'ticker': 'MSFT', 'as_of': '2024-11-01', 'mode': 'demo'})
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        run = request('/api/research/' + run['id'])
        if run['status'] in ('completed', 'failed'):
            break
        time.sleep(.2)
    assert run['status'] == 'completed', run.get('error')
    report = run['result']
    assert report['mode'] == 'demo'
    assert not report['model']['used']
    assert report['metrics'] and report['evidence']
    assert all(item['published_at'] <= report['as_of'] for item in report['evidence'])
    request('/api/research/' + run['id'] + '/export?format=json')
    terminal = request('/api/terminal/research', {'question': '查询微软年度营业利润率和资产负债情况', 'ticker': 'MSFT', 'as_of': '2024-11-02', 'fiscal_year': 2024, 'metric_ids': ['operating_margin', 'assets'], 'mode': 'demo'})
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        terminal = request('/api/research/' + terminal['id'])
        if terminal['status'] in ('completed', 'failed'):
            break
        time.sleep(.2)
    assert terminal['status'] == 'completed', terminal.get('error')
    assert terminal['result']['coverage'] == {'available': 2, 'total': 2}
    assert all(answer['gate']['passed'] for answer in terminal['result']['answers'])
    request('/workbench/data/finance_cube.json')
    print(json.dumps({'status': 'passed', 'mode': 'demo', 'legacy_metrics': len(report['metrics']), 'terminal_verified_answers': 2, 'paid_calls': 0}))


if __name__ == '__main__':
    main()
