import json
import time
from urllib.request import Request, urlopen

BASE = 'http://127.0.0.1:19277'

def request(method, path, token=None, body=None):
    headers = {'content-type': 'application/json'}
    if token:
        headers['authorization'] = f'Bearer {token}'
    payload = json.dumps(body).encode() if body is not None else None
    with urlopen(Request(BASE + path, data=payload, headers=headers, method=method), timeout=20) as response:
        return response.status, response.headers, response.read()

status, _, raw = request('POST', '/api/auth/login', body={'email': 'analyst@huaiye.demo', 'password': 'Archive2026!'})
assert status == 200
analyst = json.loads(raw)['token']
status, _, raw = request('GET', '/api/recommendations', analyst)
assert status == 200
groups = json.loads(raw)
group = next(item for item in groups if item['sleeve_code'] == 'SL-MAP' and item['start_week'] == '2026-10-26')
assert group['status'] in {'completed', 'stale'} and group['result']
existing_id = group['id']
status, _, raw = request('POST', '/api/recommendations', analyst, {
    'title': 'SL-MAP 2026-10-26 备料', 'sleeve_code': 'SL-MAP',
    'start_week': '2026-10-26', 'alpha': 0.2, 'baseline_weeks': 8,
})
assert status == 202
queued = json.loads(raw)
assert queued['id'] == existing_id
for _ in range(20):
    status, _, raw = request('GET', f'/api/recommendations/{existing_id}', analyst)
    detail = json.loads(raw)
    if detail['status'] in {'completed', 'stale', 'failed'}:
        break
    time.sleep(0.5)
assert detail['status'] in {'completed', 'stale'} and detail['result']
assert detail['history'] and detail['applied_revision'] <= detail['revision']
status, headers, raw = request('GET', f'/api/recommendations/{existing_id}/export', analyst)
assert status == 200 and 'text/csv' in headers.get_content_type() and '建议备料' in raw.decode('utf-8-sig')
print(json.dumps({
    'health': 'checked separately', 'recommendationId': existing_id,
    'status': detail['status'], 'revision': detail['revision'],
    'appliedRevision': detail['applied_revision'], 'historyRows': len(detail['history']),
    'export': 'csv with recommendation metrics',
}, ensure_ascii=False))
