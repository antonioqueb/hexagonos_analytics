"""Pure calendar and cohort rules, also used by portable regression tests."""
from calendar import monthrange
from datetime import timedelta
from statistics import median


def unique_assignment(ids):
    keys = set(ids)
    return next(iter(keys)) if len(keys) == 1 else False


def shift_month(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    return day.replace(year=year, month=month + 1,
                       day=min(day.day, monthrange(year, month + 1)[1]))


def comparison(start, end, mode):
    if mode == 'previous_period':
        return start - timedelta(days=(end - start).days + 1), start - timedelta(days=1)
    offset = -12 if mode == 'previous_year' else -1
    # Preserve whole-month intent (e.g. Feb 1..28 -> Jan 1..31).
    previous_end = shift_month(end, offset)
    if end.day == monthrange(end.year, end.month)[1]:
        previous_end = previous_end.replace(day=monthrange(previous_end.year, previous_end.month)[1])
    return shift_month(start, offset), previous_end


def delta(current, previous):
    return dict(current=current, previous=previous, difference=current - previous,
                percent=100 * (current - previous) / abs(previous) if previous else None,
                base_label='Sin base anterior' if not previous else '')


def months_between(start, end):
    cursor = start.replace(day=1)
    result = []
    while cursor <= end:
        result.append(cursor.strftime('%Y-%m'))
        cursor = shift_month(cursor, 1)
    return result


def customer_status(days, start, end, inactivity, cadence_factor):
    days = sorted(set(d for d in days if d <= end))
    before = [d for d in days if d < start]
    current = [d for d in days if d >= start]
    gaps = [(b - a).days for a, b in zip(before, before[1:])]
    cadence = median(gaps) if len(gaps) >= 3 else None
    threshold = max(inactivity, round(cadence * cadence_factor)) if cadence else inactivity
    if current:
        status = 'new' if not before else ('reactivated' if (current[0] - before[-1]).days >= threshold else 'recurring')
    else:
        status = 'lost' if days and (end - days[-1]).days >= threshold else 'no_purchase'
    return dict(status=status, last_purchase=str(days[-1]) if days else None,
                days_inactive=(end - days[-1]).days if days else None,
                threshold=threshold, cadence=cadence)
