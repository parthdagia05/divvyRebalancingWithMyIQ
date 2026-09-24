-- OUTCOME: can the station survive the day without a truck?
--
-- Walk each station's events in time order and keep a running total of
-- bikes gained (arrivals - departures) since the 04:00 reset.
--   peak   = the largest gain reached (>= 0): docks needed to absorb it
--   trough = the deepest loss reached (<= 0): bikes needed to supply it
--   swing  = peak - trough
-- A station starting the day with s bikes stays open for both renting and
-- returning only if s >= -trough and s + peak <= capacity. That requires
-- swing <= capacity. So swing > capacity means that NO starting stock level
-- could have served the day: an intervention (truck, valet, or over-dock
-- e-bike locking) must have happened, or riders were turned away.
-- excess_moves = swing - capacity is the minimum number of bikes that had to
-- be moved that day (a lower bound).
create or replace table model.station_day as
with running as (
    select station_id, op_date, event_ts, bike_delta,
           sum(bike_delta) over (partition by station_id, op_date
                                 order by event_ts, bike_delta desc, ride_id
                                 rows unbounded preceding) as cum
    from model.fact_station_event
    where op_date between date '{window_start}' and date '{window_end}'
),
daily as (
    select station_id, op_date,
           count(*) filter (where bike_delta = -1) as departures,
           count(*) filter (where bike_delta = +1) as arrivals,
           sum(bike_delta)                        as net_flow,
           greatest(max(cum), 0)                  as peak,
           least(min(cum), 0)                     as trough
    from running group by all
)
select d.*,
       d.peak - d.trough                                   as swing,
       s.capacity,
       s.station_kind,
       s.station_kind = 'docking_station'                  as in_kpi_scope,
       (d.peak - d.trough) > s.capacity                    as needs_rebalance,
       greatest((d.peak - d.trough) - s.capacity, 0)       as excess_moves,
       case when -d.trough > d.peak then 'drain' else 'fill' end as pressure
from daily d
join model.dim_station s using (station_id);
