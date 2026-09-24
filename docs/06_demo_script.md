# 06 — Demo script (3–5 minutes)

Screens to open beforehand: the repo README on GitHub, a terminal in the repo,
and `notebooks/walkthrough.ipynb` rendered on GitHub.

---

### 0:00 – 0:30 · The client problem (README §1–3)

> "Divvy riders fail in two ways: no bike to take, or no dock to return to. Ops
> moves bikes with trucks, but it has no trustworthy measure of how often
> stations fail on their own, or where the trucks should go. My KPI is the share
> of station-days that needed rebalancing."

### 0:30 – 1:15 · Sources and the join (docs/01)

> "There are two operator systems. The trip history is a monthly file export, and
> it's the only place history lives. GBFS is a live API, and it's the only place
> capacity lives. The first trap is that the trip `station_id` does **not** match
> GBFS `station_id`. It matches `short_name`. Most of the ids that don't match
> are public racks, which have no real capacity."

### 1:15 – 2:00 · Run the pipeline live (terminal)

```bash
python -m divvy run
```

Point at the log as it scrolls:

> "The downloads are skipped because the sha256 checksums match, so this is a
> rerun. 2.5M rows are reconciled from CSV to warehouse. Then 20 validation
> rules. Look at F01: Divvy says rides under 60 seconds are removed, and 63,000
> of them are still here. I flag them and exclude them from station flow. I
> don't delete them."

Optionally, show fail-closed:

> "If a whole day were missing, V04 would block the run, it would exit with code
> 2, and last month's outputs would stay in place. There's a test for exactly
> that."

### 2:00 – 3:45 · **The judgement call** (notebook §4, the station-day chart)

> "The client really asked about **interventions**, the truck moves. But no
> published source contains a single rebalancing event. I had three options:
>
> 1. Use net flow per day. It's simple, but it's wrong: a station that loses 15
>    bikes by 9am and gets them back by 7pm nets to zero, yet it was empty all
>    morning.
> 2. Invent a threshold, like 10 bikes. That can't be defended: 10 bikes is a
>    lot for a 7-dock station and very little for a 39-dock one.
> 3. **Swing against capacity.** Track the running gain through the day. If the
>    gap between the highest and lowest point is bigger than the number of docks,
>    then *no* starting stock could have survived the day. An intervention
>    **had** to happen.
>
> I chose option 3 because it's provable from the data we have, and I label it
> honestly as a **lower bound**. This chart is DuSable Lake Shore & North on a
> Saturday. A 39-dock station gained 154 bikes. That's only possible with valet
> overflow, so the model is detecting an intervention nobody logged.
>
> The second half of that call is scope. I restricted the KPI to docking
> stations, because capacity means nothing at a public rack. The 38% of rides I
> therefore can't see is not dropped. It's metric M5, so leadership knows how
> much of the picture this KPI covers."

### 3:45 – 4:30 · The decision (README §4)

> "Here are the results. About 1.6% of station-days fail, which is at least 115
> bikes a day to move. The top 50 stations account for 93% of that work, and 14
> of the top 15 are *fill* stations on the lakefront and in the Loop. So the
> recommendation is a fixed bike-removal route weighted to weekends, not more
> general truck capacity. The live GBFS snapshots back this up: the chronic
> stations are empty or full far more often than the rest."

### 4:30 – 5:00 · What I'd ask the client next (docs/05)

> "The unknowns that would change this: the actual truck logs, historical
> capacity, and riders who gave up at an empty station. Those are the three
> questions I'd take to Divvy Ops."
