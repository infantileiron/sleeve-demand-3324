from datetime import date, timedelta
from math import ceil
from statistics import mean

WEEK = timedelta(days=7)


def croston_sba(values, alpha):
    first = next((i for i, units in enumerate(values) if units > 0), None)
    if first is None:
        return 0.0
    size, spacing, last = values[first], first + 1, first
    for i in range(first + 1, len(values)):
        if values[i] > 0:
            size += alpha * (values[i] - size)
            spacing += alpha * (i - last - spacing)
            last = i
    return (1 - alpha / 2) * size / spacing


def _by_week(records):
    return {date.fromisoformat(row['week']): row for row in (records or [])}


def _observed(record):
    return record is not None and record.get('state') == 'observed'


def _walk_back(by_week, week):
    """从 ``week`` 向前收集已核周领用量。

    缺传或未登记的周是未知，不是零领用，向前收集到此为止。返回最旧在前，
    且只含严格早于 ``week`` 的周。
    """
    units = []
    while _observed(by_week.get(week)):
        units.append(by_week[week]['units'])
        week -= WEEK
    return units[::-1]


def analyze(snapshot, config, actuals=None):
    """回测一个采购组。

    ``snapshot`` 是排入时锁定的训练账本（首次登记值），每个回测起点只用该
    起点之前已知的记录。``actuals`` 是补报后的实领账本，用来给评估窗口打分；
    缺省时与 ``snapshot`` 相同。
    """
    training = _by_week(snapshot)
    current = _by_week(actuals or snapshot)
    if config.get('start_week'):
        start = date.fromisoformat(config['start_week'])
    elif training:
        start = max(training) + WEEK
    else:
        raise ValueError('请先录入该规格的领用周')
    history = {week: row for week, row in training.items() if week < start}
    scored = {week: row for week, row in current.items() if week < start}
    if not history:
        raise ValueError('备料起始周之前没有领用记录')
    # 备料起始周的前一周必须已核（按补报后的当前值判断），缺传或没登记都
    # 不能当作零领用。回测训练仍只用各起点当时已知的首次登记值。
    latest = start - WEEK
    if latest not in history and latest not in scored:
        raise ValueError('备料起始周前一周没有登记，先补齐再计算')
    if not _observed(scored.get(latest)):
        raise ValueError('最近一周缺传，补齐后才能预测')
    earliest = min(history)

    grid, week = [], earliest
    while week <= latest:
        grid.append(week)
        week += WEEK

    horizon = int(config['lead_weeks'])
    width = int(config['baseline_weeks'])
    minimum_train = max(12, width)

    rows, errors = [], {'SBA': [], '移动平均': []}
    for index in range(len(grid) - horizon + 1):
        origin_week = grid[index]
        window = grid[index:index + horizon]
        # 只有评估窗口的实领全部已核（补报后）才可计误差。
        if not all(_observed(scored.get(week)) for week in window):
            continue
        train = _walk_back(history, origin_week - WEEK)
        # 每个起点只使用该起点之前已知的记录；训练不足则跳过该起点。
        if len(train) < minimum_train or sum(units > 0 for units in train) < 2:
            continue
        actual = sum(scored[week]['units'] for week in window)
        sba = croston_sba(train, config['alpha']) * horizon
        naive = mean(train[-width:]) * horizon
        errors['SBA'].append(sba - actual)
        errors['移动平均'].append(naive - actual)
        rows.append({'首个预测周': origin_week.isoformat(), '训练周数': len(train), '实际领用': actual,
                     'SBA': round(sba, 2), '移动平均': round(naive, 2),
                     'SBA 误差': round(sba - actual, 2), '移动平均误差': round(naive - actual, 2)})

    if len(rows) < 6:
        raise ValueError('可回测起点不足 6 个。至少需 6 个完整交货周期，且各起点训练窗口至少有 2 周正领用量')

    mae = {key: mean(map(abs, values)) for key, values in errors.items()}
    # min 保持插入顺序，完全打平时落到 SBA。
    winner = min(mae, key=mae.get)
    # 点预测用补报后的最新实领（这是“现在”已知的记录）；回测训练仍只用各
    # 起点当时已知的首次登记值。
    full_train = _walk_back(scored, latest)
    if len(full_train) < minimum_train:
        raise ValueError('最新连续实领不足，先补齐缺传周再计算')
    weekly = croston_sba(full_train, config['alpha']) if winner == 'SBA' else mean(full_train[-width:])
    total = horizon * weekly
    packs = ceil(total / config['pack_size'])
    return {
        'summary': f'本次采用{winner}。从 {start.isoformat()} 起覆盖 {horizon} 周，预计领用 {total:.2f} 张，'
                   f'按 {config["pack_size"]} 张/包备 {packs} 包。',
        'method': winner,
        'start_week': start.isoformat(),
        'pack_size': config['pack_size'],
        'lead_weeks': horizon,
        'alpha': config['alpha'],
        'baseline_weeks': width,
        'metrics': {'连续周数': len(full_train), '周均预测（张）': round(weekly, 3), '建议备料（包）': packs,
                    'SBA MAE': round(mae['SBA'], 3), '移动平均 MAE': round(mae['移动平均'], 3),
                    '可回测起点': len(rows)},
        'rows': rows[::-1],
        'notes': ['每个回测起点只使用该周之前已登记（已核）的领用周，缺传周不当作零领用，也不使用未来记录。',
                  '误差按补报后的实领量计算；历史相邻评估窗口可能重叠。',
                  '本表使用点预测，备料量未扣现存库存或在途。'],
    }
