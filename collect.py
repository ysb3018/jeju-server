import json
import pathlib
import socket
import time
import traceback
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from server import KST, latest_tmfc, midterm, shortterm, require_key

TIDE_URL = "https://apis.data.go.kr/1192136/tideFcstHghLw/GetTideFcstHghLwApiService"
# 조위관측소 실측 수온 (국립해양조사원)
WATER_OBS_URL = "https://apis.data.go.kr/1192136/surveyWaterTemp/GetSurveyWaterTempApiService"
WATER_OBS_CODES = {"DT_0010": "서귀포", "DT_0004": "제주"}
# 예보지점: DT_0010 서귀포, DT_0004 제주, DT_0022 성산포, DT_0023 모슬포
OBS_CODES = ["DT_0010", "DT_0004", "DT_0022", "DT_0023"]
TYPE_MAP = {"1": "만조", "2": "간조", "3": "만조", "4": "간조"}
OUT = pathlib.Path("docs/forecast")
ATTEMPTS = 5
# 수온 격자 (0.25도 격자, 첨부 격자표로 확인): 서귀포 앞바다 → 제주시 앞바다 순으로 시도
WATER_GRIDS = ["GR2_F4K43", "GR2_F4K23"]
# 수온 자료는 최근 날짜가 늦게 올라오므로 최대 60일까지 거슬러 올라간다
WATER_LOOKBACK_DAYS = 60


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


def fetch_text(url, params):
    query = dict(params)
    query["serviceKey"] = require_key()
    full = url + "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(full, headers={"User-Agent": "JejuFishingApp/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read().decode("utf-8", errors="replace")


def get_json(url, params):
    text = fetch_text(url, params)
    try:
        data = json.loads(text)
    except ValueError:
        return xml_to_dict(text)
    # 일부 API는 response로 감싸지 않고 header/body를 바로 준다
    if "response" not in data and "header" in data:
        data = {"response": data}
    return data


def xml_to_dict(text):
    """XML 응답을 JSON과 같은 모양(response.header / response.body.items.item)으로 바꾼다."""
    root = ET.fromstring(text)
    header = {c.tag: (c.text or "") for c in root.find("header")} if root.find("header") is not None else {}
    items = []
    body = root.find("body")
    if body is not None:
        for item in body.iter("item"):
            items.append({c.tag: (c.text or "") for c in item})
    return {"response": {"header": header, "body": {"items": {"item": items}}}}


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


def fetch_tide(start, raw_sample):
    result = {}
    for code in OBS_CODES:
        events = []
        for offset in range(7):
            day = (start + timedelta(days=offset)).strftime("%Y%m%d")
            payload = retry(f"tide {code} {day}", lambda: get_json(
                TIDE_URL, {"obsCode": code, "reqDate": day, "type": "json", "numOfRows": "10"}))
            if not raw_sample:
                raw_sample.append({"obsCode": code, "reqDate": day, "response": payload})
            check(payload, f"tide {code} {day}")
            print(f"tide {code} {day}: {len(items_of(payload))} items")
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


def reachable(host="apis.data.go.kr", port=443, timeout=15):
    """공공데이터 서버에 TCP 연결이 되는지 짧게 확인한다. 안 되면 실행을 빨리 끝낸다."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError as exc:
        print(f"precheck failed: {host}:{port} unreachable ({exc})")
        return False


def tide_has_events():
    path = OUT / "tide.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return any(data.get("obsCodes", {}).values())
    except ValueError:
        return False


def fetch_water(today):
    """격자와 날짜를 바꿔 가며 처음으로 자료가 있는 조합을 쓴다."""
    last = None
    for grid in WATER_GRIDS:
        for back in range(WATER_LOOKBACK_DAYS):
            day = (today - timedelta(days=back)).strftime("%Y%m%d")
            text = retry(f"water {grid} {day}", lambda: fetch_text(
                WATER_URL, {"analsYmd": day, "gridCd": grid, "numOfRows": "100", "pageNo": "1"}))
            parsed = xml_to_dict(text)
            check(parsed, f"water {grid} {day}")
            total = parsed["response"]["body"]["items"]["item"]
            if total:
                print(f"water {grid} {day}: {len(total)} items")
            last = (f"{grid} {day}", parsed, text)
            if total:
                return last
    return last


def fetch_water_obs(today):
    """관측소별 실측 수온. 최근 3일 안에서 처음 자료가 있는 날을 쓴다."""
    stations = {}
    for code, name in WATER_OBS_CODES.items():
        found = None
        for back in range(3):
            day = (today - timedelta(days=back)).strftime("%Y%m%d")
            payload = retry(f"water {code} {day}", lambda: get_json(
                WATER_OBS_URL, {"obsCode": code, "reqDate": day, "type": "json",
                                "min": "60", "numOfRows": "100", "pageNo": "1"}))
            check(payload, f"water {code} {day}")
            items = items_of(payload)
            print(f"water {code} {day}: {len(items)} items")
            if items:
                found = (day, items)
                break
        if found:
            day, items = found
            stations[code] = {
                "name": name,
                "reqDate": day,
                "items": [{
                    "date": str(i.get("obsrvnDt", ""))[:10],
                    "time": str(i.get("obsrvnDt", ""))[11:16],
                    "tempC": i.get("wtem"),
                } for i in items],
            }
        else:
            stations[code] = {"name": name, "reqDate": None, "items": []}
    return stations


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{name} saved")


def main():
    now = datetime.now(KST)
    OUT.mkdir(parents=True, exist_ok=True)
    failed = []

    if not reachable():
        # 서버에 아예 연결이 안 되면 모든 항목을 건너뛰고 빨리 끝낸다 (이전 파일 유지)
        print("API server unreachable; skipping this run. Previous files kept.")
        return

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
    if now.hour % 6 == 0 or not tide_has_events():
        try:
            raw_sample = []
            tide_data = fetch_tide(now.date(), raw_sample)
            save("tide.json", {"updatedAt": now.isoformat(timespec="seconds"),
                               "obsCodes": tide_data, "rawSample": raw_sample})
        except Exception:
            failed.append("tide")
            traceback.print_exc()

    # 3) 조위관측소 실측 수온 (자료가 하나도 없으면 이전 파일 유지)
    try:
        stations = fetch_water_obs(now.date())
        if any(v["items"] for v in stations.values()):
            save("water.json", {"updatedAt": now.isoformat(timespec="seconds"), "stations": stations})
        else:
            print("water: no observations in last 3 days; previous file kept")
    except Exception:
        failed.append("water")
        traceback.print_exc()

    # 실패한 항목이 있어도 나머지는 커밋되도록 종료 코드는 0으로 둔다
    if failed:
        print("FAILED SECTIONS (previous files kept):", ", ".join(failed))


if __name__ == "__main__":
    main()
