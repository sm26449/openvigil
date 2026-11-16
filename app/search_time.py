"""Intervale calendaristice locale pentru cautare; UTC epoch la granita cu stocarea."""
import re
import time
from datetime import datetime, timedelta


def bounds(days=30, now=None):
    today = datetime.fromtimestamp(time.time() if now is None else now).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return (today - timedelta(days=max(1, int(days)) - 1)).timestamp(), (today + timedelta(days=1)).timestamp()


def parse_period(text, days=30, now=None):
    """Scoate perioada din text; ieri/alaltaieri sunt zile intregi, nu ultimele 48/72h."""
    start, end = bounds(days, now)
    today = datetime.fromtimestamp(time.time() if now is None else now).replace(
        hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=today.weekday())
    month = today.replace(day=1)
    previous_month = (month - timedelta(days=1)).replace(day=1)
    periods = {
        'alaltaieri': (today-timedelta(days=2), today-timedelta(days=1), 3),
        'day before yesterday': (today-timedelta(days=2), today-timedelta(days=1), 3),
        'ieri': (today-timedelta(days=1), today, 2),
        'yesterday': (today-timedelta(days=1), today, 2),
        'azi': (today, today+timedelta(days=1), 1),
        'astazi': (today, today+timedelta(days=1), 1),
        'today': (today, today+timedelta(days=1), 1),
        'saptamana asta': (week, today+timedelta(days=1), 7),
        'this week': (week, today+timedelta(days=1), 7),
        'saptamana trecuta': (week-timedelta(days=7), week, 14),
        'last week': (week-timedelta(days=7), week, 14),
        'luna asta': (month, today+timedelta(days=1), 31),
        'this month': (month, today+timedelta(days=1), 31),
        'luna trecuta': (previous_month, month, 62),
        'last month': (previous_month, month, 62),
    }
    for word, n in [('saptamana', 7), ('past week', 7), ('week', 7),
                    ('luna', 31), ('past month', 31), ('month', 31)]:
        periods[word] = (today-timedelta(days=n-1), today+timedelta(days=1), n)
    pattern = r'\b(?:' + '|'.join(re.escape(x) for x in sorted(periods, key=len, reverse=True)) + r')\b'
    matches = list(re.finditer(pattern, text))
    date_match = re.search(r'\b\d{4}-\d{2}-\d{2}\b', text)
    if len(matches) + bool(date_match) > 1:
        raise ValueError('period')
    explicit = bool(matches or date_match)
    if matches:
        m = matches[0]
        a, b, days = periods[m.group()]
        start, end = a.timestamp(), b.timestamp()
        text = text[:m.start()] + ' ' + text[m.end():]
    elif date_match:
        a = datetime.strptime(date_match.group(), '%Y-%m-%d')
        start, end = a.timestamp(), (a+timedelta(days=1)).timestamp()
        prefix = re.sub(r'\b(?:pe|on)\s*$', '', text[:date_match.start()])
        text = prefix + ' ' + text[date_match.end():]
    m = re.search(r'\b(?:intre|between)\s+(\d{1,2})(?::(\d{2}))?\s+(?:si|and)\s+(\d{1,2})(?::(\d{2}))?\b', text)
    hours = None
    if m:
        h1, m1, h2, m2 = (int(x or 0) for x in m.groups())
        if h1 > 23 or h2 > 24 or m1 > 59 or m2 > 59 or (h2 == 24 and m2):
            raise ValueError('hours')
        hours = [h1*60+m1, h2*60+m2]
        if hours[0] == hours[1]:
            raise ValueError('hours')
        text = text[:m.start()] + ' ' + text[m.end():]
        if hours[0] > hours[1]:
            # Noaptea apartine zilei in care incepe: ieri 22–02 se termina azi la 02.
            start = datetime.fromtimestamp(start).replace(hour=h1,minute=m1).timestamp()
            end = datetime.fromtimestamp(end).replace(hour=h2,minute=m2).timestamp()
    if re.search(r'\b(?:intre|between)\b|\b(?:dupa|inainte de|after|before)\s+\d', text):
        raise ValueError('hours')
    return text, {'days': days, 'since': start, 'until': end, 'hours': hours, 'explicit_period': explicit}


def in_hours(hms, hours):
    if hours is None:
        return True
    h, m = (int(x) for x in hms.split(':')[:2])
    minute = h*60+m
    a, b = hours
    return a <= minute < b if a < b else minute >= a or minute < b
