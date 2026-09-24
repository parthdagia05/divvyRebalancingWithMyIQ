-- Station x operational date x clock hour flow. Feeds peak-window analysis.
create or replace table model.station_hour as
select station_id, op_date, event_hour,
       count(*) filter (where event_type = 'undock') as departures,
       count(*) filter (where event_type = 'dock')   as arrivals,
       sum(bike_delta)                                as net_flow
from model.fact_station_event
where op_date between date '{window_start}' and date '{window_end}'
group by all;
