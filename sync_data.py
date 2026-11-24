import os
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
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
        "platform": "data_go_kr_v2",
        "service_key": PUBLIC_DATA_KEY,
        "url": "https://apis.data.go.kr/1741000/public_restroom_info_v2/info_v2",
        "name_keys": ["toiletNm", "화장실명", "FCLTY_NM", "REARE_NM", "RESTROOM_NM", "NM"],
        "addr_keys": ["rdnmadr", "lnmadr", "소재지도로명주소", "소재지지번주소", "RONA_DADDR", "DADDR", "ADRES"],
        "lat_keys": ["latitude", "WGS84위도", "위도", "LAT", "LA"],
        "lng_keys": ["longitude", "WGS84경도", "경도", "LOT", "LO", "LNG"],
    },
    {
        "category": "졸음쉼터",
        "platform": "data_go_kr",
        "service_key": PUBLIC_DATA_KEY,
        "url": "https://api.data.go.kr/openapi/tn_pubr_public_drowsy_shelter_api",
        "name_keys": ["drowsyShelterNm", "restAreaNm", "졸음쉼터명", "FCLTY_NM", "REARE_NM", "NM"],
        "addr_keys": ["rdnmadr", "lnmadr", "소재지도로명주소", "소재지지번주소", "RONA_DADDR", "DADDR", "ADRES"],
        "lat_keys": ["latitude", "WGS84위도", "위도", "LAT", "LA"],
        "lng_keys": ["longitude", "WGS84경도", "경도", "LOT", "LO", "LNG"],
    },
    {
        "category": "AED",
        "platform": "data_go_kr",
        "service_key": PUBLIC_DATA_KEY,
        "url": "https://apis.data.go.kr/B552657/AEDInfoInqireService/getAedFullDown",
        "name_keys": ["buildPlace", "org"],
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


def parse_xml_response(xml_text: str):
    root = ET.fromstring(xml_text)
    err_msg = root.findtext(".//errMsg")
    if err_msg:
        reason = root.findtext(".//returnReasonCode") or ""
        return {"error": f"({reason}) {err_msg}"}

    result_code = (root.findtext(".//header/resultCode") or "").strip()
    if result_code and result_code != "00":
        result_msg = root.findtext(".//header/resultMsg") or ""
        return {"error": f"({result_code}) {result_msg}"}

    total_str = (root.findtext(".//body/totalCount") or "0").strip()
    total_count = int(total_str) if total_str.isdigit() else 0
    items = []
    for item_el in root.findall(".//body/items/item"):
        row = {child.tag: (child.text or "").strip() for child in item_el}
        items.append(row)
    return {"totalCount": total_count, "items": items}


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
    platform = source["platform"]
    is_safetydata = platform == "safetydata"
    num_of_rows = 500 if is_safetydata else 1000
    max_retries = 6 if is_safetydata else 3
    geocode_used = 0
    max_geocode_per_source = 300

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; CapstoneMapBot/1.0)"})

    while True:
        data = None
        xml_parsed = None
        for attempt in range(1, max_retries + 1):
            try:
                if is_safetydata or platform == "data_go_kr_v2":
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
                    if "SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in res.text:
                        raw_url = (
                            f"{source['url']}?serviceKey={raw_key}"
                            f"&pageNo={page}&numOfRows={num_of_rows}&type=json"
                        )
                        res = session.get(raw_url, timeout=60)

                text = res.text.strip()
                if text.startswith("<"):
                    xml_parsed = parse_xml_response(text)
                    break

                data = res.json()
                break
            except Exception as e:
                print(f"[{category}] {page}페이지 요청 지연 (시도 {attempt}/{max_retries}): {e}")
                time.sleep(min(2 * attempt, 10))

        if data is None and xml_parsed is None:
            print(f"[{category}] {page}페이지 재시도 실패로 현재까지 수집된 {len(collected)}건만 저장합니다.")
            break

        if xml_parsed is not None:
            if "error" in xml_parsed:
                print(f"[{category}] API 응답 오류: {xml_parsed['error']}")
                break
            total_count = xml_parsed["totalCount"]
            items = xml_parsed["items"]
        elif "response" in data and isinstance(data["response"], dict):
            response = data["response"]
            header = response.get("header") or data
            result_code = str(header.get("resultCode", ""))
            if result_code !=
