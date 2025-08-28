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
