-- Optional hardening: a least-privilege role for the web app.
--
-- The ETL needs to truncate and reload tables; the agent only ever reads the
-- semantic views. Point the web service's DATABASE_URL at this role and keep the
-- owner credentials for the ETL only.
--
--   psql "$OWNER_DATABASE_URL" -v ro_password="'choose-a-strong-password'" -f sql/readonly_role.sql
--
-- Views run with their owner's privileges, so the role needs no access to
-- reservations_hackathon itself.

create role rm_agent_ro login password :ro_password;

grant usage on schema public to rm_agent_ro;
grant select on
  public.vw_stay_night_base,
  public.vw_posted_stay_night,
  public.vw_segment_stay_night,
  public.load_manifest
to rm_agent_ro;

alter role rm_agent_ro set default_transaction_read_only = on;
alter role rm_agent_ro set statement_timeout = '10s';
