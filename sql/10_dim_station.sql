-- ENTITY: station. One row per station id that appears in trips.
-- Trip ids join to GBFS short_name (verified in docs/01_source_map.md).
-- station_kind decides whether the station is in scope for the capacity KPI.
create or replace table model.dim_station as
with trip_ids as (
    select station_id, station_name, count(*) as n
    from (
        select start_station_id as station_id, start_station_name as station_name from staging.trips
        union all
        select end_station_id, end_station_name from staging.trips
    )
    where station_id is not null
    group by all
),
trip_station as (
    select station_id,
           arg_max(station_name, n) as trip_name,        -- most common spelling
           count(distinct station_name) as name_variants,
           sum(n) as events
    from trip_ids group by 1
)
select
    t.station_id,
    coalesce(g.name, t.trip_name)          as station_name,
    t.name_variants,
    g.gbfs_station_id,
    g.capacity,
    g.lat, g.lon,
    case
        when regexp_matches(t.trip_name, '{staff_pattern}')          then 'staff_facility'
        when regexp_matches(t.trip_name, '(?i)public rack|corral')   then 'public_rack'
        when g.short_name is null                                    then 'not_in_gbfs'
        when g.station_kind = 'docking_station' and g.capacity > 0   then 'docking_station'
        else 'public_rack'
    end                                    as station_kind,
    t.events                               as trip_events_in_window
from trip_station t
left join staging.gbfs_stations g on g.short_name = t.station_id;
