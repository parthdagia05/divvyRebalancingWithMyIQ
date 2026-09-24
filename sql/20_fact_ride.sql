-- INTERACTION: a ride. Clean, typed, one row per ride_id, with flags carried through.
-- counts_for_flow decides whether the ride moves a bike between docks for balance purposes.
create or replace table model.fact_ride as
select
    ride_id, rideable_type, member_casual,
    started_at, ended_at, duration_s,
    start_station_id, end_station_id,
    f_under_60s, f_over_24h, f_unreturned, f_staff_station,
    -- Sub-60s rides are re-docks or false starts; staff rides are operator moves.
    not (coalesce(f_under_60s, false) or f_staff_station) as counts_for_flow,
    source_month
from staging.trips;
