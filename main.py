from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
import pymysql
import os
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. TiDB 클라우드 접속 정보 (비밀번호만 입력)

# ... 기존 import 생략 ...

DB_CONFIG = {
    "host": "gateway01.ap-northeast-1.prod.aws.tidbcloud.com",
    "port": 4000,
    "user": "24EZ7grkonk8qKX.root",
    "password": os.getenv("DB_PASSWORD", "tg7jqsvYAyZPMEfX"),  # 호스팅 설정창에서 입력할 예정
    "database": "map_db",
    "charset": "utf8mb4",
    "ssl": {"ca": None},
    "cursorclass": pymysql.cursors.DictCursor,
}

# 실제 테이블 이름으로 맞춤
TABLE_NAME = "location_items"

def get_db():
    return pymysql.connect(**DB_CONFIG)

# 1. 현재 지도 화면 영역 내 시설 조회 API
@app.get("/api/locations")
def get_locations(
    sw_lat: float = Query(...),
    sw_lng: float = Query(...),
    ne_lat: float = Query(...),
    ne_lng: float = Query(...),
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    limit: int = Query(800)
):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            sql = f"""
                SELECT id, category, name, lat, lng, address, status, note
                FROM {TABLE_NAME}
                WHERE lat BETWEEN %s AND %s
                  AND lng BETWEEN %s AND %s
            """
            params = [sw_lat, ne_lat, sw_lng, ne_lng]

            if category and category != "ALL":
                sql += " AND category = %s"
                params.append(category)
            if status and status != "ALL":
                sql += " AND status = %s"
                params.append(status)
            if keyword and keyword.strip():
                sql += " AND (name LIKE %s OR address LIKE %s)"
                kw = f"%{keyword.strip()}%"
                params.extend([kw, kw])

            sql += " LIMIT %s"
            params.append(limit)

            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        conn.close()

# 2. 검수 상태 및 메모 저장 API
class UpdatePayload(BaseModel):
    status: Optional[str] = None
    note: Optional[str] = None

@app.patch("/api/locations/{item_id}")
def update_location(item_id: int, payload: UpdatePayload):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            updates = []
            params = []
            if payload.status is not None:
                updates.append("status = %s")
                params.append(payload.status)
            if payload.note is not None:
                updates.append("note = %s")
                params.append(payload.note)
            if not updates:
                raise HTTPException(status_code=400, detail="수정할 내용이 없습니다.")

            params.append(item_id)
            sql = f"UPDATE {TABLE_NAME} SET {', '.join(updates)} WHERE id = %s"
            cursor.execute(sql, params)
        conn.commit()
        return {"success": True}
    finally:
        conn.close()

# 3. 신규 시설 등록 API
class CreatePayload(BaseModel):
    category: str
    name: str
    lat: float
    lng: float
    address: Optional[str] = ""
    note: Optional[str] = ""

@app.post("/api/locations")
def create_location(payload: CreatePayload):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            sql = f"""
                INSERT INTO {TABLE_NAME} (category, name, lat, lng, address, status, note)
                VALUES (%s, %s, %s, %s, %s, 'PENDING', %s)
            """
            cursor.execute(sql, (payload.category, payload.name, payload.lat, payload.lng, payload.address, payload.note))
            new_id = cursor.lastrowid
        conn.commit()
        return {"id": new_id, "success": True}
    finally:
        conn.close()
        
        
import requests  # requirements.txt에도 'requests' 한 줄 추가 필요

TMAP_APP_KEY = os.getenv("TMAP_APP_KEY", "3xr2gmMxLL89OGeglZ1vv3oyFXhESAGiadCjQBCU")

@app.get("/api/nearest")
def get_nearest_facility(
    category: str = Query(...),
    lat: float = Query(...),
    lng: float = Query(...),
    mode: str = Query("auto")  # auto, walk, transit, car
):
    if mode == "auto":
        mode = "car" if category == "졸음쉼터" else "walk"

    # 이동수단별 1차 DB 탐색 반경 (차량/대중교통은 약 20km, 도보는 약 3km)
    is_wide = mode in ("car", "transit")
    lat_range = 0.20 if is_wide else 0.03
    lng_range = 0.25 if is_wide else 0.035

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            sql = f"""
                SELECT id, category, name, lat, lng, address, status, note,
                       (6371 * acos(
                           LEAST(1.0, cos(radians(%s)) * cos(radians(lat)) *
                           cos(radians(lng) - radians(%s)) +
                           sin(radians(%s)) * sin(radians(lat)))
                       )) AS straight_km
                FROM {TABLE_NAME}
                WHERE category = %s
                  AND status != 'INVALID'
                  AND lat BETWEEN %s - %s AND %s + %s
                  AND lng BETWEEN %s - %s AND %s + %s
                ORDER BY straight_km ASC
                LIMIT 3
            """
            cursor.execute(sql, (
                lat, lng, lat, category,
                lat, lat_range, lat, lat_range,
                lng, lng_range, lng, lng_range
            ))
            candidates = cursor.fetchall()
    finally:
        conn.close()

    if not candidates:
        raise HTTPException(status_code=404, detail=f"주변에 이용 가능한 {category} 시설이 없습니다.")

    headers = {"appKey": TMAP_APP_KEY, "Accept": "application/json", "Content-Type": "application/json"}
    best_route = None

    if TMAP_APP_KEY:
        for item in candidates:
            try:
                # 1) 대중교통 경로 계산
                if mode == "transit":
                    payload = {
                        "startX": str(float(lng)),
                        "startY": str(float(lat)),
                        "endX": str(float(item["lng"])),
                        "endY": str(float(item["lat"])),
                        "count": 1,
                        "lang": 0,
                        "format": "json"
                    }
                    res = requests.post(
                        "https://apis.openapi.sk.com/transit/routes",
                        json=payload, headers=headers, timeout=5
                    )
                    if res.status_code != 200:
                        print("[TMAP 대중교통 오류]", res.status_code, res.text)
                        continue

                    itineraries = res.json().get("metaData", {}).get("plan", {}).get("itineraries", [])
                    if not itineraries:
                        continue

                    itin = itineraries[0]
                    total_time = itin.get("totalTime", 0)
                    total_distance = itin.get("totalDistance", 999999)
                    fare = itin.get("fare", {}).get("regular", {}).get("totalFare", 0)

                    steps = [f"💳 예상 요금: {fare:,}원"] if fare else []
                    path_coords = []

                    for leg in itin.get("legs", []):
                        leg_mode = leg.get("mode", "")
                        route_name = leg.get("route", "")
                        s_name = leg.get("start", {}).get("name", "")
                        e_name = leg.get("end", {}).get("name", "")
                        sec = max(1, round(leg.get("sectionTime", 0) / 60))

                        if leg_mode == "WALK":
                            steps.append(f"🚶 도보 이동 ({sec}분)")
                        else:
                            steps.append(f"🚌 [{route_name}] {s_name} → {e_name} ({sec}분)")

                        # 대중교통 및 도보 구간 좌표 추출
                        linestring = leg.get("passShape", {}).get("linestring", "")
                        if linestring:
                            for pair in linestring.split(" "):
                                if "," in pair:
                                    x_str, y_str = pair.split(",")
                                    path_coords.append({"lat": float(y_str), "lng": float(x_str)})
                        else:
                            for st in leg.get("steps", []):
                                for pair in st.get("linestring", "").split(" "):
                                    if "," in pair:
                                        x_str, y_str = pair.split(",")
                                        path_coords.append({"lat": float(y_str), "lng": float(x_str)})

                    if best_route is None or total_time < best_route["duration"]:
                        best_route = {
                            "mode": "transit",
                            "target": item,
                            "distance": total_distance,
                            "duration": total_time,
                            "path": path_coords or [{"lat": lat, "lng": lng}, {"lat": float(item["lat"]), "lng": float(item["lng"])}],
                            "steps": steps,
                            "is_fallback": False
                        }

                # 2) 도보 또는 자동차 경로 계산
                else:
                    tmap_url = (
                        "https://apis.openapi.sk.com/tmap/routes?version=1"
                        if mode == "car" else
                        "https://apis.openapi.sk.com/tmap/routes/pedestrian?version=1"
                    )
                    payload = {
                        "startX": float(lng),
                        "startY": float(lat),
                        "endX": float(item["lng"]),
                        "endY": float(item["lat"]),
                        "reqCoordType": "WGS84GEO",
                        "resCoordType": "WGS84GEO",
                        "startName": "start",
                        "endName": "end"
                    }
                    res = requests.post(tmap_url, json=payload, headers=headers, timeout=5)
                    if res.status_code != 200:
                        print("[TMAP 경로 오류]", res.status_code, res.text)
                        continue

                    features = res.json().get("features", [])
                    if not features:
                        continue

                    props = features[0].get("properties", {})
                    total_distance = props.get("totalDistance", 999999)
                    total_time = props.get("totalTime", 0)

                    path_coords = []
                    steps = []
                    for feat in features:
                        geom = feat.get("geometry", {})
                        f_props = feat.get("properties", {})
                        if geom.get("type") == "LineString":
                            for coord in geom.get("coordinates", []):
                                path_coords.append({"lat": coord[1], "lng": coord[0]})
                        elif geom.get("type") == "Point":
                            desc = f_props.get("description", "").strip()
                            if desc and desc not in ("출발", "도착"):
                                steps.append(desc)

                    if best_route is None or total_distance < best_route["distance"]:
                        best_route = {
                            "mode": mode,
                            "target": item,
                            "distance": total_distance,
                            "duration": total_time,
                            "path": path_coords,
                            "steps": steps,
                            "is_fallback": False
                        }
            except Exception as e:
                print("[TMAP 예외]", e)
                continue

    if not best_route:
        top = candidates[0]
        dist_m = int(top["straight_km"] * 1000)
        best_route = {
            "mode": mode,
            "target": top,
            "distance": dist_m,
            "duration": int(dist_m / (11.0 if mode == "car" else 1.2)),
            "path": [{"lat": lat, "lng": lng}, {"lat": float(top["lat"]), "lng": float(top["lng"])}],
            "steps": ["상세 경로 미지원 구간입니다. 아래 '카카오맵 길찾기' 버튼을 이용해 주세요."],
            "is_fallback": True
        }

    return best_route

    # TMAP 미지원 지역이거나 키 미설정 시 직선거리 1위 시설로 안전하게 대체
    if not best_route:
        top = candidates[0]
        dist_m = int(top["straight_km"] * 1000)
        best_route = {
            "mode": mode,
            "target": top,
            "distance": dist_m,
            "duration": int(dist_m / (11.0 if is_car else 1.2)),
            "path": [{"lat": lat, "lng": lng}, {"lat": top["lat"], "lng": top["lng"]}],
            "is_fallback": True
        }

    return best_route

# 프론트엔드 정적 파일 연결 (반드시 API 라우트 아래에 위치)
app.mount("/", StaticFiles(directory="static", html=True), name="static")