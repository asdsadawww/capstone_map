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
    "password": os.getenv("DB_PASSWORD", ""),  # 호스팅 설정창에서 입력할 예정
    "database": "map_db",
    "charset": "utf8mb4",
    "ssl": {"ca": None},
    "cursorclass": pymysql.cursors.DictCursor,
}

# 방금 DESCRIBE 했던 실제 테이블 이름으로 맞추세요
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

# 프론트엔드 정적 파일 연결 (반드시 API 라우트 아래에 위치)
app.mount("/", StaticFiles(directory="static", html=True), name="static")