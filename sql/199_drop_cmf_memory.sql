-- Removes ONLY the memory schema (never touches cmf_ops).  PostgreSQL.  (SQLite: MemoryStore.init(reset=True) drops the same tables.)
DROP SCHEMA IF EXISTS cmf_memory CASCADE;
