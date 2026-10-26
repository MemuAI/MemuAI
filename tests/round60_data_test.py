from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import duckdb
from app.memudb import Workspace, DBError, _params
from app.memudb_duckdb import DuckWorkspace

class DataRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.owner = ('round60', 'memudb')
        path = self.root / 'data.sqlite'
        with sqlite3.connect(path) as conn:
            conn.executescript('CREATE TABLE items(id INTEGER PRIMARY KEY, value REAL); INSERT INTO items VALUES(1, 10)')
        conn.close()
        self.sqlite = Workspace()
        self.sqlite_id = self.sqlite.open(str(path), self.owner)['id']
        self.sqlite.configure(self.sqlite_id, self.owner, False, confirmed=True)
        path = self.root / 'data.duckdb'
        conn = duckdb.connect(str(path))
        conn.execute('CREATE TABLE items(id INTEGER PRIMARY KEY, value DOUBLE); INSERT INTO items VALUES(1, 10)')
        conn.close()
        self.duck = DuckWorkspace()
        self.duck_id = self.duck.open(str(path), self.owner)['id']
        self.duck.configure(self.duck_id, self.owner, False, confirmed=True)

    def tearDown(self):
        self.sqlite.close_all()
        self.duck.close_all()
        self.temp.cleanup()

    def test_parameters_reject_nonfinite_values_before_binding(self):
        for value in (float('inf'), float('-inf'), float('nan')):
            with self.subTest(value=value), self.assertRaises(DBError):
                _params([value])

    def test_sqlite_out_of_range_integer_is_a_validation_error(self):
        with self.assertRaises(DBError):
            self.sqlite.query(self.sqlite_id, self.owner, 'SELECT ?', [1 << 80])

    def test_preview_parameters_are_sealed_against_caller_mutation(self):
        parameters = [23]
        proposal = self.sqlite.preview(self.sqlite_id, self.owner, [{'sql': 'UPDATE items SET value=?', 'parameters': parameters}])
        parameters[0] = 99
        self.sqlite.apply(self.sqlite_id, self.owner, proposal['preview_id'], confirmed=True)
        self.assertEqual(self.sqlite.query(self.sqlite_id, self.owner, 'SELECT value FROM items')['rows'], [[23.0]])

    def test_exports_encode_nonfinite_values_as_valid_json(self):
        for workspace, session in [(self.sqlite, self.sqlite_id), (self.duck, self.duck_id)]:
            with self.subTest(engine=type(workspace).__name__):
                sql = "UPDATE items SET value=1e999"
                proposal = workspace.preview(session, self.owner, [{'sql': sql}])
                workspace.apply(session, self.owner, proposal['preview_id'], confirmed=True)
                data = workspace.export(session, self.owner, table='items', format='json')['text']
                parsed = json.loads(data, parse_constant=lambda value: self.fail('Nonstandard JSON token: ' + value))
                self.assertEqual(parsed[0]['value'], 'inf')

    def test_duck_nested_nonfinite_results_have_a_json_transport(self):
        result = self.duck.query(self.duck_id, self.owner, "SELECT {'nested': [1e999::DOUBLE]} AS data")
        json.dumps(result, allow_nan=False)
        self.assertEqual(result['rows'][0][0]['nested'], ['inf'])

    def test_duck_table_builder_accepts_the_types_offered_by_the_ui(self):
        for kind in ['BIGINT', 'DOUBLE', 'VARCHAR', 'BOOLEAN', 'TIMESTAMP', 'JSON']:
            with self.subTest(kind=kind):
                proposal = self.duck.table_preview(self.duck_id, self.owner, 'new_' + kind.lower(), [{'name': 'value', 'type': kind}])
                self.assertTrue(proposal['schema_changes'])

    def test_duck_schema_preview_includes_constraints_and_indexes(self):
        for sql in ['ALTER TABLE items ALTER value SET NOT NULL', 'CREATE INDEX idx_value ON items(value)', 'CREATE SEQUENCE seq_items']:
            with self.subTest(sql=sql):
                proposal = self.duck.preview(self.duck_id, self.owner, [{'sql': sql}])
                self.assertTrue(proposal['schema_changes'], sql)

    def test_duck_long_operation_id_can_be_cancelled(self):
        operation_id = 'long-' + 'x' * 120
        with ThreadPoolExecutor() as pool:
            future = pool.submit(self.duck.query, self.duck_id, self.owner,
                                 'SELECT sum(sin(i)) FROM range(1000000000) r(i)', opid=operation_id)
            deadline = time.monotonic() + 3
            while not self.duck.operations and time.monotonic() < deadline:
                time.sleep(.01)
            response = self.duck.cancel(self.duck_id, self.owner, operation_id)
            self.assertTrue(response['cancelled'])
            with self.assertRaises(DBError):
                future.result(timeout=3)

    def test_duck_guard_change_interrupts_an_inflight_query(self):
        changed = threading.Event()
        def guard():
            if changed.is_set():
                raise DBError('Profile changed')
        with ThreadPoolExecutor() as pool:
            future = pool.submit(self.duck.query, self.duck_id, self.owner,
                                 'SELECT sum(sin(i)) FROM range(1000000000) r(i)', guard=guard)
            deadline = time.monotonic() + 3
            while not self.duck.operations and time.monotonic() < deadline:
                time.sleep(.01)
            changed.set()
            try:
                with self.assertRaises(DBError):
                    future.result(timeout=2)
            finally:
                self.duck.cancel(self.duck_id, self.owner, next(iter(self.duck.operations), ('', ''))[1])

    def test_unclosed_csv_quote_is_rejected_before_import(self):
        with self.assertRaises((DBError, ValueError)):
            self.sqlite.csv_preview(self.sqlite_id, self.owner, 'items', 'id,value\n2,"unclosed')

    def test_result_limit_counts_utf8_bytes(self):
        with patch('app.memudb.MAX_RESULT', 100):
            result = self.duck.query(self.duck_id, self.owner, "SELECT repeat('中', 50)")
        self.assertTrue(result['truncated'])
        self.assertEqual(result['rows'], [])

    def test_duck_preview_bounds_the_combined_statement_results(self):
        with patch('app.memudb_duckdb.MAX_RESULT', 20000):
            proposal = self.duck.preview(self.duck_id, self.owner, [{'sql': "SELECT repeat('中', 5000)"}] * 2)
        self.assertTrue(proposal['statements'][1]['truncated'])
        self.assertEqual(proposal['statements'][1]['rows'], [])

    def test_json_export_stops_fetching_at_the_byte_limit(self):
        session = self.sqlite.get(self.sqlite_id, self.owner)
        seen = []
        session.connection.create_function('seen', 1, lambda value: seen.append(value) or 'x' * 200)
        with sqlite3.connect(session.path) as writer:
            writer.execute('CREATE VIEW bounded AS WITH RECURSIVE numbers(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM numbers WHERE n<1000) SELECT seen(n) AS value FROM numbers')
        writer.close()
        session.connection.execute('PRAGMA trusted_schema=ON')
        with patch('app.memudb.MAX_EXPORT', 100), self.assertRaises(DBError):
            self.sqlite.export(self.sqlite_id, self.owner, table='bounded', format='json')
        self.assertLess(len(seen), 10)

    def test_busy_sqlite_backup_can_be_cancelled_without_leaving_a_partial_file(self):
        session = self.sqlite.get(self.sqlite_id, self.owner)
        blocker = sqlite3.connect(session.path)
        blocker.execute('BEGIN EXCLUSIVE')
        try:
            with ThreadPoolExecutor() as pool:
                future = pool.submit(self.sqlite.backup, self.sqlite_id, self.owner, opid='backup')
                deadline = time.monotonic() + 3
                while not self.sqlite.operations and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(self.sqlite.cancel(self.sqlite_id, self.owner, 'backup')['cancelled'])
                try:
                    with self.assertRaises(DBError):future.result(timeout=4)
                finally:blocker.rollback()
        finally:blocker.close()
        self.assertFalse(list(self.root.glob('.memudb-backups/*.sqlite')))

if __name__ == '__main__':
    unittest.main()
