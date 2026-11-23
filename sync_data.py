import os
import time
import xml.etree.ElementTree as ET
from urllib.parse import unquote
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
PUBLIC_DATA_KEY = os.getenv("PUBLIC_DATA_KEY", "")  # 공공데이터포털 인증키 (Encoding/Decoding 모두 자동 처리)
KAKAO_REST_KEY = os.getenv("KAKAO_REST_KEY", "")    # 카카오 디벨로퍼스 REST API 키

API_SOURCES = [
    {
        "category": "공중화장실",
        "url": "http://api.data.go.kr/openapi/tn_pubr_public_toilet_api",
        "name_key": "toiletNm",
        "addr_key": "rdnmadr",
        "fallback_addr_key": "lnmadr",
        "lat_key": "latitude",
        "lng_key": "longitude",
    },
    {
        "category": "졸음쉼터",
        "url": "http://api.data.go.kr/openapi/tn_pubr_public_rest_area_api",
        "name_key": "restAreaNm",
        "addr_key": "rdnmadr",
        "fallback_addr_key": "lnmadr",
        "lat_key": "latitude",
        "lng_key": "longitude",
    },
    {
        "category": "AED",
        "url": "http://apis.data.go.kr/B552657/AEDInfoInqireService/getAedFullDown",
        "name_key": "buildPlace",
        "addr_key": "buildAddress",
        "fallback_addr_key": "buildAddress",
        "lat_key": "wgs84Lat",
        "lng_key": "wgs84Lon",
    },
]


def geocode_address(address: str, fallback_name: str = ""):
    """주소를 카카오 로컬 REST API로 위도(lat), 경도(lng) 변환"""
    if not KAKAO_REST_KEY or not address:
        return None, None

    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}

    try:
        res = requests.get(
            "https://dapi.kakao.com/v2/local/search/address.json",
            headers=headers,
            params={"query": address},
            timeout=5,
        )
        docs = res.json().get("documents", [])
        if docs:
            return float(docs[0]["y"]), float(docs[0]["x"])
    except Exception:
        pass

    if fallback_name:
        try:
            region_hint = address.split()[0] if address.split() else ""
            res = requests.get(
                "https://dapi.kakao.com/v2/local/search/keyword.json",
                headers=headers,
                params={"query": f"{region_hint} {fallback_name}".strip()},
                timeout=5,
            )
            docs = res.json().get("documents", [])
            if docs:
                return float(docs[0]["y"]), float(docs[0]["x"])
        except Exception:
            pass

    return None, None


def fetch_api_items(source):
    """공공데이터 API 응답이 XML이든 JSON이든 자동으로 파싱하여 수집"""
    collected = []
    page = 1
    num_of_rows = 1000
    clean_key = unquote(PUBLIC_DATA_KEY)  # Encoding 키를 넣었더라도 이중 인코딩 방지

    while True:
        params = {
            "serviceKey": clean_key,
            "pageNo": page,
            "numOfRows": num_of_rows,
            "type": "json",
        }
        try:
            res = requests.get(source["url"], params=params, timeout=20)
            raw_text = res.text.lstrip()
        except Exception as e:
            print(f"[{source['category']}] {page}페이지 네트워크 오류: {e}")
            break

        items = []
        total_count = 0

        # 1) 응답이 XML인 경우 (AED API 및 공공데이터 게이트웨이 인증 오류)
        if raw_text.startswith("<"):
            try:
                root = ET.fromstring(raw_text)
            except ET.ParseError as e:
                print(f"[{source['category']}] XML 파싱 실패: {e}")
                break

            auth_msg = root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg")
            if auth_msg:
                print(f"[{source['category']}] 인증/활용신청 오류: {auth_msg}")
                break

            result_code = root.findtext(".//resultCode")
            result_msg = root.findtext(".//resultMsg")
            if result_code and result_code != "00":
                print(f"[{source['category']}] API 응답 오류 ({result_code}): {result_msg}")
                break

            total_count = int(root.findtext(".//totalCount") or 0)
            for item_el in root.findall(".//item"):
                items.append({child.tag: (child.text or "").strip() for child in item_el})

        # 2) 응답이 JSON인 경우 (공중화장실, 졸음쉼터 등 표준데이터)
        else:
            try:
                data = res.json()
            except Exception as e:
                print(f"[{source['category']}] JSON 파싱 실패: {e} | 응답 미리보기: {raw_text[:120]}")
                break

            header = data.get("response", {}).get("header", {})
            result_code = str(header.get("resultCode", ""))
            if result_code and result_code != "00":
                print(f"[{source['category']}] API 응답 오류 ({result_code}): {header.get('resultMsg')}")
                break

            body = data.get("response", {}).get("body", {})
            total_count = int(body.get("totalCount", 0))
            raw_items = body.get("items", [])
            if isinstance(raw_items, dict):
                raw_items = raw_items.get("item", [])
            if isinstance(raw_items, dict):
                raw_items = [raw_items]
            items = raw_items

        if not items:
            break

        for row in items:
            name = str(row.get(source["name_key"]) or "").strip()
            addr = str(row.get(source["addr_key"]) or row.get(source["fallback_addr_key"]) or "").strip()
            if not name:
                continue

            try:
                lat = float(row.get(source["lat_key"]) or 0)
                lng = float(row.get(source["lng_key"]) or 0)
            except ValueError:
                lat, lng = 0.0, 0.0

            if not (33.0 <= lat <= 39.0 and 124.0 <= lng <= 132.0):
                lat, lng = geocode_address(addr, name)
                time.sleep(0.05)
                if not lat or not lng:
                    continue

            collected.append({
                "category": source["category"],
                "name": name,
                "address": addr,
                "lat": lat,
                "lng": lng,
            })

        print(f"  - {page}페이지 완료 (누적 {len(collected)} / 전체 {total_count}건)")
        if page * num_of_rows >= total_count:
            break

        page += 1
        time.sleep(0.2)

    return collected


def sync_to_tidb():
    if not PUBLIC_DATA_KEY:
        raise RuntimeError("PUBLIC_DATA_KEY 환경변수가 설정되지 않았습니다.")

    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT id, category, name, address, lat, lng FROM {TABLE_NAME}")
            existing_rows = cursor.fetchall()

            existing_map = {
                (r["category"], r["name"], r["address"] or ""): r
                for r in existing_rows
            }

            to_insert = []
            to_update = []

            for source in API_SOURCES:
                print(f"▶ [{source['category']}] 공공데이터 수집 시작...")
                api_items = fetch_api_items(source)
                print(f"  수집 완료: {len(api_items)}건")

                for item in api_items:
                    key = (item["category"], item["name"], item["address"])
                    if key not in existing_map:
                        to_insert.append((
                            item["category"], item["name"], item["lat"], item["lng"], item["address"]
                        ))
                        existing_map[key] = True
                    else:
                        old = existing_map[key]
                        if isinstance(old, dict):
                            if abs(float(old["lat"]) - item["lat"]) > 1e-5 or abs(float(old["lng"]) - item["lng"]) > 1e-5:
                                to_update.append((item["lat"], item["lng"], old["id"]))

            if to_insert:
                insert_sql = f"""
                    INSERT INTO {TABLE_NAME} (category, name, lat, lng, address, status, note)
                    VALUES (%s, %s, %s, %s, %s, 'PENDING', '')
                """
                for i in range(0, len(to_insert), 1000):
                    cursor.executemany(insert_sql, to_insert[i:i + 1000])

            if to_update:
                update_sql = f"UPDATE {TABLE_NAME} SET lat = %s, lng = %s WHERE id = %s"
                for i in range(0, len(to_update), 1000):
                    cursor.executemany(update_sql, to_update[i:i + 1000])

        conn.commit()
        print(f"✅ 동기화 완료! 신규 추가: {len(to_insert)}건 / 좌표 갱신: {len(to_update)}건")
    finally:
        conn.close()


if __name__ == "__main__":
    sync_to_tidb()