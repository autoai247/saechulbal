"""
새출발 - 회생/파산 상담 중개 플랫폼
1건의 상담 신청 → 여러 업체에 중개 (건당 과금)
"""

from fastapi import FastAPI, Request, Depends, HTTPException, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime, timedelta
import os
import jwt
import bcrypt
import uuid

app = FastAPI(title="새출발", description="회생/파산 상담 중개 플랫폼")

# Static & Templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ============================================================
# Config
# ============================================================
SECRET_KEY = os.getenv("SECRET_KEY", "saechulbal-secret-key-change-in-prod")
ADMIN_ID = os.getenv("ADMIN_ID", "admin")
ADMIN_PW = os.getenv("ADMIN_PW", "admin")
LEAD_PRICE = int(os.getenv("LEAD_PRICE", "5000"))  # 건당 열람 가격 (원)

# ============================================================
# 채무 유형 상수
# ============================================================
DEBT_TYPES = {
    "personal_recovery": "개인회생",
    "personal_bankruptcy": "개인파산",
    "corporate_recovery": "기업회생",
    "corporate_bankruptcy": "기업파산",
}

REGIONS = [
    "서울", "경기", "인천", "부산", "대구", "대전", "광주",
    "울산", "세종", "강원", "충북", "충남", "전북", "전남",
    "경북", "경남", "제주",
]

DEBT_RANGES = [
    "1천만원 미만",
    "1천만원 ~ 3천만원",
    "3천만원 ~ 5천만원",
    "5천만원 ~ 1억원",
    "1억원 ~ 3억원",
    "3억원 ~ 5억원",
    "5억원 이상",
]


# ============================================================
# In-memory DB (Supabase 연동 전 프로토타입)
# ============================================================
# 실제 운영 시 database.py로 분리하여 Supabase 연동
applications_db: list[dict] = []  # 상담 신청 목록
companies_db: list[dict] = []     # 업체 목록
company_users_db: list[dict] = [] # 업체 계정
distributions_db: list[dict] = [] # 중개 기록 (신청 → 업체 매칭)
purchases_db: list[dict] = []     # 열람(구매) 기록


# ============================================================
# Auth Helpers
# ============================================================
def create_token(data: dict, expires_hours: int = 24) -> str:
    payload = {**data, "exp": datetime.utcnow() + timedelta(hours=expires_hours)}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def verify_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def get_admin(request: Request) -> dict | None:
    token = request.cookies.get("admin_token")
    if not token:
        return None
    return verify_token(token)


def get_company_user(request: Request) -> dict | None:
    token = request.cookies.get("company_token")
    if not token:
        return None
    payload = verify_token(token)
    if not payload:
        return None
    return next((u for u in company_users_db if u["id"] == payload.get("user_id")), None)


# ============================================================
# 공개 페이지 (신청인용)
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("home.html", {
        "request": request,
        "debt_types": DEBT_TYPES,
        "regions": REGIONS,
        "debt_ranges": DEBT_RANGES,
    })


@app.post("/apply", response_class=HTMLResponse)
async def apply(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    debt_type: str = Form(...),
    region: str = Form(...),
    debt_amount: str = Form(...),
    max_companies: int = Form(3),
    description: str = Form(""),
):
    """상담 신청 접수"""
    if max_companies not in (3, 5, 7):
        max_companies = 3

    application = {
        "id": str(uuid.uuid4()),
        "name": name,
        "phone": phone,
        "debt_type": debt_type,
        "debt_type_label": DEBT_TYPES.get(debt_type, debt_type),
        "region": region,
        "debt_amount": debt_amount,
        "max_companies": max_companies,
        "description": description,
        "status": "pending",  # pending → distributed → contacted → completed
        "created_at": datetime.now().isoformat(),
    }
    applications_db.append(application)

    # 자동 배분: 필터 매칭되는 업체에게 배분
    _distribute_to_companies(application)

    return templates.TemplateResponse("apply_complete.html", {
        "request": request,
        "application": application,
    })


def _distribute_to_companies(application: dict):
    """신청건을 필터 매칭되는 업체들에게 자동 배분"""
    for company in companies_db:
        if company["status"] != "active":
            continue

        # 필터 체크
        filters = company.get("filters", {})
        if filters.get("debt_types") and application["debt_type"] not in filters["debt_types"]:
            continue
        if filters.get("regions") and application["region"] not in filters["regions"]:
            continue

        distribution = {
            "id": str(uuid.uuid4()),
            "application_id": application["id"],
            "company_id": company["id"],
            "status": "notified",  # notified → viewed → purchased → contacted
            "created_at": datetime.now().isoformat(),
        }
        distributions_db.append(distribution)

    application["status"] = "distributed"


# ============================================================
# 업체 리스트 (공개 - 신청인이 업체 탐색)
# ============================================================
@app.get("/companies", response_class=HTMLResponse)
async def company_list(
    request: Request,
    region: str = None,
    debt_type: str = None,
    sort: str = "rating",  # rating, review, recent
):
    """업체 리스트 (필터링 + 정렬)"""
    filtered = [c for c in companies_db if c["status"] == "active"]

    # 지역 필터
    if region:
        filtered = [c for c in filtered if not c["filters"].get("regions") or region in c["filters"]["regions"]]

    # 채무유형 필터
    if debt_type:
        filtered = [c for c in filtered if not c["filters"].get("debt_types") or debt_type in c["filters"]["debt_types"]]

    # 정렬
    if sort == "rating":
        filtered.sort(key=lambda c: c.get("rating", 0), reverse=True)
    elif sort == "review":
        filtered.sort(key=lambda c: c.get("review_count", 0), reverse=True)
    elif sort == "recent":
        filtered.sort(key=lambda c: c.get("created_at", ""), reverse=True)

    return templates.TemplateResponse("company_list.html", {
        "request": request,
        "companies": filtered,
        "debt_types": DEBT_TYPES,
        "regions": REGIONS,
        "selected_region": region,
        "selected_debt_type": debt_type,
        "selected_sort": sort,
        "total_count": len(filtered),
    })


@app.get("/companies/{company_id}", response_class=HTMLResponse)
async def company_detail(request: Request, company_id: str):
    """업체 상세 페이지"""
    company = next((c for c in companies_db if c["id"] == company_id and c["status"] == "active"), None)
    if not company:
        return RedirectResponse("/companies")

    # 이 업체의 전문 분야 라벨
    specialty_labels = [DEBT_TYPES[dt] for dt in company["filters"].get("debt_types", []) if dt in DEBT_TYPES]
    region_labels = company["filters"].get("regions", [])

    return templates.TemplateResponse("company_detail.html", {
        "request": request,
        "company": company,
        "specialty_labels": specialty_labels,
        "region_labels": region_labels,
        "debt_types": DEBT_TYPES,
        "regions": REGIONS,
        "debt_ranges": DEBT_RANGES,
    })


# ============================================================
# 업체 페이지
# ============================================================
@app.get("/company/login", response_class=HTMLResponse)
async def company_login_page(request: Request):
    return templates.TemplateResponse("company_login.html", {"request": request})


@app.post("/company/login")
async def company_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    user = next((u for u in company_users_db if u["email"] == email), None)
    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"]):
        return templates.TemplateResponse("company_login.html", {
            "request": request, "error": "이메일 또는 비밀번호가 올바르지 않습니다."
        })
    token = create_token({"user_id": user["id"], "company_id": user["company_id"], "role": "company"})
    response = RedirectResponse("/company/dashboard", status_code=303)
    response.set_cookie("company_token", token, httponly=True, max_age=86400)
    return response


@app.get("/company/register", response_class=HTMLResponse)
async def company_register_page(request: Request):
    return templates.TemplateResponse("company_register.html", {
        "request": request,
        "debt_types": DEBT_TYPES,
        "regions": REGIONS,
    })


@app.post("/company/register")
async def company_register(
    request: Request,
    company_name: str = Form(...),
    business_number: str = Form(...),
    contact_name: str = Form(...),
    contact_phone: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    debt_types: list[str] = Form([]),
    regions: list[str] = Form([]),
):
    """업체 회원가입"""
    if any(u["email"] == email for u in company_users_db):
        return templates.TemplateResponse("company_register.html", {
            "request": request, "error": "이미 등록된 이메일입니다.",
            "debt_types": DEBT_TYPES, "regions": REGIONS,
        })

    company = {
        "id": str(uuid.uuid4()),
        "name": company_name,
        "business_number": business_number,
        "contact_name": contact_name,
        "contact_phone": contact_phone,
        "status": "pending",  # pending → active → suspended
        "filters": {
            "debt_types": debt_types if debt_types else [],
            "regions": regions if regions else [],
        },
        "description": "",  # 업체 소개 (프로필에서 수정)
        "min_fee": "",       # 최소 수임료
        "experience_years": "",  # 경력 연수
        "success_count": 0,  # 성공 사례 수
        "rating": 0.0,       # 평점
        "review_count": 0,   # 리뷰 수
        "balance": 0,  # 충전 잔액 (원)
        "created_at": datetime.now().isoformat(),
    }
    companies_db.append(company)

    user = {
        "id": str(uuid.uuid4()),
        "company_id": company["id"],
        "email": email,
        "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()),
        "created_at": datetime.now().isoformat(),
    }
    company_users_db.append(user)

    return templates.TemplateResponse("company_register_complete.html", {
        "request": request, "company": company,
    })


@app.get("/company/dashboard", response_class=HTMLResponse)
async def company_dashboard(request: Request):
    user = get_company_user(request)
    if not user:
        return RedirectResponse("/company/login")

    company = next((c for c in companies_db if c["id"] == user["company_id"]), None)
    if not company:
        return RedirectResponse("/company/login")

    # 이 업체에 배분된 신청건 목록
    my_distributions = [d for d in distributions_db if d["company_id"] == company["id"]]
    leads = []
    for dist in my_distributions:
        app_data = next((a for a in applications_db if a["id"] == dist["application_id"]), None)
        if app_data:
            purchased = any(
                p["distribution_id"] == dist["id"] for p in purchases_db
            )
            current_purchases = sum(1 for p in purchases_db if p["application_id"] == app_data["id"])
            max_companies = app_data.get("max_companies", 3)
            sold_out = current_purchases >= max_companies
            leads.append({
                **app_data,
                "distribution_id": dist["id"],
                "purchased": purchased,
                "sold_out": sold_out,
                "dist_status": dist["status"],
            })

    return templates.TemplateResponse("company_dashboard.html", {
        "request": request,
        "company": company,
        "leads": leads,
        "lead_price": LEAD_PRICE,
    })


@app.post("/company/purchase/{distribution_id}")
async def purchase_lead(request: Request, distribution_id: str):
    """DB 열람 구매 (개인정보 공개)"""
    user = get_company_user(request)
    if not user:
        return JSONResponse({"error": "로그인 필요"}, status_code=401)

    company = next((c for c in companies_db if c["id"] == user["company_id"]), None)
    dist = next((d for d in distributions_db if d["id"] == distribution_id), None)
    if not company or not dist or dist["company_id"] != company["id"]:
        return JSONResponse({"error": "권한 없음"}, status_code=403)

    # 이미 구매했는지 확인
    if any(p["distribution_id"] == distribution_id for p in purchases_db):
        return JSONResponse({"error": "이미 구매한 건입니다."}, status_code=400)

    # 선착순 제한: 신청자가 선택한 업체 수만큼만 판매
    app_data = next((a for a in applications_db if a["id"] == dist["application_id"]), None)
    max_companies = app_data.get("max_companies", 3) if app_data else 3
    current_purchases = sum(1 for p in purchases_db if p["application_id"] == dist["application_id"])
    if current_purchases >= max_companies:
        return JSONResponse({"error": "이 건은 마감되었습니다. (최대 열람 수 초과)"}, status_code=400)

    # 잔액 확인
    if company["balance"] < LEAD_PRICE:
        return JSONResponse({"error": f"잔액이 부족합니다. (현재: {company['balance']:,}원, 필요: {LEAD_PRICE:,}원)"}, status_code=400)

    # 차감 & 구매 기록
    company["balance"] -= LEAD_PRICE
    purchase = {
        "id": str(uuid.uuid4()),
        "distribution_id": distribution_id,
        "company_id": company["id"],
        "application_id": dist["application_id"],
        "price": LEAD_PRICE,
        "created_at": datetime.now().isoformat(),
    }
    purchases_db.append(purchase)
    dist["status"] = "purchased"

    # 신청인 정보 반환 (app_data는 위에서 이미 조회됨)
    return JSONResponse({
        "success": True,
        "name": app_data["name"],
        "phone": app_data["phone"],
        "description": app_data["description"],
    })


@app.post("/company/logout")
async def company_logout():
    response = RedirectResponse("/company/login", status_code=303)
    response.delete_cookie("company_token")
    return response


# ============================================================
# 관리자 페이지
# ============================================================
@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    return templates.TemplateResponse("admin_login.html", {"request": request})


@app.post("/admin/login")
async def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if username != ADMIN_ID or password != ADMIN_PW:
        return templates.TemplateResponse("admin_login.html", {
            "request": request, "error": "아이디 또는 비밀번호가 올바르지 않습니다."
        })
    token = create_token({"role": "admin"})
    response = RedirectResponse("/admin/dashboard", status_code=303)
    response.set_cookie("admin_token", token, httponly=True, max_age=86400)
    return response


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    admin = get_admin(request)
    if not admin:
        return RedirectResponse("/admin/login")

    total_apps = len(applications_db)
    total_companies = len(companies_db)
    total_purchases = len(purchases_db)
    total_revenue = sum(p["price"] for p in purchases_db)

    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "stats": {
            "total_apps": total_apps,
            "total_companies": total_companies,
            "total_purchases": total_purchases,
            "total_revenue": total_revenue,
        },
        "applications": applications_db[-20:],  # 최근 20건
        "companies": companies_db,
    })


@app.post("/admin/company/{company_id}/approve")
async def approve_company(request: Request, company_id: str):
    admin = get_admin(request)
    if not admin:
        return JSONResponse({"error": "권한 없음"}, status_code=401)

    company = next((c for c in companies_db if c["id"] == company_id), None)
    if not company:
        return JSONResponse({"error": "업체를 찾을 수 없습니다."}, status_code=404)

    company["status"] = "active"
    return JSONResponse({"success": True})


@app.post("/admin/company/{company_id}/charge")
async def charge_company(request: Request, company_id: str, amount: int = Form(...)):
    """업체 잔액 충전 (관리자)"""
    admin = get_admin(request)
    if not admin:
        return JSONResponse({"error": "권한 없음"}, status_code=401)

    company = next((c for c in companies_db if c["id"] == company_id), None)
    if not company:
        return JSONResponse({"error": "업체를 찾을 수 없습니다."}, status_code=404)

    company["balance"] += amount
    return JSONResponse({"success": True, "new_balance": company["balance"]})


@app.post("/admin/logout")
async def admin_logout():
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie("admin_token")
    return response


# ============================================================
# API (향후 확장용)
# ============================================================
@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "새출발", "timestamp": datetime.now().isoformat()}
