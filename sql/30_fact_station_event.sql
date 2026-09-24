-- EVENT: a bike leaving (undock, -1) or arriving (dock, +1) at a station.
-- This is the grain station balance lives at: one ride = up to two station events.
-- Dockless ends produce no event; that is the coverage gap reported as M5.
create or replace table model.fact_station_event as
with events as (
    select ride_id, start_station_id as station_id, started_at as event_ts,
           'undock' as event_type, -1 as bike_delta, rideable_type, member_casual
    from model.fact_ride where counts_for_flow and start_station_id is not null
    union all
    select ride_id, end_station_id, ended_at, 'dock', +1, rideable_type, member_casual
    from model.fact_ride where counts_for_flow and end_station_id is not null
)
select e.*,
       cast(e.event_ts - interval {op_day_start_hour} hour as date) as op_date,
       hour(e.event_ts)                                            as event_hour
from events e;
