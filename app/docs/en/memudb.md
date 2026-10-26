---
title: MemuDB
summary: Local SQLite and DuckDB databases with table browsing, SQL, schema and relationships, import and export, backups, and AI proposals with confirmed writes.
---

# MemuDB

Enable **Settings → Modules → MemuDB** and open it from the sidebar. It opens local SQLite (`.sqlite`, `.sqlite3`, `.db`, `.db3`) and DuckDB (`.duckdb`) files. This version does not include PostgreSQL, MySQL or remote server drivers.

## Opening databases and browsing

Choose **Open database** and select a file in the system dialog. **Create database** creates a new file without overwriting an existing one. Up to eight databases can stay open; use the selector at the top. Databases initially open read only. Connections belong to the active profile and module and expire after an hour of inactivity or when the application shuts down.

Select a table or view on the left. Search filters rows, column headers sort, and the bottom controls switch pages. **Schema** shows columns, types, primary keys, constraints, indexes, foreign keys and original SQL definitions. Large values and result sets are limited in previews; export preserves full values within its size limit.

## SQL and changes

The **SQL** tab accepts a query and separate JSON parameters: `[1, "text"]` for `?` or `{"id": 1}` for `:id`. **Run read query** and `Ctrl+Enter` run one statement that cannot write data. **Explain query** runs `EXPLAIN QUERY PLAN`. Queries can be cancelled and also have time limits.

To write, first use **Read only** to allow preparing changes. Choose **Preview changes** to run the SQL on a separate temporary copy. The dialog shows SQL, affected rows, returned data and schema changes. Closing it discards the proposal.

**Continue to confirmation** opens a separate MemuAI approval. On approval, a backup is created in `.memudb-backups` beside the original database, then the sealed SQL transaction is applied atomically. Errors roll back the write. Previews expire after 10 minutes; external database changes require a fresh preview. Approval covers the SQL, not identical outcomes for nondeterministic expressions such as `random()`.

**Create table**, **Add row** and **Edit** also prepare proposals. Editing or deleting a row requires its complete primary key. CSV import accepts matching column headers, up to 500 rows and 1 MB; `.tsv` uses tabs. Use SQL for advanced changes, indexes and views. User SQL cannot attach other files, load extensions, call filesystem functions or manage transactions.

SQL history and saved queries are stored locally in module data. They may include values you wrote directly into SQL. Table results and credentials are not added to history.

## AI assistant

Choose a text model and describe a task: draft SQL, explain a schema, find an error or propose a migration. Pressing **Ask AI** sends the question and open database schema to the selected model. Rows are shared only if you explicitly enable **Share up to 10 rows of the selected table with the model**. The selected provider's usual terms apply.

AI responses never execute SQL. **Open proposal in SQL** copies the proposal to the editor. You run reads; writes follow the same preview and confirmation process. The module command available in regular chat also only opens a proposal.

## Export and backups

Export the selected table as CSV or JSON, or the entire database as SQL. CSV protects values that resemble spreadsheet formulas. Export is limited to 32 MB and 100,000 rows per table. JSON exports binary data as Base64. **Back up** creates a full SQLite file beside the database; every write also creates an automatic backup. Close the original database connection before restoring a backup.

## DuckDB and the assistant panel

Open existing `.duckdb` files alongside SQLite. Creating a new database file in the UI currently creates SQLite; create DuckDB externally before opening it. DuckDB supports the `main` schema, positional `?` parameters, table creation, primary-key row editing, CSV import and confirmed transactions. Change previews run on a temporary copy, with a full file backup before application. Checkpoint and close any external writer first: nonempty WAL files are not copied and changes are refused until they are checkpointed. External file access, network, extensions and SQL secrets are disabled.

DuckDB CSV/JSON exports preserve full values up to 100,000 rows and 32 MB; larger results are explicitly rejected. Use a file backup for a complete copy. SQL export is available for SQLite. The current DuckDB schema view does not show indexes, foreign keys or source definitions. PostgreSQL and MySQL are not connected.

The assistant stays beside your data and remembers up to three recent exchanges in the current dialog. New conversation, switching databases or changing sample-sharing clears the context. Conversations do not persist across module openings. AI SQL is still a proposal, never executed automatically.
