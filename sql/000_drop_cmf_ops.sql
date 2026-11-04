-- Removes ONLY the operational financial schema (never touches other schemas, e.g. a future cmf_memory).
DROP SCHEMA IF EXISTS cmf_ops CASCADE;
