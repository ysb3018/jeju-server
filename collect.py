import json
import pathlib
import time
import traceback
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
ATTEMPTS = 5


def retry(label, fn):
    """일시적인 네트워크 오류(시간 초과 등)에 대비해 여러 번 시도한다."""
    last = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return fn()
        except Exception as exc:  # 네트워크·API 오류 모두 재시도
            last = exc
            print(f"{label}: attempt {attempt} failed: {exc}")
            if attempt < ATTEMPTS:
                time.sleep(30 * attempt)  # 30, 60, 90, 120초 간격
    raise RuntimeError(f"{label}: all {ATTEMPTS} attempts failed") from last


def get_json(url, params):
    query = dict(params)
    query["serviceKey"] = require_key()
    full = url + "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(full, headers={"User-Agent": "JejuFishingApp/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
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
            payload = retry(f"tide {code} {day}", lambda: get_json(
                TIDE_URL, {"obsCode": code, "reqDate": day, "type": "json", "numOfRows": "10"}))
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


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{name} saved")


def main():
    now = datetime.now(KST)
    OUT.mkdir(parents=True, exist_ok=True)
    failed = []

    # 1) 단기·중기 예보: 실패하면 이전 파일을 그대로 두어 앱이 마지막 정상 데이터를 보게 한다
    try:
        save("weekly.json", {
            "updatedAt": now.isoformat(timespec="seconds"),
            "shortTerm": retry("shortterm", shortterm),
            "midTerm": retry("midterm", lambda: midterm(latest_tmfc())),
        })
    except Exception:
        failed.append("weekly")
        traceback.print_exc()

    # 2) 조석: 6시간마다 갱신
    if now.hour % 6 == 0 or not (OUT / "tide.json").exists():
        try:
            save("tide.json", {"updatedAt": now.isoformat(timespec="seconds"),
                               "obsCodes": fetch_tide(now.date())})
        except Exception:
            failed.append("tide")
            traceback.print_exc()

    # 3) 수온 원본 응답 (격자 번호 확인용)
    try:
        water = retry("water", lambda: get_json(
            WATER_URL, {"analsYmd": now.strftime("%Y%m%d"), "numOfRows": "100", "pageNo": "1"}))
        save("water.json", {"updatedAt": now.isoformat(timespec="seconds"), "raw": water})
    except Exception:
        failed.append("water")
        traceback.print_exc()

    # 실패한 항목이 있어도 나머지는 커밋되도록 종료 코드는 0으로 둔다
    if failed:
        print("FAILED SECTIONS (previous files kept):", ", ".join(failed))


if __name__ == "__main__":
    main()
