import os
import sys
import time
import urllib.parse
import requests
import pymysql

sys.stdout.reconfigure(line_buffering=True)

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
        "service_key": HEAT_SHELTER_KEY,
        "url": "https://www.safetydata.go.kr/V2/api/DSSP-IF-10804",
        "name_keys": ["REARE_NM", "RSTR_NM", "FCLTY_NM", "SHLT_NM", "NM"],
        "addr_keys": ["RONA_DADDR", "DADDR", "RN_DTL_ADRES", "RDNMADR", "LNMADR", "ADRES"],
        "lat_keys": ["LAT", "LA", "YCRD", "WGS84_LA"],
        "lng_keys": ["LOT", "LO", "LNG", "XCRD", "WGS84_LO"],
    },
    {
        "category": "무더위쉼터",
        "platform": "safetydata",
        "service_key": COLD_SHELTER_KEY,
        "url": "https://www.safetydata.go.kr/V2/api/DSSP-IF-10942",
        "name_keys": ["REARE_NM", "RSTR_NM", "FCLTY_NM", "SHLT_NM", "NM"],
        "addr_keys": ["RONA_DADDR", "DADDR", "RN_DTL_ADRES", "RDNMADR", "LNMADR", "ADRES"],
        "lat_keys": ["LAT", "LA", "YCRD", "WGS84_LA"],
        "lng_keys": ["LOT", "LO", "LNG", "XCRD", "WGS84_LO"],
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
    is_safetydata = source["platform"] == "safetydata"
    num_of_rows = 500 if is_safetydata else 1000
    max_retries = 6 if is_safetydata else 3
    geocode_used = 0
    max_geocode_per_source = 300

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; CapstoneMapBot/1.0)"})

    while True:
        data = None
        for attempt in range(1, max_retries + 1):
            try:
                if is_safetydata:
                    req_url = (
                        f"{source['url']}?serviceKey={raw_key}"
                        f"&returnType=json&pageNo={page}&numOfRows={num_of_rows}"
                    )
                    res = session.get(req_url, timeout=60)
                else:
                    params = {
                        "serviceKey": clean_key,
                        "pageNo": page,
                        "numOfRows": num_of_rows,
                        "type": "json",
                    }
                    res = session.get(source["url"], params=params, timeout=60)

                text = res.text.strip()
                if text.startswith("<"):
                    print(f"[{category}] 일일 호출 한도 초과 또는 XML 오류 응답으로 건너뜁니다.")
                    return collected

                data = res.json()
                break
            except Exception as e:
                print(f"[{category}] {page}페이지 요청 지연 (시도 {attempt}/{max_retries}): {e}")
                time.sleep(min(2 * attempt, 10))

        if data is None:
            print(f"[{category}] {page}페이지 재시도 실패로 현재까지 수집된 {len(collected)}건만 저장합니다.")
            break

        if is_safetydata:
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

        page += 1
        time.sleep(0.4 if is_safetydata else 0.2)

    return collected


def sync_to_tidb():
    all_items = []
    for source in API_SOURCES:
        print(f"▶ [{source['category']}] 공공데이터 수집 시작...")
        items = fetch_api_items(source)
        print(f"  수집 완료: {len(items)}건")
        all_items.extend(items)

    if not all_items:
        print("동기화할 데이터가 없습니다.")
        return

    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT id, category, name, address, lat, lng FROM {TABLE_NAME}")
            existing_rows = cursor.fetchall()

            existing_keys = set()
            for r in existing_rows:
                existing_keys.add((r["category"], r["name"], r["address"] or ""))

            to_insert = []
            for item in all_items:
                key = (item[0], item[1], item[4])
                if key not in existing_keys:
                    to_insert.append(item)
                    existing_keys.add(key)

            insert_sql = (
                f"INSERT INTO {TABLE_NAME} (category, name, lat, lng, address, status, note) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)"
            )
            for i in range(0, len(to_insert), 500):
                conn.ping(reconnect=True)
                cursor.executemany(insert_sql, to_insert[i : i + 500])
                conn.commit()

        print(f"✅ 동기화 완료! 신규 추가: {len(to_insert)}건")
    finally:
        conn.close()


if __name__ == "__main__":
    sync_to_tidb()
