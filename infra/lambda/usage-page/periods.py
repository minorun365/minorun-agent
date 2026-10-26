"""日本時間の集計期間と、その期間を保持するURLの値。"""
import datetime as dt

JST = dt.timezone(dt.timedelta(hours=9))


def bounds(value, now, since):
    mode, _, anchor = (value or '').partition(':')
    if mode not in ('week', 'month', 'all'):
        mode, anchor = 'day', value
    try:
        start = dt.date.fromisoformat(anchor) if anchor else now.date()
    except (ValueError, TypeError):
        start = now.date()
    start = min(start, now.date())
    start = dt.datetime.combine(start, dt.time(), JST)
    if mode == 'all':
        return mode, since.astimezone(JST), now
    if mode == 'week':
        start -= dt.timedelta(days=start.weekday())
        end = start + dt.timedelta(days=7)
    elif mode == 'month':
        start = start.replace(day=1)
        end = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    else:
        end = start + dt.timedelta(days=1)
    return mode, start, min(end, now)


def key(mode, start, now):
    if mode == 'all':
        return 'all'
    current = bounds(mode + ':' if mode != 'day' else None, now, start)[1]
    anchor = '' if start.date() == current.date() else start.date().isoformat()
    return anchor if mode == 'day' else mode + ':' + anchor


def date_label(value):
    return f'{value.year}/{value.month}/{value.day}（{"月火水木金土日"[value.weekday()]}）'


def label(mode, start, end):
    if mode == 'day':
        return date_label(start)
    last = end - dt.timedelta(microseconds=1) if end > start else start
    return ('全期間 · ' if mode == 'all' else '') + date_label(start) + '〜' + date_label(last)


def shift(mode, start, step):
    if mode == 'month':
        index = start.year * 12 + start.month - 1 + step
        return start.replace(year=index // 12, month=index % 12 + 1, day=1)
    return start + dt.timedelta(days=step * (7 if mode == 'week' else 1))


def short_label(mode, start, end):
    """上部の切り替え欄に出す短い表示。月は「2026年10月」、週は終わりの年を省く。"""
    if mode == 'month':
        return f'{start.year}年{start.month}月'
    if mode == 'week':
        last = end - dt.timedelta(microseconds=1) if end > start else start
        w = "月火水木金土日"
        tail = f'{last.month}/{last.day}（{w[last.weekday()]}）'
        return f'{start.year}/{start.month}/{start.day}（{w[start.weekday()]}）〜{tail}'
    return label(mode, start, end)
