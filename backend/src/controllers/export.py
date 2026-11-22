import csv
import io

from fastapi import HTTPException, Response


def safe(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def export(group, latest=None, signature=None):
    """下载与页面同一份现行建议：采购组上锁定的结果。

    附上批次状态、未纳入建议的补报，以及已签单按当时数据与包数的留档。
    """
    if not group.result:
        raise HTTPException(409, '现行建议尚未完成计算')
    result = group.result
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['采购单', '规格', '备料起始周', '单据标题'])
    writer.writerow([group.id, safe(group.sleeve_code), group.start_week.isoformat(), safe(group.title)])
    writer.writerow(['现行建议批次', group.applied_revision or '—', '最新批次状态',
                     latest.status if latest else '—'])
    writer.writerow(['建议状态', group.status])
    if group.note:
        writer.writerow(['计算说明', safe(group.note)])
    writer.writerow(['结论', safe(result['summary'])])
    for key, value in result['metrics'].items():
        writer.writerow([safe(key), value])
    if group.warnings:
        writer.writerow([])
        writer.writerow(['未纳入本建议的补报'])
        writer.writerows([[safe(text)] for text in group.warnings])

    if signature:
        writer.writerow([])
        writer.writerow(['已签单留档批次', signature['revision'], '当时包数', signature['packs']])
        writer.writerow(['签核意见', safe(signature['note'])])
        writer.writerow(['签单结论', safe(signature['result'].get('summary', ''))])
    rows = result.get('rows') or []
    if rows:
        writer.writerow([])
        columns = ['首个预测周', '训练周数', '实际领用', 'SBA', '移动平均', 'SBA 误差', '移动平均误差']
        writer.writerow(columns)
        writer.writerows([safe(row.get(key, '')) for key in columns] for row in rows)
    return Response(content='﻿' + stream.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="sleeve-{group.id}.csv"'})
