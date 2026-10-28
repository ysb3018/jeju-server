import json
import os
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

KST = timezone(timedelta(hours=9))
HOST = os.getenv("APP_HOST", "0.0.0.0")
PORT = int(os.getenv("APP_PORT", "8080"))
KMA_BASE = "https://apis.data.go.kr/1360000"


def latest_tmfc(now=None):
    now = now or datetime.now(KST)
    issue = now.replace(hour=18 if now.hour >= 18 else 6, minute=0, second=0, microsecond=0)
    if now < issue:
        issue -= timedelta(days=1)
    return issue.strftime("%Y%m%d%H%M")


def require_key():
    key = os.getenv("KMA_SERVICE_KEY", "").strip()
    if not key:
        raise RuntimeError("KMA_SERVICE_KEY is not configured")
    return key


def kma_json(path, params):
    params = dict(params)
    params["serviceKey"] = require_key()
    params.setdefault("pageNo", "1")
    params.setdefault("numOfRows", "1000")
    params.setdefault("dataType", "JSON")
    request = Request(f"{KMA_BASE}/{path}?{urlencode(params)}", headers={"User-Agent": "JejuFishingApp/1.0"})
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    header = payload.get("response", {}).get("header", {})
    if header.get("resultCode") != "00":
        raise RuntimeError(f"KMA {header.get('resultCode')}: {header.get('resultMsg')}")
    return payload.get("response", {}).get("body", {})


def item_list(body):
    items = body.get("items", {}).get("item", [])
    return items if isinstance(items, list) else [items]


def shortterm(now=None):
    now = now or datetime.now(KST)
    release = now - timedelta(minutes=40)
    slots = [(2, 0), (5, 0), (8, 0), (11, 0), (14, 0), (17, 0), (20, 0), (23, 0)]
    candidates = [(h, m) for h, m in slots if (h, m) <= (release.hour, release.minute)]
    if candidates:
        chosen = max(candidates)
        base = release.replace(hour=chosen[0], minute=chosen[1], second=0, microsecond=0)
    else:
        # 새벽 00~02시 40분 사이에는 아직 당일 02시 발표가 없으므로 전날 23시 발표를 사용
        base = (release - timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0)
    rows = item_list(kma_json("VilageFcstInfoService_2.0/getVilageFcst", {
        "base_date": base.strftime("%Y%m%d"), "base_time": base.strftime("%H%M"),
        "nx": "48", "ny": "36", "numOfRows": "1000"
    }))
    grouped = {}
    for row in rows:
        grouped.setdefault((row.get("fcstDate"), row.get("fcstTime")), {})[row.get("category")] = row.get("fcstValue")
    days = {}
    for (date_key, time_key), values in grouped.items():
        d = days.setdefault(date_key, {"date": f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:]}", "hours": []})
        d["hours"].append({"time": time_key, "temp": values.get("TMP"), "windSpeed": values.get("WSD"), "windDirection": values.get("VEC"), "rainProbability": values.get("POP"), "sky": values.get("SKY"), "precipitationType": values.get("PTY"), "rainAmount": values.get("PCP")})
    result = []
    for d in sorted(days.values(), key=lambda x: x["date"]):
        temps = [float(x["temp"]) for x in d["hours"] if str(x.get("temp", "")).replace('.', '', 1).isdigit()]
        pops = [int(float(x["rainProbability"])) for x in d["hours"] if str(x.get("rainProbability", "")).replace('.', '', 1).isdigit()]
        sampled = sorted((x for x in d["hours"] if x["time"] in {"0000", "0300", "0600", "0900", "1200", "1500", "1800", "2100"}), key=lambda x: x["time"])
        result.append({"date": d["date"], "minTemp": min(temps) if temps else None, "maxTemp": max(temps) if temps else None, "rainProbability": max(pops) if pops else None, "hours": sampled, "source": "KMA 단기예보 · 한림읍 nx=48 ny=36"})
    return {"baseTime": base.strftime("%Y%m%d%H%M"), "days": result}


def midterm(tmfc):
    land = item_list(kma_json("MidFcstInfoService/getMidLandFcst", {"regId": "11G00000", "tmFc": tmfc, "numOfRows": "10"}))
    temp = item_list(kma_json("MidFcstInfoService/getMidTa", {"regId": "11G00201", "tmFc": tmfc, "numOfRows": "10"}))
    sea = item_list(kma_json("MidFcstInfoService/getMidSeaFcst", {"regId": "12B10500", "tmFc": tmfc, "numOfRows": "10"}))
    land, temp, sea = land[0], temp[0], sea[0]
    issue = datetime.strptime(tmfc, "%Y%m%d%H%M").replace(tzinfo=KST)
    days = []
    for offset in range(5, 11):
        date = issue.date() + timedelta(days=offset)
        suffix = str(offset)
        am = {
            "weather": land.get(f"wf{suffix}Am"),
            "rainProbability": land.get(f"rnSt{suffix}Am"),
            "waveMin": sea.get(f"wh{suffix}AAm"),
            "waveMax": sea.get(f"wh{suffix}BAm"),
        }
        pm = {
            "weather": land.get(f"wf{suffix}Pm"),
            "rainProbability": land.get(f"rnSt{suffix}Pm"),
            "waveMin": sea.get(f"wh{suffix}APm"),
            "waveMax": sea.get(f"wh{suffix}BPm"),
        }
        if offset >= 8:
            am = {
                "weather": land.get(f"wf{suffix}"),
                "rainProbability": land.get(f"rnSt{suffix}"),
                "waveMin": sea.get(f"wh{suffix}A"),
                "waveMax": sea.get(f"wh{suffix}B"),
            }
            pm = None
        days.append({
            "date": date.isoformat(),
            "offset": offset,
            "minTemp": temp.get(f"taMin{suffix}"),
            "maxTemp": temp.get(f"taMax{suffix}"),
            "am": am,
            "pm": pm,
        })
    return {"issuedAt": tmfc, "region": "제주도", "days": days}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self.send_json(200, {"ok": True, "service": "jeju-fishing-backend", "kmaKeyConfigured": bool(os.getenv("KMA_SERVICE_KEY", "").strip())})
            return
        if path == "/api/forecast/weekly":
            try:
                tmfc = parse_qs(urlparse(self.path).query).get("tmFc", [latest_tmfc()])[0]
                self.send_json(200, {"shortTerm": shortterm(), "midTerm": midterm(tmfc)})
            except Exception as exc:
                self.send_json(503, {"ok": False, "error": str(exc), "hint": "Set KMA_SERVICE_KEY and use a published 0600/1800 tmFc."})
            return
        self.send_json(404, {"ok": False, "error": "not found"})

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {fmt % args}")


if __name__ == "__main__":
    print(f"Jeju Fishing backend listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
