"""Portable catalog regression; uses real addon methods with a synthetic ORM.

Run: python3 hexagonos_analytics/tests/check_filter_options.py
Not an Odoo database or record-rule integration test.
"""
import copy
import importlib.util
import unittest

import check_executive as executive


# Exercise the supplier addon search override, including its real domain filter.
path = executive.ROOT.parent / 'catalogo_productos_proveedores_hexagonos/models/product_product.py'
spec = importlib.util.spec_from_file_location('supplier_catalog', path)
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class Records:
    def __init__(self, rows, denied=False):
        self.rows, self.denied = rows, denied

    def read(self, fields):
        if self.denied:
            raise executive.legacy.AccessError('read denied')
        return [{key: row[key] for key in ['id'] + fields} for row in self.rows]

    def mapped(self, field):
        return [row[field] for row in self.rows]

    def __bool__(self):
        return bool(self.rows)


class Context(dict):
    def __init__(self, context=None):
        super().__init__()
        self.context = context or {}


class CatalogSource:
    def __init__(self, rows):
        self.rows, self.calls = rows, []
        self.env = Context()
        self.denied_search = self.denied_read = False

    def with_context(self, **context):
        result = copy.copy(self)
        result.env = Context(dict(self.env.context, **context))
        result.env.update(self.env)
        return result

    def search(self, domain, offset=0, limit=None, order=None):
        if self.denied_search:
            raise executive.legacy.AccessError('search denied')
        self.calls.append((domain, limit, order, self.env.context))
        def matches(row):
            for key, operator, value in domain:
                actual = row.get(key, False)
                if operator == 'ilike':
                    valid = value.casefold() in str(actual).casefold()
                elif operator == 'in':
                    valid = actual in value
                elif operator == '=':
                    valid = actual == value
                else:
                    raise AssertionError(operator)
                if not valid:
                    return False
            return row.get('visible', True)
        rows = sorted(filter(matches, self.rows), key=lambda row: row['id'])
        return Records(rows[offset:offset + limit if limit else None], self.denied_read)

    def search_read(self, domain, fields, **kwargs):
        return self.search(domain, **kwargs).read(fields)


class LegacyProductSource(catalog.ProductProduct, CatalogSource):
    def search_read(self, *args, **kwargs):
        # Simulate the still-deployed buggy method from the reported traceback.
        raise TypeError("BaseModel._read_format() got an unexpected keyword argument 'specification'")


class TestFilterOptions(unittest.TestCase):
    def setUp(self):
        self.service = executive.Service()
        self.products = LegacyProductSource([
            dict(id=i, display_name=f'Producto {i:02d}', sale_ok=True) for i in range(1, 62)])
        self.service.env['product.product'] = self.products

    def test_initial_product_catalog_works_with_legacy_search_read(self):
        rows = self.service.get_filter_options('product')
        self.assertEqual([row['id'] for row in rows], list(range(1, 51)))
        domain, limit, order, context = self.products.calls[0]
        self.assertEqual((domain, limit, order), ([('sale_ok', '=', True)], 50, 'id'))
        self.assertEqual(context, dict(allowed_company_ids=[1], active_test=False))

    def test_search_and_restoration_of_selected_product(self):
        self.assertEqual(self.service.get_filter_options('product', 'Producto 61'),
                         [dict(id=61, name='Producto 61')])
        rows = self.service.get_filter_options('product', selected=61)
        self.assertEqual(len(rows), 51)
        self.assertEqual(rows[-1], dict(id=61, name='Producto 61'))
        self.assertEqual(len(self.service.get_filter_options('product', selected=1)), 50)

    def test_supplier_filter_still_runs_through_search(self):
        self.products.env.context = {'supplier_filter': 42}
        templates = CatalogSource([{'id': 1, 'supplier_ids': 42, 'purchase_ok': True,
                                    'product_variant_ids.id': 61}])
        self.products.env['product.template'] = templates
        rows = self.service.get_filter_options('product', selected=1)
        self.assertEqual(rows, [dict(id=61, name='Producto 61')])
        self.assertEqual(self.products.calls[0][0][-1], ('id', 'in', [61]))

    def test_invisible_or_denied_records_are_not_exposed(self):
        self.products.rows[-1]['visible'] = False
        self.assertEqual(len(self.service.get_filter_options('product', selected=61)), 50)
        self.products.denied_read = True
        self.assertEqual(self.service.get_filter_options('product'), [])
        self.products.denied_read = False
        self.products.denied_search = True
        self.assertEqual(self.service.get_filter_options('product'), [])

    def test_dashboard_access_check_is_kept(self):
        self.service.env.user.has_group = lambda group: False
        with self.assertRaises(executive.legacy.AccessError):
            self.service.get_filter_options('product')

    def test_full_options_load_with_legacy_product_extension(self):
        self.service.with_context = lambda **context: self.service
        self.service.env.companies = [self.service.env.company]
        for name in ['res.partner', 'product.category', 'res.users', 'stock.warehouse']:
            self.service.env[name] = CatalogSource([])
        self.service.env['res.currency'] = CatalogSource([dict(id=1, name='MXN')])
        options = self.service.get_options()
        self.assertEqual(len(options['products']), 50)
        self.assertNotIn('companies', options)
        self.assertEqual(options['company_id'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
