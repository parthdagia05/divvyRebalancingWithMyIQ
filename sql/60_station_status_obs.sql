-- OBSERVED OUTCOME from live GBFS polling: was the station empty or full?
-- Used only to corroborate the trip-based model (no status history exists).
create or replace table model.station_status_obs as
select
    s.station_id,
    st.snapshot,
    to_timestamp(st.last_reported) as last_reported,
    st.num_bikes_available, st.num_docks_available,
    st.num_bikes_available = 0 and st.is_renting = 1   as is_empty,
    st.num_docks_available = 0 and st.is_returning = 1 as is_full
from raw.gbfs_station_status st
join model.dim_station s on s.gbfs_station_id = st.station_id
where s.station_kind = 'docking_station'
  and st.is_installed = 1
  and st.feed_last_updated - st.last_reported <= 3600;   -- rule G04
