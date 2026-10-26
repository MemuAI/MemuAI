import tempfile
import unittest
from pathlib import Path
import duckdb
from app.memudb_duckdb import DuckWorkspace
from app.memudb import DBError

class DuckDBTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'test.duckdb'
  c=duckdb.connect(str(self.path));c.execute('CREATE TABLE items(id INTEGER PRIMARY KEY, price DECIMAL(10,2))');c.execute('INSERT INTO items VALUES(1,20.5)');c.close()
  self.w=DuckWorkspace();self.owner=('profile','memudb');self.id=self.w.open(str(self.path),self.owner)['id']
 def tearDown(self):self.w.close_all();self.temp.cleanup()
 def test_preview_apply_backup_and_readonly(self):
  with self.assertRaises(DBError):self.w.query(self.id,self.owner,'DELETE FROM items')
  self.w.configure(self.id,self.owner,False,confirmed=True)
  p=self.w.preview(self.id,self.owner,[{'sql':'UPDATE items SET price=30 RETURNING *'}])
  self.assertEqual(p['affected'],1);self.assertEqual(self.w.query(self.id,self.owner,'SELECT price FROM items')['rows'],[['20.50']])
  with self.assertRaises(DBError):self.w.apply(self.id,self.owner,p['preview_id'])
  result=self.w.apply(self.id,self.owner,p['preview_id'],confirmed=True)
  self.assertEqual(self.w.query(self.id,self.owner,'SELECT price FROM items')['rows'],[['30.00']])
  backup=duckdb.connect(result['backup'],read_only=True)
  try:self.assertEqual(str(backup.execute('SELECT price FROM items').fetchone()[0]),'20.50')
  finally:backup.close()
 def test_file_network_extensions_config_and_secret_writes_denied(self):
  self.w.configure(self.id,self.owner,False,confirmed=True)
  for sql in ["SELECT * FROM read_text('C:/Windows/win.ini')","SELECT * FROM read_csv('https://example.com/data.csv')","SET enable_external_access=true","INSTALL httpfs","ATTACH 'other.duckdb'","CREATE SECRET x (TYPE S3, KEY_ID 'abc', SECRET 'def')"]:
   with self.subTest(sql=sql),self.assertRaises(Exception):self.w.preview(self.id,self.owner,[{'sql':sql}])
 def test_profile_ownership_and_failed_transaction_are_preserved(self):
  with self.assertRaises(DBError):self.w.query(self.id,('other','memudb'),'SELECT 1')
  self.w.configure(self.id,self.owner,False,confirmed=True)
  with self.assertRaises(DBError):self.w.preview(self.id,self.owner,[{'sql':'UPDATE items SET price=90'},{'sql':'INSERT INTO items VALUES(1,100)'}])
  self.assertEqual(self.w.query(self.id,self.owner,'SELECT price FROM items')['rows'],[['20.50']])
 def test_schema_browse_and_json_export(self):
  self.assertEqual(self.w.schema(self.id,self.owner)['tables'][0]['columns'][0]['pk'],1)
  self.assertEqual(self.w.browse(self.id,self.owner,'items',search='20.5')['total'],1)
  self.assertIn('20.50',self.w.export(self.id,self.owner,table='items',format='json')['text'])
 def test_exports_preserve_long_values_binary_and_more_than_preview_row_limit(self):
  import json,base64
  self.w.configure(self.id,self.owner,False,confirmed=True)
  p=self.w.preview(self.id,self.owner,[{'sql':"CREATE TABLE exports AS SELECT i, repeat('x',10020) AS text, from_hex('000102FF') AS blob FROM range(1100) t(i)"}])
  self.w.apply(self.id,self.owner,p['preview_id'],confirmed=True)
  data=json.loads(self.w.export(self.id,self.owner,table='exports',format='json')['text'])
  self.assertEqual(len(data),1100);self.assertEqual(len(data[0]['text']),10020)
  self.assertEqual(base64.b64decode(data[0]['blob']['base64']),b'\x00\x01\x02\xff')
 def test_row_builder_uses_engine_dialect_and_confirmed_apply(self):
  self.w.configure(self.id,self.owner,False,confirmed=True)
  proposal=self.w.row_preview(self.id,self.owner,'items',{'price':42},{'id':1})
  self.assertEqual(proposal['affected'],1)
  self.w.apply(self.id,self.owner,proposal['preview_id'],confirmed=True)
  self.assertEqual(self.w.query(self.id,self.owner,'SELECT price FROM items')['rows'],[['42.00']])
  proposal=self.w.table_preview(self.id,self.owner,'example',[{'name':'id','type':'INTEGER','pk':True},{'name':'label','type':'TEXT'}])
  self.w.apply(self.id,self.owner,proposal['preview_id'],confirmed=True)
  self.assertIn('example',[t['name'] for t in self.w.schema(self.id,self.owner)['tables']])
 def test_wal_and_ambiguous_parameters_are_refused_without_writes(self):
  self.w.configure(self.id,self.owner,False,confirmed=True)
  with self.assertRaises(DBError):self.w.preview(self.id,self.owner,[{'sql':'UPDATE items SET price=?; DELETE FROM items','parameters':[90]}])
  wal=Path(str(self.path)+'.wal');wal.write_bytes(b'pending-writer-state')
  try:
   with self.assertRaises(DBError):self.w.preview(self.id,self.owner,[{'sql':'UPDATE items SET price=90'}])
   with self.assertRaises(DBError):self.w.backup(self.id,self.owner)
  finally:wal.unlink()
  self.assertEqual(self.w.query(self.id,self.owner,'SELECT price FROM items')['rows'],[['20.50']])
 def test_multistatement_script_previews_every_statement_then_applies_atomically(self):
  self.w.configure(self.id,self.owner,False,confirmed=True)
  proposal=self.w.preview(self.id,self.owner,[{'sql':'UPDATE items SET price=30; INSERT INTO items VALUES(2,40); SELECT * FROM items ORDER BY id'}])
  self.assertEqual(len(proposal['statements']),3);self.assertEqual(proposal['affected'],2)
  self.assertEqual(self.w.query(self.id,self.owner,'SELECT COUNT(*) FROM items')['rows'],[[1]])
  self.w.apply(self.id,self.owner,proposal['preview_id'],confirmed=True)
  self.assertEqual(self.w.query(self.id,self.owner,'SELECT price FROM items ORDER BY id')['rows'],[['30.00'],['40.00']])

if __name__=='__main__':unittest.main()
