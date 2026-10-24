import json
import pathlib
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from server import KST, latest_tmfc, midterm, shortterm, require_key

TIDE_URL = "https://apis.data.go.kr/1192136/tideFcstHghLw/GetTideFcstHghLwApiService"
WATER_URL = "http://apis.data.go.kr/1192000/apVhdService_Tgcw15/getOpnTgcw15"
# 예보지점: DT_0010 서귀포, DT_0004 제주, DT_0022 성산포, DT_0023 모슬포
OBS_CODES = ["DT_0010", "DT_0004", "DT_0022", "DT_0023"]
TYPE_MAP = {"1": "만조", "2": "간조", "3": "만조", "4": "간조"}
OUT = pathlib.Path("docs/forecast")


def get_json(url, params):
    query = dict(params)
    query["serviceKey"] = require_key()
    full = url + "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(full, headers={"User-Agent": "JejuFishingApp/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def items_of(payload):
    body = payload.get("response", {}).get("body", {})
    found = body.get("items", {})
    if isinstance(found, dict):
        found = found.get("item", [])
    if isinstance(found, dict):
        found = [found]
    return found or []


def check(payload, label):
    header = payload.get("response", {}).get("header", {})
    code = header.get("resultCode")
    if code not in (None, "00"):
        raise RuntimeError(f"{label}: {code} {header.get('resultMsg')}")


def fetch_tide(start):
    result = {}
    for code in OBS_CODES:
        events = []
        for offset in range(7):
            day = (start + timedelta(days=offset)).strftime("%Y%m%d")
            payload = get_json(TIDE_URL, {"obsCode": code, "reqDate": day, "type": "json", "numOfRows": "10"})
            check(payload, f"tide {code} {day}")
            for item in items_of(payload):
                stamp = str(item.get("predcDt", ""))
                events.append({
                    "date": stamp[:10],
                    "time": stamp[11:16],
                    "type": TYPE_MAP.get(str(item.get("extrSe")), "기타"),
                    "heightM": round(float(item.get("predcTdlvVl") or 0) / 100, 2),
                })
        result[code] = events
    return result


def main():
    now = datetime.now(KST)
    OUT.mkdir(parents=True, exist_ok=True)

    weekly = {
        "updatedAt": now.isoformat(timespec="seconds"),
        "shortTerm": shortterm(),
        "midTerm": midterm(latest_tmfc()),
    }
    (OUT / "weekly.json").write_text(json.dumps(weekly, ensure_ascii=False, indent=2), encoding="utf-8")
    print("weekly saved")

    # 조석은 하루 6시간마다 갱신 (API 호출 횟수 절약)
    tide_path = OUT / "tide.json"
    if now.hour % 6 == 0 or not tide_path.exists():
        try:
            tide = {"updatedAt": now.isoformat(timespec="seconds"), "obsCodes": fetch_tide(now.date())}
            tide_path.write_text(json.dumps(tide, ensure_ascii=False, indent=2), encoding="utf-8")
            print("tide saved")
        except Exception as exc:
            print("tide skipped:", exc)

    # 수온 원본 응답을 그대로 저장 (격자 번호 확인용)
    try:
        water = get_json(WATER_URL, {"analsYmd": now.strftime("%Y%m%d"), "numOfRows": "100", "pageNo": "1"})
        water_out = {"updatedAt": now.isoformat(timespec="seconds"), "raw": water}
        (OUT / "water.json").write_text(json.dumps(water_out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("water saved")
    except Exception as exc:
        print("water skipped:", exc)


if __name__ == "__main__":
    main()
