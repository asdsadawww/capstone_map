import os
import time
import urllib.parse
import requests
import pymysql

DB_CONFIG = {
    "host": "gateway01.ap-northeast-1.prod.aws.tidbcloud.com",
    "port": 4000,
    "user": "24EZ7grkonk8qKX.root",
    "password": os.getenv("DB_PASSWORD", ""),
    "database": "map_db",
    "charset": "utf8mb4",
    "ssl": {"ca": None},
    "cursorclass": pymysql.cursors.DictCursor,
}

TABLE_NAME = "location_items"

PUBLIC_DATA_KEY = os.getenv("PUBLIC_DATA_KEY", "").strip()
COLD_SHELTER_KEY = os.getenv("COLD_SHELTER_KEY", "").strip()
HEAT_SHELTER_KEY = os.getenv("HEAT_SHELTER_KEY", "").strip()
KAKAO_REST_KEY = os.getenv("KAKAO_REST_KEY", "").strip()

API_SOURCES = [
    {
        "category": "공중화장실",
        "platform": "data_go_kr",
        "service_key": PUBLIC_DATA_KEY,
        "url": "http://api.data.go.kr/openapi/tn_pubr_public_toilet_api",
        "name_keys": ["toiletNm"],
        "addr_keys": ["rdnmadr", "lnmadr"],
        "lat_keys": ["latitude"],
        "lng_keys": ["longitude"],
    },
    {
        "category": "졸음쉼터",
        "platform": "data_go_kr",
        "service_key": PUBLIC_DATA_KEY,
        "url": "http://api.data.go.kr/openapi/tn_pubr_public_rest_area_api",
        "name_keys": ["restAreaNm"],
        "addr_keys": ["rdnmadr", "lnmadr"],
        "lat_keys": ["latitude"],
        "lng_keys": ["longitude"],
    },
    {
        "category": "AED",
        "platform": "data_go_kr",
        "service_key": PUBLIC_DATA_KEY,
        "url": "http://apis.data.go.kr/B552657/AEDInfoInqireService/getAedFullDown",
        "name_keys": ["buildPlace"],
        "addr_keys": ["buildAddress"],
        "lat_keys": ["wgs84Lat"],
        "lng_keys": ["wgs84Lon"],
    },
    {
        "category": "한파쉼터",
        "platform": "safetydata",
        "service_key": COLD_SHELTER_KEY,
        "url": "https://www.safetydata.go.kr/V2/api/DSSP-IF-10804",
        "name_keys": ["RSTR_NM", "FCLTY_NM", "SHLT_NM", "COLD_RSTR_NM", "NM"],
        "addr_keys": ["RN_DTL_ADRES", "RDNMADR", "DTL_ADRES", "LNMADR", "ADRES", "ADDR"],
        "lat_keys": ["LA", "LAT", "YCRD", "WGS84_LA"],
        "lng_keys": ["LO", "LOT", "LNG", "XCRD", "WGS84_LO"],
    },
    {
        "category": "무더위쉼터",
        "platform": "safetydata",
        "service_key": HEAT_SHELTER_KEY,
        "url": "https://www.safetydata.go.kr/V2/api/DSSP-IF-10942",
        "name_keys": ["RSTR_NM", "FCLTY_NM", "SHLT_NM", "HEAT_RSTR_NM", "NM"],
        "addr_keys": ["RN_DTL_ADRES", "RDNMADR", "DTL_ADRES", "LNMADR", "ADRES", "ADDR"],
        "lat_keys": ["LA", "LAT", "YCRD", "WGS84_LA"],
        "lng_keys": ["LO", "LOT", "LNG", "XCRD", "WGS84_LO"],
    },
]


def pick_value(row: dict, keys: list) -> str:
    lower_map = {str(k).lower(): v for k, v in row.items()}
    for k in keys:
        val = row.get(k)
        if val is None:
            val = lower_map.get(k.lower())
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return ""


def geocode_address(address: str, fallback_name: str = ""):
    if not KAKAO_REST_KEY or not address:
        return None, None
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    try:
        res = requests.get(
            "https://dapi.kakao.com/v2/local/search/address.json",
            headers=headers,
            params={"query": address},
            timeout=10,
        )
        docs = res.json().get("documents", [])
        if docs:
            return float(docs[0]["y"]), float(docs[0]["x"])
    except Exception:
        pass
    if fallback_name:
        try:
            region = address.split()[0] if address.split() else ""
            res = requests.get(
                "https://dapi.kakao.com/v2/local/search/keyword.json",
                headers=headers,
                params={"query": f"{region} {fallback_name}".strip()},
                timeout=10,
            )
            docs = res.json().get("documents", [])
            if docs:
                return float(docs[0]["y"]), float(docs[0]["x"])
        except Exception:
            pass
    return None, None


def fetch_api_items(source: dict):
    category = source["category"]
    raw_key = source.get("service_key", "")
    if not raw_key:
        print(f"[{category}] 서비스키가 비어 있어 건너뜁니다.")
        return []

    clean_key = urllib.parse.unquote(raw_key)
    collected = []
    page = 1
    num_of_rows = 1000
    geocode_used = 0
    max_geocode_per_source = 300

    while True:
        data = None
        for attempt in range(1, 4):
            try:
                if source["platform"] == "safetydata":
                    req_url = (
                        f"{source['url']}?serviceKey={raw_key}"
                        f"&returnType=json&pageNo={page}&numOfRows={num_of_rows}"
                    )
                    res = requests.get(req_url, timeout=60)
                else:
                    params = {
                        "serviceKey": clean_key,
                        "pageNo": page,
                        "numOfRows": num_of_rows,
                        "type": "json",
                    }
                    res = requests.get(source["url"], params=params, timeout=60)
                data = res.json()
                break
            except Exception as e:
                print(f"[{category}] {page}페이지 요청 지연 (시도 {attempt}/3): {e}")
                time.sleep(2 * attempt)

        if data is None:
            print(f"[{category}] {page}페이지 3회 실패로 수집 중단")
            break

        if source["platform"] == "safetydata":
            header = data.get("header", {})
            result_code = str(header.get("resultCode", ""))
            if result_code != "00":
                print(f"[{category}] API 응답 오류 ({result_code}): {header.get('resultMsg')}")
                break
            total_count = int(data.get("totalCount", 0))
            items = data.get("body") or []
        else:
            response = data.get("response", {})
            header = response.get("header", {})
            result_code = str(header.get("resultCode", ""))
            if result_code != "00":
                print(f"[{category}] API 응답 오류 ({result_code}): {header.get('resultMsg')}")
                break
            body = response.get("body", {})
            total_count = int(body.get("totalCount", 0))
            raw_items = body.get("items", [])
            if isinstance(raw_items, dict):
                items = raw_items.get("item", [])
            else:
                items = raw_items or []
            if isinstance(items, dict):
                items = [items]

        if not items:
            break

        if page == 1 and isinstance(items[0], dict):
            test_name = pick_value(items[0], source["name_keys"])
            if not test_name:
                print(f"[{category}] 경고: 이름 키 매칭 실패! 실제 응답 키 목록: {list(items[0].keys())}")

        for row in items:
            if not isinstance(row, dict):
                continue

            name = pick_value(row, source["name_keys"])
            if not name:
                continue

            addr = pick_value(row, source["addr_keys"])
            lat_str = pick_value(row, source["lat_keys"])
            lng_str = pick_value(row, source["lng_keys"])

            try:
                lat = float(lat_str) if lat_str else 0.0
                lng = float(lng_str) if lng_str else 0.0
            except ValueError:
                lat, lng = 0.0, 0.0

            if 124.0 <= lat <= 132.0 and 33.0 <= lng <= 39.0:
                lat, lng = lng, lat

            if not (33.0 <= lat <= 39.0 and 124.0 <= lng <= 132.0):
                if geocode_used < max_geocode_per_source and addr:
                    lat, lng = geocode_address(addr, name)
                    geocode_used += 1
                    time.sleep(0.05)
                if not lat or not lng:
                    continue

            collected.append((category, name, lat, lng, addr, "PENDING", ""))

        print(f"  - {page}페이지 완료 (누적 {len(collected)} / 전체 {total_count}건)")

        if page * num_of_rows >= total_count:
            break
