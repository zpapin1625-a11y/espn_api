import os
import requests
from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv

# Load secrets from .env file
load_dotenv()

ESPN_S2  = os.getenv("ESPN_S2")        # your espn_s2 cookie
ESPN_SWID= os.getenv("ESPN_SWID")      # your SWID cookie (with curly braces)

if not ESPN_S2 or not ESPN_SWID:
    raise RuntimeError("Missing ESPN_S2 / ESPN_SWID env vars")

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

app = FastAPI(title="ESPN Fantasy Proxy")

def espn_get(url, params):
    try:
        r = requests.get(
            url,
            params=params,
            cookies={"espn_s2": ESPN_S2, "SWID": ESPN_SWID},
            timeout=20,
        )
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        raise HTTPException(status_code=r.status_code, detail=r.text) from e

@app.get("/healthz")
def health():
    return {"ok": True}

@app.get("/espn/league")
def league(league_id: int, year: int, views: str = "mRoster,mMatchup,mTeam"):
    """
    Example:
    /espn/league?league_id=123456&year=2025&views=mRoster,mMatchup,mTeam
    """
    url = f"{BASE}/seasons/{year}/segments/0/leagues/{league_id}"
    params = [("view", v) for v in views.split(",") if v]
    return espn_get(url, params)

@app.get("/espn/matchups")
def matchups(league_id: int, year: int, week: int):
    """
    Example:
    /espn/matchups?league_id=123456&year=2025&week=1
    """
    url = f"{BASE}/seasons/{year}/segments/0/leagues/{league_id}"
    params = [("view","mMatchup"), ("scoringPeriodId", week)]
    return espn_get(url, params)



from typing import Optional, List, Dict, Any
import time

# -------- tiny in-memory cache --------
CACHE: Dict[str, Any] = {}

def cache_get(key: str, ttl: int) -> Optional[Any]:
    row = CACHE.get(key)
    if not row:
        return None
    ts, data = row
    return data if (time.time() - ts) < ttl else None

def cache_put(key: str, data: Any) -> None:
    CACHE[key] = (time.time(), data)

# ESPN helpers
def league_get(league_id: int, year: int, views: List[str], extra_params: List[tuple] = None) -> dict:
    url = f"{BASE}/seasons/{year}/segments/0/leagues/{league_id}"
    params = [("view", v) for v in views]
    if extra_params:
        params.extend(extra_params)
    return espn_get(url, params)

def find_week_projection(player_obj: dict, week: int) -> Optional[float]:
    """
    ESPN embeds multiple 'stats' entries per player. We try to find
    a week projection: statSourceId==1 (projected) for the scoringPeriodId.
    """
    for s in player_obj.get("stats", []):
        if s.get("scoringPeriodId") == week and s.get("statSourceId") == 1:
            # 'appliedTotal' is the projection already adjusted to your league scoring
            if "appliedTotal" in s and s["appliedTotal"] is not None:
                return float(s["appliedTotal"])
    # fallback: latest projection
    proj = [s for s in player_obj.get("stats", []) if s.get("statSourceId") == 1 and s.get("appliedTotal") is not None]
    if proj:
        return float(sorted(proj, key=lambda x: (x.get("seasonId", 0), x.get("scoringPeriodId", 0)))[-1]["appliedTotal"])
    return None

@app.post("/init")
def init(league_id: int, year: int):
    """
    Fetches team IDs/names + settings once and caches them for 1 hour.
    """
    key = f"init:{league_id}:{year}"
    data = cache_get(key, ttl=3600)
    if not data:
        raw = league_get(league_id, year, ["mTeam", "mSettings"])
        teams = [{"id": t["id"], "name": t.get("name")} for t in raw.get("teams", [])]
        data = {"leagueId": league_id, "year": year, "teams": teams}
        cache_put(key, data)
    return data

@app.get("/team/overview")
def team_overview(league_id: int, year: int, team_id: int):
    """
    Returns trimmed starters & bench for a single team (tiny payload).
    """
    key = f"roster:{league_id}:{year}"
    pack = cache_get(key, ttl=900)
    if not pack:
        raw = league_get(league_id, year, ["mRoster", "mTeam"])
        teams_by_id = {t["id"]: t for t in raw.get("teams", [])}
        pack = {"teams": teams_by_id}
        cache_put(key, pack)

    t = pack["teams"].get(team_id)
    if not t:
        return {"teamId": team_id, "teamName": None, "starters": [], "bench": []}

    entries = (t.get("roster") or {}).get("entries", [])
    def trim(e):
        p = (e.get("playerPoolEntry") or {}).get("player", {})
        return {
            "pid": e.get("playerId"),
            "name": p.get("fullName"),
            "posId": p.get("defaultPositionId"),
            "eligibleSlots": p.get("eligibleSlots"),
            "slotId": e.get("lineupSlotId"),
            "injuryStatus": p.get("injuryStatus"),
        }

    starters = [trim(e) for e in entries if e.get("lineupSlotId") not in (20, 21)]  # 20=BN, 21=IR (common)
    bench    = [trim(e) for e in entries if e.get("lineupSlotId") in (20, )]

    return {"teamId": team_id, "teamName": t.get("name"), "starters": starters, "bench": bench}


@app.get("/matchup/summary")
def matchup_summary(league_id: int, year: int, team_id: int, week: int):
    """
    Returns your opponent for the week and compact team names/ids.
    """
    raw = league_get(league_id, year, ["mTeam", "mMatchup"], extra_params=[("scoringPeriodId", week)])
    teams = {t["id"]: t.get("name") for t in raw.get("teams", [])}
    opponent_id = None
    for game in raw.get("schedule", []):
        home = (game.get("home") or {}).get("teamId")
        away = (game.get("away") or {}).get("teamId")
        if home == team_id:
            opponent_id = away
            break
        if away == team_id:
            opponent_id = home
            break
    return {
        "week": week,
        "team": {"id": team_id, "name": teams.get(team_id)},
        "opponent": {"id": opponent_id, "name": teams.get(opponent_id)}
    }

@app.get("/start-sit")
def start_sit(league_id: int, year: int, team_id: int, week: int, margin: float = 0.5):
    """
    Suggest swaps where a bench candidate projects higher than the current starter
    for that slot by at least `margin` points.
    """
    # get roster snapshot (cached)
    raw = league_get(league_id, year, ["mRoster"], extra_params=[("scoringPeriodId", week)])
    teams = {t["id"]: t for t in raw.get("teams", [])}
    me = teams.get(team_id)
    if not me:
        return {"week": week, "teamId": team_id, "recommendations": [], "note": "Team not found"}

    entries = (me.get("roster") or {}).get("entries", [])

    # split starters vs bench
    starters = [e for e in entries if e.get("lineupSlotId") not in (20, 21)]
    bench = [e for e in entries if e.get("lineupSlotId") in (20, )]

    def player_and_proj(e):
        p = (e.get("playerPoolEntry") or {}).get("player", {})
        proj = find_week_projection(p, week)
        # tiny fallback signal if no projection (ownership %)
        if proj is None:
            own = ((p.get("ownership") or {}).get("percentOwned") or 0) / 10.0
            proj = float(own)
        return p, proj

    # candidates by slot: check eligibleSlots for fit
    recs = []
    for s in starters:
        slot = s.get("lineupSlotId")
        ps, proj_start = player_and_proj(s)
        best = (None, proj_start)

        for b in bench:
            pb = (b.get("playerPoolEntry") or {}).get("player", {})
            if slot in (pb.get("eligibleSlots") or []):
                _, proj_b = player_and_proj(b)
                if proj_b is not None and proj_b > best[1]:
                    best = (b, proj_b)

        if best[0] and (best[1] - proj_start) >= margin:
            # trim fields
            p_from = (s.get("playerPoolEntry") or {}).get("player", {})
            p_to   = (best[0].get("playerPoolEntry") or {}).get("player", {})
            recs.append({
                "slotId": slot,
                "out": {"pid": s.get("playerId"), "name": p_from.get("fullName"), "proj": round(proj_start, 2)},
                "in":  {"pid": best[0].get("playerId"), "name": p_to.get("fullName"), "proj": round(best[1], 2)},
                "delta": round(best[1] - proj_start, 2)
            })

    recs.sort(key=lambda x: -x["delta"])
    return {"week": week, "teamId": team_id, "count": len(recs), "recommendations": recs}

