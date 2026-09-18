"""Exercise the real DOF HTTP validation/cache methods with an isolated cache and transport."""
import copy
import sys
import types
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

import check_dof as base

# Odoo's fields and requests transport are not installed in the portable environment.
fields = base.base.legacy.odoo.fields
fields.Char = fields.Json = lambda *args, **kwargs: None
fields.Date.__init__ = fields.Datetime.__init__ = lambda self, *args, **kwargs: None
request_module = types.ModuleType('requests')
request_module.RequestException = type('RequestException', (Exception,), {})
request_module.get = Mock()
sys.modules['requests'] = request_module
rates = base.base.load('dof_rates')


class Cache(rates.DofMonth):
    def __init__(self):
        self.rows = []; self.sudo_calls = 0
    def sudo(self):
        self.sudo_calls += 1
        return self
    def search(self, domain, order):
        return sorted([row for row in self.rows if row.month in domain[0][2]], key=lambda row: row.fetched_at, reverse=True)
    def create(self, values):
        values = copy.deepcopy(values); values['through'] = date.fromisoformat(values['through'])
        self.rows.append(types.SimpleNamespace(**values))


class TestCache(unittest.TestCase):
    def setUp(self):
        request_module.get.reset_mock(side_effect=True, return_value=True)
        request_module.get.return_value = types.SimpleNamespace(status_code=200, content=b'{}',
            raise_for_status=lambda: None, json=lambda: base.payload([('01-09-2026', '19'), ('02-09-2026', '21')]))

    def test_http_fixed_official_origin_tls_defaults_timeout_and_cache_reuse(self):
        cache = Cache()
        args = (date(2026, 9, 1), date(2026, 9, 16), date(2026, 9, 16))
        first = cache._monthly_rates(*args); second = cache._monthly_rates(*args)
        self.assertEqual(first, second); self.assertEqual(request_module.get.call_count, 1)
        call = request_module.get.call_args
        self.assertEqual(call.args[0], 'https://sidof.segob.gob.mx/dof/sidof/indicadores/158/01-09-2026/16-09-2026')
        self.assertEqual(call.kwargs['timeout'], (3.05, 12)); self.assertFalse(call.kwargs['allow_redirects'])
        self.assertNotIn('verify', call.kwargs)
        self.assertEqual(first['2026-09']['average'], '20')

    def test_http_error_is_not_zero_and_is_throttled(self):
        request_module.get.side_effect = request_module.RequestException('timeout')
        cache = Cache(); args = (date(2026, 9, 1), date(2026, 9, 16), date(2026, 9, 16))
        values = cache._monthly_rates(*args)
        self.assertFalse(values['2026-09']['available']); self.assertIsNone(values['2026-09']['average'])
        self.assertEqual(cache._monthly_rates(*args), values)
        self.assertEqual(request_module.get.call_count, 1)

    def test_open_month_refreshes_after_six_hours_and_when_month_closes(self):
        cache = Cache(); args = (date(2026, 9, 1), date(2026, 9, 16), date(2026, 9, 16))
        cache._monthly_rates(*args)
        with patch.object(fields.Datetime, 'now', return_value=datetime(2026, 9, 17, 1)):
            cache._monthly_rates(*args)
        self.assertEqual(request_module.get.call_count, 2)
        # A last-day provisional snapshot must be fetched again when the month closes.
        cache.rows[-1].through = date(2026, 9, 30)
        cache.rows[-1].fetched_at = datetime(2026, 9, 30, 12)
        with patch.object(fields.Datetime, 'now', return_value=datetime(2026, 10, 1, 12)):
            result = cache._monthly_rates(date(2026, 9, 1), date(2026, 9, 30), date(2026, 10, 1))
        self.assertEqual(request_module.get.call_count, 3)
        self.assertFalse(result['2026-09']['provisional'])

    def test_malformed_json_and_redirects_fail_without_following_external_origins(self):
        response = request_module.get.return_value
        response.status_code = 302
        with self.assertRaises(base.math.DofRateError):
            Cache()._fetch(date(2026, 9, 1), date(2026, 9, 16), date(2026, 9, 16))
        response.status_code = 200; response.json = Mock(side_effect=ValueError('not JSON'))
        with self.assertRaises(base.math.DofRateError):
            Cache()._fetch(date(2026, 9, 1), date(2026, 9, 16), date(2026, 9, 16))


if __name__ == '__main__':
    unittest.main(verbosity=2)
