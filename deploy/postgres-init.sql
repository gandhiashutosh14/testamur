-- Runs once, when the demo's Postgres volume is created. The application role connects for every tenant session:
-- not the schema owner and not BYPASSRLS, so row-level security (installed by the migrations) applies to it.
-- A local demo's password; a deployment sets its own.
CREATE ROLE testamur_app LOGIN PASSWORD 'testamur_app' NOSUPERUSER NOBYPASSRLS;
