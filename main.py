"""
새출발 - 회생/파산 상담 중개 플랫폼
양방향 매칭: 신청인이 업체를 선택 → 업체가 열람(구매)

# === 매우 보수적 수익 시뮬레이션 ===
# 월 100건 신청 (광고 최소 집행 가정)
# 평균 2곳 선택 (신청인이 보수적으로 선택)
# 열람 전환율 70% (알림 받고 결제까지)
# 평균 단가 3,600원 (할인 반영)
# 월 수익 = 100 × 2 × 0.7 × 3,600 = 504,000원
#
# 6개월 후 목표 (월 500건, 80개 업체)
# 월 수익 = 500 × 2.5 × 0.75 × 3,500 = 3,281,250원
#
# 1년 후 목표 (월 1,500건, 200개 업체)
# 월 수익 = 1,500 × 3 × 0.8 × 3,400 = 12,240,000원
"""

from fastapi import FastAPI, Request, Depends, HTTPException, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
import asyncio
import threading
import os
import jwt
import bcrypt
import uuid

app = FastAPI(title="새출발", description="회생/파산 상담 중개 플랫폼 (양방향 매칭)")

# Static & Templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ============================================================
# Config
# ============================================================
SECRET_KEY = os.getenv("SECRET_KEY", "saechulbal-secret-key-change-in-prod")
ADMIN_ID = os.getenv("ADMIN_ID", "admin")
ADMIN_PW = os.getenv("ADMIN_PW", "admin")

# 선택 업체 수에 따른 건당 가격
PRICE_BY_SELECTION = {
    1: 5000,   # 1곳만 선택 → 5,000원
    2: 4000,   # 2곳 선택 → 4,000원
    3: 3500,   # 3곳 선택 → 3,500원
    4: 3000,   # 4곳 선택 → 3,000원
    5: 2500,   # 5곳 선택 → 2,500원
}

# ============================================================
# 채무 유형 상수
# ============================================================
DEBT_TYPES = {
    "personal_recovery": "개인회생",
    "personal_bankruptcy": "개인파산",
    "corporate_recovery": "기업회생",
    "corporate_bankruptcy": "기업파산",
}

# 업체 등급 시스템 — 할인 전용 (리스팅 우선순위 아님)
COMPANY_TIERS = {
    "free":    {"label": "무료",      "monthly_fee": 0,      "badge": "",         "discount": 0},
    "premium": {"label": "프리미엄",  "monthly_fee": 30000,  "badge": "인증 업체",  "discount": 0.10},
    "vip":     {"label": "VIP",       "monthly_fee": 50000,  "badge": "우수 업체",  "discount": 0.20},
    "diamond": {"label": "다이아몬드", "monthly_fee": 100000, "badge": "최우수 업체", "discount": 0.30},
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
applications_db: list[dict] = []      # 상담 신청 목록
companies_db: list[dict] = []         # 업체 목록
company_users_db: list[dict] = []     # 업체 계정
distributions_db: list[dict] = []     # 중개 기록 (신청인이 선택한 업체)
purchases_db: list[dict] = []         # 열람(구매) 기록
inquiries_db: list[dict] = []        # 업체 입점 문의
transactions_db: list[dict] = []     # 포인트 거래 내역
point_requests_db: list[dict] = []   # 포인트 충전 요청
refund_requests_db: list[dict] = []  # 환불 요청


# ============================================================
# 크롤링 업체 데이터 로드 + 샘플 보강
# ============================================================
import random
import json as _json
import os as _os
import re as _re

random.seed(42)

# 크롤링 데이터 로드
_crawled_path = _os.path.join(_os.path.dirname(__file__) or ".", "crawled_companies.json")
if _os.path.exists(_crawled_path):
    with open(_crawled_path, "r", encoding="utf-8") as _f:
        _crawled = _json.load(_f)
    for _c in _crawled:
        # 이름 정리: 톡톡, 쿠폰, 법률사무소/법무사사무소 꼬리 제거
        _name = _c.get("name", "")
        _name = _re.sub(r"(톡톡|쿠폰|법률사무소$|법무사사무소$)", "", _name).strip()
        _c["name"] = _name
        _c["tier"] = "free"
        _c["verified"] = False
        _c["status"] = "listed"
        _c["response_rate"] = 0.0  # 응답률 (0~1)
        if not _c.get("description"):
            _c["description"] = ""
        if not _c.get("rating"):
            _c["rating"] = 0.0
        if not _c.get("review_count"):
            _c["review_count"] = 0
        if not _c.get("success_count"):
            _c["success_count"] = 0
        if not _c.get("experience_years"):
            _c["experience_years"] = ""
        if not _c.get("min_fee"):
            _c["min_fee"] = ""
        if not _c.get("balance"):
            _c["balance"] = 0
    companies_db.extend(_crawled)
    print(f"[새출발] 크롤링 업체 {len(_crawled)}개 로드됨")
else:
    print("[새출발] crawled_companies.json 없음 - 업체 데이터가 없습니다")


# ============================================================
# Helper: 업체 리스팅 정렬 점수 계산
# ============================================================
def _company_sort_score(company: dict) -> float:
    """
    리스팅 순서: rating(40%) + response_rate(30%) + tier(30%)
    Free tier도 평점/응답률이 좋으면 상위에 올 수 있음
    """
    rating = company.get("rating", 0.0)
    response_rate = company.get("response_rate", 0.0)
    tier = company.get("tier", "free")
    tier_scores = {"free": 0, "premium": 0.33, "vip": 0.67, "diamond": 1.0}
    tier_score = tier_scores.get(tier, 0)

    # rating: 0~5 → 0~1 정규화
    norm_rating = min(rating / 5.0, 1.0) if rating else 0
    norm_response = min(response_rate, 1.0)

    return norm_rating * 0.4 + norm_response * 0.3 + tier_score * 0.3


def _get_lead_price(num_selected: int, company: dict) -> int:
    """선택 업체 수 기반 가격 + 등급 할인 적용"""
    base_price = PRICE_BY_SELECTION.get(num_selected, PRICE_BY_SELECTION[5])
    tier = company.get("tier", "free")
    discount = COMPANY_TIERS.get(tier, COMPANY_TIERS["free"])["discount"]
    discounted = int(base_price * (1 - discount))
    return discounted


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
    # 정렬: sort_score (rating 40% + response_rate 30% + tier 30%)
    top_companies = sorted(
        [c for c in companies_db if c["status"] in ("active", "listed")],
        key=_company_sort_score,
        reverse=True,
    )[:12]
    stats = {
        "total_companies": len([c for c in companies_db if c["status"] in ("active", "listed")]),
        "total_apps": len(applications_db),
        "total_purchases": len(purchases_db),
        "total_success": sum(1 for a in applications_db if a.get("status") == "completed"),
    }
    return templates.TemplateResponse("home.html", {
        "request": request,
        "debt_types": DEBT_TYPES,
        "regions": REGIONS,
        "debt_ranges": DEBT_RANGES,
        "companies": top_companies,
        "tiers": COMPANY_TIERS,
        "stats": stats,
    })


# ============================================================
# 신청 Flow: Step 1 - 신청 폼
# ============================================================
@app.get("/apply", response_class=HTMLResponse)
async def apply_form(request: Request):
    """상담 신청 폼 (개인정보 동의 포함)"""
    return templates.TemplateResponse("apply_form.html", {
        "request": request,
        "debt_types": DEBT_TYPES,
        "regions": REGIONS,
        "debt_ranges": DEBT_RANGES,
    })


# ============================================================
# 신청 Flow: Step 2 - 신청 접수 → 업체 선택 페이지로 리다이렉트
# ============================================================
@app.post("/apply", response_class=HTMLResponse)
async def apply_submit(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    debt_type: str = Form(...),
    region: str = Form(...),
    debt_amount: str = Form(...),
    description: str = Form(""),
    privacy_consent: bool = Form(False),
    marketing_consent: bool = Form(False),
):
    """상담 신청 접수 → 업체 선택 페이지로 이동"""
    if not privacy_consent:
        return templates.TemplateResponse("apply_form.html", {
            "request": request,
            "error": "개인정보 수집·이용에 동의해주세요.",
            "debt_types": DEBT_TYPES,
            "regions": REGIONS,
            "debt_ranges": DEBT_RANGES,
        })

    application = {
        "id": str(uuid.uuid4()),
        "name": name,
        "phone": phone,
        "debt_type": debt_type,
        "debt_type_label": DEBT_TYPES.get(debt_type, debt_type),
        "region": region,
        "debt_amount": debt_amount,
        "description": description,
        "privacy_consent": True,          # 수집·이용 동의 (필수)
        "third_party_consent": False,     # 제3자 제공 동의 (업체 선택 시 자동)
        "marketing_consent": marketing_consent,  # 마케팅 동의 (선택)
        "selected_company_ids": [],       # 신청인이 선택한 업체 목록
        "num_selected": 0,                # 선택한 업체 수
        "status": "pending",  # pending → selecting → distributed → contacted → completed
        "created_at": datetime.now().isoformat(),
    }
    applications_db.append(application)

    # 업체 선택 페이지로 리다이렉트
    return RedirectResponse(f"/apply/{application['id']}/select", status_code=303)


# ============================================================
# 신청 Flow: Step 3 - 매칭 업체 표시 (신청인이 업체 선택)
# ============================================================
@app.get("/apply/{app_id}/select", response_class=HTMLResponse)
async def apply_select_companies(request: Request, app_id: str):
    """매칭되는 업체 목록 표시 - 신청인이 1~5곳 선택"""
    app_data = next((a for a in applications_db if a["id"] == app_id), None)
    if not app_data:
        return RedirectResponse("/apply")

    # 이미 선택 완료된 신청건
    if app_data["status"] not in ("pending", "selecting"):
        return templates.TemplateResponse("apply_complete.html", {
            "request": request,
            "application": app_data,
        })

    app_data["status"] = "selecting"

    # 매칭 업체 찾기: 지역 + 채무유형 필터
    matching = []
    for company in companies_db:
        if company["status"] not in ("active", "listed"):
            continue

        filters = company.get("filters", {})
        # 지역 필터: 업체가 지역 필터를 설정한 경우 매칭 확인, 미설정이면 전체
        if filters.get("regions") and app_data["region"] not in filters["regions"]:
            continue
        # 채무유형 필터
        if filters.get("debt_types") and app_data["debt_type"] not in filters["debt_types"]:
            continue

        matching.append(company)

    # 정렬: sort_score (rating 40% + response_rate 30% + tier 30%)
    matching.sort(key=_company_sort_score, reverse=True)

    # 가격 정보 계산 (1~5곳 선택 시 각각의 가격)
    price_info = {}
    for n in range(1, 6):
        price_info[n] = PRICE_BY_SELECTION[n]

    return templates.TemplateResponse("apply_select.html", {
        "request": request,
        "application": app_data,
        "companies": matching,
        "tiers": COMPANY_TIERS,
        "debt_types": DEBT_TYPES,
        "price_info": price_info,
        "max_select": 5,
    })


# ============================================================
# 신청 Flow: Step 4 - 선택 확정 → 배분 생성
# ============================================================
@app.post("/apply/{app_id}/select", response_class=HTMLResponse)
async def apply_confirm_selection(
    request: Request,
    app_id: str,
    selected_companies: list[str] = Form([]),
):
    """신청인이 선택한 업체들에 대해 배분 생성"""
    app_data = next((a for a in applications_db if a["id"] == app_id), None)
    if not app_data:
        return RedirectResponse("/apply")

    if app_data["status"] not in ("pending", "selecting"):
        return templates.TemplateResponse("apply_complete.html", {
            "request": request,
            "application": app_data,
        })

    # 유효성 검사
    if not selected_companies or len(selected_companies) < 1:
        return RedirectResponse(f"/apply/{app_id}/select", status_code=303)

    if len(selected_companies) > 5:
        selected_companies = selected_companies[:5]

    # 선택한 업체들이 실제 존재하는지 확인
    valid_ids = []
    for cid in selected_companies:
        company = next((c for c in companies_db if c["id"] == cid and c["status"] in ("active", "listed")), None)
        if company:
            valid_ids.append(cid)

    if not valid_ids:
        return RedirectResponse(f"/apply/{app_id}/select", status_code=303)

    # 신청 데이터 업데이트
    app_data["selected_company_ids"] = valid_ids
    app_data["num_selected"] = len(valid_ids)
    app_data["third_party_consent"] = True  # 업체 선택 = 제3자 제공 동의
    app_data["status"] = "distributed"

    # 선택된 업체들에 대해 배분(distribution) 생성
    for cid in valid_ids:
        distribution = {
            "id": str(uuid.uuid4()),
            "application_id": app_data["id"],
            "company_id": cid,
            "status": "notified",  # notified → viewed → purchased → contacted
            "price": _get_lead_price(len(valid_ids), next(c for c in companies_db if c["id"] == cid)),
            "created_at": datetime.now().isoformat(),
        }
        distributions_db.append(distribution)

    return templates.TemplateResponse("apply_complete.html", {
        "request": request,
        "application": app_data,
        "selected_count": len(valid_ids),
    })


# ============================================================
# 직접 신청 (특정 업체 1곳 지정)
# ============================================================
@app.post("/apply/direct", response_class=HTMLResponse)
async def apply_direct(
    request: Request,
    company_id: str = Form(...),
    name: str = Form(...),
    phone: str = Form(...),
    debt_type: str = Form(...),
    region: str = Form(...),
    debt_amount: str = Form(...),
    description: str = Form(""),
    privacy_consent: bool = Form(False),
):
    """특정 업체에 직접 상담 신청 (1곳 선택 = 가장 높은 단가)"""
    if not privacy_consent:
        return RedirectResponse(f"/companies/{company_id}", status_code=303)

    target_company = next((c for c in companies_db if c["id"] == company_id and c["status"] in ("active", "listed")), None)
    if not target_company:
        return RedirectResponse("/companies")

    application = {
        "id": str(uuid.uuid4()),
        "name": name,
        "phone": phone,
        "debt_type": debt_type,
        "debt_type_label": DEBT_TYPES.get(debt_type, debt_type),
        "region": region,
        "debt_amount": debt_amount,
        "description": description,
        "privacy_consent": True,
        "third_party_consent": True,  # 직접 선택 = 동의
        "marketing_consent": False,
        "selected_company_ids": [company_id],
        "num_selected": 1,
        "direct_company_id": company_id,
        "direct_company_name": target_company["name"],
        "status": "distributed",
        "created_at": datetime.now().isoformat(),
    }
    applications_db.append(application)

    # 직접 선택 → 바로 배분 생성 (1곳이므로 5,000원)
    distribution = {
        "id": str(uuid.uuid4()),
        "application_id": application["id"],
        "company_id": company_id,
        "status": "notified",
        "price": _get_lead_price(1, target_company),
        "created_at": datetime.now().isoformat(),
    }
    distributions_db.append(distribution)

    return templates.TemplateResponse("apply_complete.html", {
        "request": request,
        "application": application,
        "selected_count": 1,
    })


# ============================================================
# 업체 리스트 (공개 - 신청인이 업체 탐색)
# ============================================================
@app.get("/companies", response_class=HTMLResponse)
async def company_list(
    request: Request,
    region: str = None,
    debt_type: str = None,
    sort: str = "score",  # score, rating, review, recent
):
    """업체 리스트 (필터링 + 정렬)"""
    filtered = [c for c in companies_db if c["status"] in ("active", "listed")]

    # 지역 필터
    if region:
        filtered = [c for c in filtered if not c.get("filters", {}).get("regions") or region in c["filters"]["regions"]]

    # 채무유형 필터
    if debt_type:
        filtered = [c for c in filtered if not c.get("filters", {}).get("debt_types") or debt_type in c["filters"]["debt_types"]]

    # 정렬
    if sort == "rating":
        filtered.sort(key=lambda c: c.get("rating", 0), reverse=True)
    elif sort == "review":
        filtered.sort(key=lambda c: c.get("review_count", 0), reverse=True)
    elif sort == "recent":
        filtered.sort(key=lambda c: c.get("created_at", ""), reverse=True)
    else:
        # 기본: 종합점수 (rating 40% + response_rate 30% + tier 30%)
        filtered.sort(key=_company_sort_score, reverse=True)

    return templates.TemplateResponse("company_list.html", {
        "request": request,
        "companies": filtered,
        "debt_types": DEBT_TYPES,
        "regions": REGIONS,
        "tiers": COMPANY_TIERS,
        "selected_region": region,
        "selected_debt_type": debt_type,
        "selected_sort": sort,
        "total_count": len(filtered),
    })


@app.get("/companies/{company_id}", response_class=HTMLResponse)
async def company_detail(request: Request, company_id: str):
    """업체 상세 페이지"""
    company = next((c for c in companies_db if c["id"] == company_id and c["status"] in ("active", "listed")), None)
    if not company:
        return RedirectResponse("/companies")

    # 이 업체의 전문 분야 라벨
    specialty_labels = [DEBT_TYPES[dt] for dt in company.get("filters", {}).get("debt_types", []) if dt in DEBT_TYPES]
    region_labels = company.get("filters", {}).get("regions", [])

    tier = COMPANY_TIERS.get(company.get("tier", "free"), COMPANY_TIERS["free"])
    return templates.TemplateResponse("company_detail.html", {
        "request": request,
        "company": company,
        "tier": tier,
        "specialty_labels": specialty_labels,
        "region_labels": region_labels,
        "debt_types": DEBT_TYPES,
        "regions": REGIONS,
        "debt_ranges": DEBT_RANGES,
    })


# ============================================================
# 업체 입점 문의
# ============================================================
@app.get("/company/inquiry", response_class=HTMLResponse)
async def company_inquiry_page(request: Request):
    return templates.TemplateResponse("company_inquiry.html", {
        "request": request, "success": False,
    })


@app.post("/company/inquiry", response_class=HTMLResponse)
async def company_inquiry_submit(
    request: Request,
    company_name: str = Form(...),
    contact_name: str = Form(...),
    contact_phone: str = Form(...),
    email: str = Form(""),
    message: str = Form(""),
):
    inquiry = {
        "id": str(uuid.uuid4()),
        "company_name": company_name,
        "contact_name": contact_name,
        "contact_phone": contact_phone,
        "email": email,
        "message": message,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
    }
    inquiries_db.append(inquiry)
    return templates.TemplateResponse("company_inquiry.html", {
        "request": request, "success": True,
    })


# ============================================================
# 업체 로그인/등록
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
        "tier": "free",
        "filters": {
            "debt_types": debt_types if debt_types else [],
            "regions": regions if regions else [],
        },
        "description": "",       # 업체 소개 (프로필에서 수정)
        "min_fee": "",            # 최소 수임료
        "experience_years": "",   # 경력 연수
        "success_count": 0,       # 성공 사례 수
        "rating": 0.0,            # 평점
        "review_count": 0,        # 리뷰 수
        "response_rate": 0.0,     # 응답률
        "balance": 0,             # 포인트 잔액 (원)
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


# ============================================================
# 업체 대시보드
# ============================================================
@app.get("/company/dashboard", response_class=HTMLResponse)
async def company_dashboard(request: Request):
    user = get_company_user(request)
    if not user:
        return RedirectResponse("/company/login")

    company = next((c for c in companies_db if c["id"] == user["company_id"]), None)
    if not company:
        return RedirectResponse("/company/login")

    tier_info = COMPANY_TIERS.get(company.get("tier", "free"), COMPANY_TIERS["free"])

    # 이 업체에 배분된 신청건 목록
    my_distributions = [d for d in distributions_db if d["company_id"] == company["id"]]
    leads = []
    for dist in my_distributions:
        app_data = next((a for a in applications_db if a["id"] == dist["application_id"]), None)
        if app_data:
            purchased = any(
                p["distribution_id"] == dist["id"] for p in purchases_db
            )
            # 이 건의 가격 (배분 시 계산된 가격)
            lead_price = dist.get("price", PRICE_BY_SELECTION.get(app_data.get("num_selected", 1), 5000))
            leads.append({
                **app_data,
                "distribution_id": dist["id"],
                "purchased": purchased,
                "dist_status": dist["status"],
                "lead_price": lead_price,
            })

    # 최근 거래 내역
    recent_transactions = sorted(
        [t for t in transactions_db if t["company_id"] == company["id"]],
        key=lambda t: t["created_at"], reverse=True,
    )[:5]

    # 대기 중인 충전 요청
    pending_point = any(d["company_id"] == company["id"] and d["status"] == "pending" for d in point_requests_db)

    return templates.TemplateResponse("company_dashboard.html", {
        "request": request,
        "company": company,
        "tier_info": tier_info,
        "leads": leads,
        "recent_transactions": recent_transactions,
        "pending_point": pending_point,
        "price_by_selection": PRICE_BY_SELECTION,
    })


# ============================================================
# 업체: DB 열람 구매
# ============================================================
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

    app_data = next((a for a in applications_db if a["id"] == dist["application_id"]), None)
    if not app_data:
        return JSONResponse({"error": "신청 정보를 찾을 수 없습니다."}, status_code=404)

    # 가격 계산 (배분 시 저장된 가격 사용)
    lead_price = dist.get("price", _get_lead_price(app_data.get("num_selected", 1), company))

    # 잔액 확인
    if company["balance"] < lead_price:
        return JSONResponse({"error": f"포인트가 부족합니다. (현재: {company['balance']:,}원, 필요: {lead_price:,}원)"}, status_code=400)

    # 차감 & 구매 기록
    company["balance"] -= lead_price
    purchase = {
        "id": str(uuid.uuid4()),
        "distribution_id": distribution_id,
        "company_id": company["id"],
        "application_id": dist["application_id"],
        "price": lead_price,
        "status": "active",  # active → refunded
        "created_at": datetime.now().isoformat(),
    }
    purchases_db.append(purchase)
    dist["status"] = "purchased"

    # 거래 내역 기록
    _record_transaction(
        company["id"], "purchase", -lead_price,
        f"DB 열람 - {app_data.get('debt_type_label', '')} ({app_data.get('region', '')})",
        ref_id=purchase["id"],
    )

    # 신청인 정보 반환
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
# 포인트 시스템 (구 예치금)
# ============================================================
def _record_transaction(company_id: str, tx_type: str, amount: int, description: str, ref_id: str = ""):
    """거래 내역 기록"""
    tx = {
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "type": tx_type,  # deposit, purchase, refund, admin_charge
        "amount": amount,  # 양수: 충전, 음수: 차감
        "description": description,
        "ref_id": ref_id,
        "balance_after": next((c["balance"] for c in companies_db if c["id"] == company_id), 0),
        "created_at": datetime.now().isoformat(),
    }
    transactions_db.append(tx)
    return tx


@app.get("/company/points", response_class=HTMLResponse)
async def company_points_page(request: Request):
    """포인트 관리 페이지"""
    user = get_company_user(request)
    if not user:
        return RedirectResponse("/company/login")

    company = next((c for c in companies_db if c["id"] == user["company_id"]), None)
    if not company:
        return RedirectResponse("/company/login")

    tier_info = COMPANY_TIERS.get(company.get("tier", "free"), COMPANY_TIERS["free"])

    # 이 업체의 거래 내역
    my_transactions = sorted(
        [t for t in transactions_db if t["company_id"] == company["id"]],
        key=lambda t: t["created_at"], reverse=True,
    )

    # 이 업체의 충전 요청
    my_points = sorted(
        [d for d in point_requests_db if d["company_id"] == company["id"]],
        key=lambda d: d["created_at"], reverse=True,
    )

    # 통계
    total_charged = sum(t["amount"] for t in transactions_db if t["company_id"] == company["id"] and t["type"] in ("deposit", "admin_charge"))
    total_spent = abs(sum(t["amount"] for t in transactions_db if t["company_id"] == company["id"] and t["type"] == "purchase"))

    return templates.TemplateResponse("company_points.html", {
        "request": request,
        "company": company,
        "tier_info": tier_info,
        "transactions": my_transactions[:50],
        "point_requests": my_points[:10],
        "total_charged": total_charged,
        "total_spent": total_spent,
    })


# 하위호환: /company/deposit → /company/points 리다이렉트
@app.get("/company/deposit", response_class=HTMLResponse)
async def company_deposit_redirect(request: Request):
    return RedirectResponse("/company/points", status_code=301)


@app.post("/company/points/request")
async def company_point_request(
    request: Request,
    amount: int = Form(...),
    depositor_name: str = Form(...),
):
    """포인트 충전 요청"""
    user = get_company_user(request)
    if not user:
        return JSONResponse({"error": "로그인 필요"}, status_code=401)

    company = next((c for c in companies_db if c["id"] == user["company_id"]), None)
    if not company:
        return JSONResponse({"error": "업체 없음"}, status_code=404)

    if amount < 10000:
        return JSONResponse({"error": "최소 충전 금액은 10,000원입니다."}, status_code=400)

    if amount > 1000000:
        return JSONResponse({"error": "최대 충전 금액은 1,000,000원입니다."}, status_code=400)

    # 포인트 유효기간: 충전일로부터 1년
    expiry_date = (datetime.now() + timedelta(days=365)).isoformat()

    point_req = {
        "id": str(uuid.uuid4()),
        "company_id": company["id"],
        "company_name": company["name"],
        "amount": amount,
        "depositor_name": depositor_name,
        "expiry_date": expiry_date,
        "status": "pending",  # pending → approved → rejected
        "created_at": datetime.now().isoformat(),
        "processed_at": None,
    }
    point_requests_db.append(point_req)

    return JSONResponse({"success": True, "message": f"{amount:,}원 포인트 충전 요청이 접수되었습니다. 관리자 확인 후 처리됩니다."})


# 하위호환: /company/deposit/request → /company/points/request
@app.post("/company/deposit/request")
async def company_deposit_request_redirect(
    request: Request,
    amount: int = Form(...),
    depositor_name: str = Form(...),
):
    return await company_point_request(request, amount=amount, depositor_name=depositor_name)


# ============================================================
# 환불 시스템
# ============================================================
REFUND_REASONS = {
    "unreachable": "연락 불가 (48시간 + 3회 시도)",
    "duplicate": "중복 신청 (7일 이내 동일인)",
    "fake_info": "허위 정보 (이름/번호 불일치)",
    "other": "기타 (수동 검토 필요)",
}

# 자동 승인 사유
AUTO_REFUND_REASONS = {"unreachable", "duplicate", "fake_info"}

# 환불 불가 사유
NO_REFUND_REASONS = {
    "connected_no_contract": "통화 연결됐으나 계약 미성사",
    "already_contracted": "다른 업체와 이미 계약",
    "expired": "구매 후 7일 이상 경과",
}


@app.post("/company/refund/{purchase_id}")
async def request_refund(
    request: Request,
    purchase_id: str,
    reason: str = Form(...),
    evidence: str = Form(""),
):
    """환불 요청"""
    user = get_company_user(request)
    if not user:
        return JSONResponse({"error": "로그인 필요"}, status_code=401)

    company = next((c for c in companies_db if c["id"] == user["company_id"]), None)
    if not company:
        return JSONResponse({"error": "업체 없음"}, status_code=404)

    purchase = next((p for p in purchases_db if p["id"] == purchase_id), None)
    if not purchase or purchase["company_id"] != company["id"]:
        return JSONResponse({"error": "구매 내역을 찾을 수 없습니다."}, status_code=404)

    if purchase.get("status") == "refunded":
        return JSONResponse({"error": "이미 환불 처리된 건입니다."}, status_code=400)

    # 구매 후 7일 초과 → 환불 불가
    purchase_date = datetime.fromisoformat(purchase["created_at"])
    if (datetime.now() - purchase_date).days > 7:
        return JSONResponse({"error": "구매 후 7일이 경과하여 환불이 불가합니다."}, status_code=400)

    # 이미 이 구매건에 대한 환불 요청이 있는지 확인
    existing_refund = next((r for r in refund_requests_db if r["purchase_id"] == purchase_id and r["status"] == "pending"), None)
    if existing_refund:
        return JSONResponse({"error": "이미 환불 요청이 접수되어 있습니다."}, status_code=400)

    # 월간 환불 비율 확인 (20% 초과 시 수동 검토)
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_purchases = [p for p in purchases_db if p["company_id"] == company["id"]
                         and datetime.fromisoformat(p["created_at"]) >= month_start]
    monthly_refunds = [r for r in refund_requests_db if r["company_id"] == company["id"]
                       and datetime.fromisoformat(r["created_at"]) >= month_start
                       and r["status"] in ("approved", "pending")]

    over_cap = len(monthly_refunds) >= max(1, int(len(monthly_purchases) * 0.2)) if monthly_purchases else False

    refund_req = {
        "id": str(uuid.uuid4()),
        "purchase_id": purchase_id,
        "company_id": company["id"],
        "company_name": company["name"],
        "amount": purchase["price"],
        "reason": reason,
        "reason_label": REFUND_REASONS.get(reason, reason),
        "evidence": evidence,
        "over_monthly_cap": over_cap,
        "status": "pending",  # pending → approved → rejected
        "created_at": datetime.now().isoformat(),
        "processed_at": None,
    }
    refund_requests_db.append(refund_req)

    # 자동 승인: 사유가 자동 승인 대상이고 월간 한도 초과가 아닌 경우
    if reason in AUTO_REFUND_REASONS and not over_cap:
        # 자동 환불 처리
        refund_req["status"] = "approved"
        refund_req["processed_at"] = datetime.now().isoformat()
        purchase["status"] = "refunded"
        company["balance"] += purchase["price"]
        _record_transaction(
            company["id"], "refund", purchase["price"],
            f"환불 - {refund_req['reason_label']}",
            ref_id=refund_req["id"],
        )
        return JSONResponse({"success": True, "message": f"환불이 자동 승인되었습니다. {purchase['price']:,}원이 포인트로 반환됩니다.", "auto_approved": True})

    # 수동 검토 필요
    return JSONResponse({"success": True, "message": "환불 요청이 접수되었습니다. 관리자 검토 후 처리됩니다.", "auto_approved": False})


# ============================================================
# 관리자 페이지
# ============================================================
@app.get("/admin")
async def admin_redirect(request: Request):
    admin = get_admin(request)
    if admin:
        return RedirectResponse("/admin/dashboard")
    return RedirectResponse("/admin/login")


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
    total_revenue = sum(p["price"] for p in purchases_db if p.get("status") != "refunded")
    total_refunds = sum(r["amount"] for r in refund_requests_db if r["status"] == "approved")

    crawled_count = len([c for c in companies_db if c.get("source") == "naver_crawl"])
    pending_points = [d for d in point_requests_db if d["status"] == "pending"]
    pending_refunds = [r for r in refund_requests_db if r["status"] == "pending"]

    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "stats": {
            "total_apps": total_apps,
            "total_companies": total_companies,
            "total_purchases": total_purchases,
            "total_revenue": total_revenue,
            "total_refunds": total_refunds,
            "crawled_count": crawled_count,
            "pending_points": len(pending_points),
            "pending_refunds": len(pending_refunds),
        },
        "crawl_state": crawl_state,
        "applications": applications_db[-20:],
        "companies": companies_db,
        "inquiries": inquiries_db[-10:],
        "pending_points": pending_points,
        "pending_refunds": pending_refunds,
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
    """업체 포인트 충전 (관리자)"""
    admin = get_admin(request)
    if not admin:
        return JSONResponse({"error": "권한 없음"}, status_code=401)

    company = next((c for c in companies_db if c["id"] == company_id), None)
    if not company:
        return JSONResponse({"error": "업체를 찾을 수 없습니다."}, status_code=404)

    company["balance"] += amount
    _record_transaction(company["id"], "admin_charge", amount, "관리자 수동 충전")
    return JSONResponse({"success": True, "new_balance": company["balance"]})


@app.post("/admin/company/{company_id}/tier")
async def change_company_tier(request: Request, company_id: str, tier: str = Form(...)):
    """업체 등급 변경 (관리자)"""
    admin = get_admin(request)
    if not admin:
        return JSONResponse({"error": "권한 없음"}, status_code=401)

    if tier not in COMPANY_TIERS:
        return JSONResponse({"error": "유효하지 않은 등급입니다."}, status_code=400)

    company = next((c for c in companies_db if c["id"] == company_id), None)
    if not company:
        return JSONResponse({"error": "업체를 찾을 수 없습니다."}, status_code=404)

    company["tier"] = tier
    return JSONResponse({"success": True, "tier": tier, "label": COMPANY_TIERS[tier]["label"]})


@app.post("/admin/application/{app_id}/approve")
async def admin_approve_application(request: Request, app_id: str):
    """관리자가 신청건 승인 (양방향 매칭에서는 주로 직접신청 건 확인용)"""
    admin = get_admin(request)
    if not admin:
        return JSONResponse({"error": "권한 없음"}, status_code=401)

    app_data = next((a for a in applications_db if a["id"] == app_id), None)
    if not app_data:
        return JSONResponse({"error": "신청건을 찾을 수 없습니다."}, status_code=404)

    if app_data["status"] not in ("pending", "selecting"):
        return JSONResponse({"error": "이미 처리된 신청건입니다."}, status_code=400)

    app_data["status"] = "distributed"
    return JSONResponse({"success": True, "message": "승인 완료"})


@app.post("/admin/application/{app_id}/reject")
async def admin_reject_application(request: Request, app_id: str):
    """관리자가 신청건 반려"""
    admin = get_admin(request)
    if not admin:
        return JSONResponse({"error": "권한 없음"}, status_code=401)

    app_data = next((a for a in applications_db if a["id"] == app_id), None)
    if not app_data:
        return JSONResponse({"error": "신청건을 찾을 수 없습니다."}, status_code=404)

    app_data["status"] = "rejected"
    return JSONResponse({"success": True})


# ============================================================
# 관리자: 포인트 충전 요청 관리
# ============================================================
@app.get("/admin/points", response_class=HTMLResponse)
async def admin_points_page(request: Request):
    """포인트 충전 요청 관리"""
    admin = get_admin(request)
    if not admin:
        return RedirectResponse("/admin/login")

    pending = [d for d in point_requests_db if d["status"] == "pending"]
    processed = sorted(
        [d for d in point_requests_db if d["status"] != "pending"],
        key=lambda d: d.get("processed_at", ""), reverse=True,
    )[:30]

    return templates.TemplateResponse("admin_points.html", {
        "request": request,
        "pending_points": pending,
        "processed_points": processed,
    })


# 하위호환: /admin/deposits → /admin/points
@app.get("/admin/deposits", response_class=HTMLResponse)
async def admin_deposits_redirect(request: Request):
    return RedirectResponse("/admin/points", status_code=301)


@app.post("/admin/point/{point_id}/approve")
async def admin_approve_point(request: Request, point_id: str):
    """포인트 충전 요청 승인 → 잔액 추가"""
    admin = get_admin(request)
    if not admin:
        return JSONResponse({"error": "권한 없음"}, status_code=401)

    dep = next((d for d in point_requests_db if d["id"] == point_id), None)
    if not dep:
        return JSONResponse({"error": "요청을 찾을 수 없습니다."}, status_code=404)

    if dep["status"] != "pending":
        return JSONResponse({"error": "이미 처리된 요청입니다."}, status_code=400)

    company = next((c for c in companies_db if c["id"] == dep["company_id"]), None)
    if not company:
        return JSONResponse({"error": "업체를 찾을 수 없습니다."}, status_code=404)

    # 잔액 추가
    company["balance"] += dep["amount"]
    dep["status"] = "approved"
    dep["processed_at"] = datetime.now().isoformat()

    # 거래 내역 기록
    _record_transaction(
        company["id"], "deposit", dep["amount"],
        f"포인트 충전 (입금자: {dep['depositor_name']})",
        ref_id=dep["id"],
    )

    return JSONResponse({"success": True, "message": f"{dep['amount']:,}원 포인트 충전 승인 완료"})


@app.post("/admin/point/{point_id}/reject")
async def admin_reject_point(request: Request, point_id: str):
    """포인트 충전 요청 반려"""
    admin = get_admin(request)
    if not admin:
        return JSONResponse({"error": "권한 없음"}, status_code=401)

    dep = next((d for d in point_requests_db if d["id"] == point_id), None)
    if not dep:
        return JSONResponse({"error": "요청을 찾을 수 없습니다."}, status_code=404)

    if dep["status"] != "pending":
        return JSONResponse({"error": "이미 처리된 요청입니다."}, status_code=400)

    dep["status"] = "rejected"
    dep["processed_at"] = datetime.now().isoformat()

    return JSONResponse({"success": True})


# 하위호환: /admin/deposit/{id}/approve → /admin/point/{id}/approve
@app.post("/admin/deposit/{deposit_id}/approve")
async def admin_approve_deposit_compat(request: Request, deposit_id: str):
    return await admin_approve_point(request, deposit_id)


@app.post("/admin/deposit/{deposit_id}/reject")
async def admin_reject_deposit_compat(request: Request, deposit_id: str):
    return await admin_reject_point(request, deposit_id)


# ============================================================
# 관리자: 환불 요청 관리
# ============================================================
@app.get("/admin/refunds", response_class=HTMLResponse)
async def admin_refunds_page(request: Request):
    """환불 요청 관리"""
    admin = get_admin(request)
    if not admin:
        return RedirectResponse("/admin/login")

    pending = [r for r in refund_requests_db if r["status"] == "pending"]
    processed = sorted(
        [r for r in refund_requests_db if r["status"] != "pending"],
        key=lambda r: r.get("processed_at", ""), reverse=True,
    )[:30]

    return templates.TemplateResponse("admin_refunds.html", {
        "request": request,
        "pending_refunds": pending,
        "processed_refunds": processed,
        "refund_reasons": REFUND_REASONS,
    })


@app.post("/admin/refund/{refund_id}/approve")
async def admin_approve_refund(request: Request, refund_id: str):
    """환불 요청 수동 승인"""
    admin = get_admin(request)
    if not admin:
        return JSONResponse({"error": "권한 없음"}, status_code=401)

    refund = next((r for r in refund_requests_db if r["id"] == refund_id), None)
    if not refund:
        return JSONResponse({"error": "환불 요청을 찾을 수 없습니다."}, status_code=404)

    if refund["status"] != "pending":
        return JSONResponse({"error": "이미 처리된 요청입니다."}, status_code=400)

    purchase = next((p for p in purchases_db if p["id"] == refund["purchase_id"]), None)
    if not purchase:
        return JSONResponse({"error": "구매 내역을 찾을 수 없습니다."}, status_code=404)

    company = next((c for c in companies_db if c["id"] == refund["company_id"]), None)
    if not company:
        return JSONResponse({"error": "업체를 찾을 수 없습니다."}, status_code=404)

    # 환불 처리
    refund["status"] = "approved"
    refund["processed_at"] = datetime.now().isoformat()
    purchase["status"] = "refunded"
    company["balance"] += refund["amount"]

    _record_transaction(
        company["id"], "refund", refund["amount"],
        f"환불 승인 (관리자) - {refund['reason_label']}",
        ref_id=refund["id"],
    )

    return JSONResponse({"success": True, "message": f"{refund['amount']:,}원 환불 승인 완료"})


@app.post("/admin/refund/{refund_id}/reject")
async def admin_reject_refund(request: Request, refund_id: str):
    """환불 요청 반려"""
    admin = get_admin(request)
    if not admin:
        return JSONResponse({"error": "권한 없음"}, status_code=401)

    refund = next((r for r in refund_requests_db if r["id"] == refund_id), None)
    if not refund:
        return JSONResponse({"error": "환불 요청을 찾을 수 없습니다."}, status_code=404)

    if refund["status"] != "pending":
        return JSONResponse({"error": "이미 처리된 요청입니다."}, status_code=400)

    refund["status"] = "rejected"
    refund["processed_at"] = datetime.now().isoformat()

    return JSONResponse({"success": True})


@app.post("/admin/logout")
async def admin_logout():
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie("admin_token")
    return response


# ============================================================
# 크롤링 관리 (관리자)
# ============================================================
crawl_state = {
    "status": "idle",       # idle, running, completed, error
    "last_crawl_at": None,
    "last_count": 0,
    "new_count": 0,
    "log": [],
    "progress": "",
    "next_scheduled": None,
}

# 초기 로드 시 crawled_companies.json의 수정 시간을 last_crawl_at으로 설정
if _os.path.exists(_crawled_path):
    _mod_time = datetime.fromtimestamp(_os.path.getmtime(_crawled_path))
    crawl_state["last_crawl_at"] = _mod_time.isoformat()
    crawl_state["last_count"] = len(companies_db)


def _run_crawl_sync():
    """크롤링 실행 (동기 - 별도 스레드에서 실행)"""
    from crawler import crawl_all, to_company_format, save_results

    crawl_state["status"] = "running"
    crawl_state["log"] = []
    crawl_state["progress"] = "크롤링 시작..."

    try:
        # 크롤링 실행
        crawl_state["progress"] = "네이버 검색 중..."
        raw = crawl_all()
        crawl_state["log"].append(f"네이버 검색 완료: {len(raw)}개 수집")

        # 포맷 변환
        crawl_state["progress"] = "데이터 변환 중..."
        new_companies = to_company_format(raw)
        crawl_state["log"].append(f"포맷 변환 완료: {len(new_companies)}개")

        # JSON 파일 저장
        crawl_state["progress"] = "파일 저장 중..."
        _save_path = _os.path.join(_os.path.dirname(__file__) or ".", "crawled_companies.json")
        save_results(new_companies, _save_path)
        crawl_state["log"].append(f"crawled_companies.json 저장 완료")

        # 메모리 DB 업데이트 (기존 크롤링 업체 교체, 등록/인증 업체는 유지)
        existing_registered = [c for c in companies_db if c.get("source") != "naver_crawl"]
        companies_db.clear()
        companies_db.extend(existing_registered)

        # 새 크롤링 데이터 로드
        for _c in new_companies:
            _name = _c.get("name", "")
            _name = _re.sub(r"(톡톡|쿠폰|법률사무소$|법무사사무소$)", "", _name).strip()
            _c["name"] = _name
            _c["tier"] = "free"
            _c["verified"] = False
            _c["status"] = "listed"
            _c["response_rate"] = 0.0
            for field, default in [("description", ""), ("rating", 0.0), ("review_count", 0),
                                   ("success_count", 0), ("experience_years", ""), ("min_fee", ""), ("balance", 0)]:
                if not _c.get(field):
                    _c[field] = default
        companies_db.extend(new_companies)

        new_added = len(new_companies) - crawl_state["last_count"]
        if new_added < 0:
            new_added = 0

        crawl_state["status"] = "completed"
        crawl_state["last_crawl_at"] = datetime.now().isoformat()
        crawl_state["last_count"] = len(new_companies)
        crawl_state["new_count"] = new_added
        crawl_state["progress"] = f"완료! 총 {len(new_companies)}개 업체 (신규 {new_added}개)"
        crawl_state["log"].append(f"DB 업데이트 완료: 총 {len(companies_db)}개 업체")

        print(f"[새출발] 크롤링 완료: {len(new_companies)}개 업체")

    except Exception as e:
        crawl_state["status"] = "error"
        crawl_state["progress"] = f"오류 발생: {str(e)}"
        crawl_state["log"].append(f"ERROR: {str(e)}")
        print(f"[새출발] 크롤링 오류: {e}")


def start_crawl_background():
    """별도 스레드에서 크롤링 시작"""
    if crawl_state["status"] == "running":
        return False
    thread = threading.Thread(target=_run_crawl_sync, daemon=True)
    thread.start()
    return True


# 주간 자동 크롤링 스케줄러
_scheduler_running = False


def _weekly_scheduler():
    """1주일마다 크롤링 자동 실행"""
    global _scheduler_running
    _scheduler_running = True
    WEEK_SECONDS = 7 * 24 * 60 * 60  # 604800초

    while _scheduler_running:
        # 다음 실행 시간 계산
        next_run = datetime.now() + timedelta(seconds=WEEK_SECONDS)
        crawl_state["next_scheduled"] = next_run.isoformat()
        print(f"[새출발] 다음 자동 크롤링 예정: {next_run.strftime('%Y-%m-%d %H:%M')}")

        # 1주일 대기 (10분 단위로 체크하여 종료 가능)
        waited = 0
        while waited < WEEK_SECONDS and _scheduler_running:
            import time
            time.sleep(600)  # 10분
            waited += 600

        if _scheduler_running:
            print("[새출발] 주간 자동 크롤링 시작")
            start_crawl_background()


@app.on_event("startup")
async def startup_scheduler():
    """서버 시작 시 주간 스케줄러 실행"""
    thread = threading.Thread(target=_weekly_scheduler, daemon=True)
    thread.start()
    next_run = datetime.now() + timedelta(weeks=1)
    crawl_state["next_scheduled"] = next_run.isoformat()
    print(f"[새출발] 주간 크롤링 스케줄러 시작됨 (다음: {next_run.strftime('%Y-%m-%d %H:%M')})")


@app.get("/admin/crawl", response_class=HTMLResponse)
async def admin_crawl_page(request: Request):
    """크롤링 관리 페이지"""
    admin = get_admin(request)
    if not admin:
        return RedirectResponse("/admin/login")

    # 지역별 통계
    region_stats = {}
    crawled_companies = [c for c in companies_db if c.get("source") == "naver_crawl"]
    for c in crawled_companies:
        regions = c.get("filters", {}).get("regions", [])
        r = regions[0] if regions else "기타"
        region_stats[r] = region_stats.get(r, 0) + 1

    return templates.TemplateResponse("admin_crawl.html", {
        "request": request,
        "crawl_state": crawl_state,
        "total_crawled": len(crawled_companies),
        "total_registered": len([c for c in companies_db if c.get("source") != "naver_crawl"]),
        "region_stats": dict(sorted(region_stats.items(), key=lambda x: -x[1])),
    })


@app.post("/admin/crawl/start")
async def admin_crawl_start(request: Request):
    """크롤링 수동 실행"""
    admin = get_admin(request)
    if not admin:
        return JSONResponse({"error": "권한 없음"}, status_code=401)

    if crawl_state["status"] == "running":
        return JSONResponse({"error": "이미 크롤링이 진행 중입니다."}, status_code=400)

    started = start_crawl_background()
    if started:
        return JSONResponse({"success": True, "message": "크롤링이 시작되었습니다."})
    return JSONResponse({"error": "크롤링 시작 실패"}, status_code=500)


@app.get("/admin/crawl/status")
async def admin_crawl_status(request: Request):
    """크롤링 상태 조회 (폴링용)"""
    admin = get_admin(request)
    if not admin:
        return JSONResponse({"error": "권한 없음"}, status_code=401)

    return JSONResponse({
        "status": crawl_state["status"],
        "progress": crawl_state["progress"],
        "last_crawl_at": crawl_state["last_crawl_at"],
        "last_count": crawl_state["last_count"],
        "new_count": crawl_state["new_count"],
        "log": crawl_state["log"][-20:],
        "next_scheduled": crawl_state["next_scheduled"],
    })


# ============================================================
# Q&A 페이지 (회생/파산 관련 FAQ)
# ============================================================
FAQ_DATA = [
    {
        "category": "개인회생",
        "questions": [
            {
                "q": "개인회생이란 무엇인가요?",
                "a": "개인회생은 정기적인 수입이 있는 채무자가 법원에 신청하여, 3~5년간 변제계획에 따라 채무 일부를 갚고 나머지를 면제받는 제도입니다. 채무 총액이 무담보 10억원, 담보 15억원 이하인 경우 신청 가능합니다."
            },
            {
                "q": "개인회생 신청 자격 조건은?",
                "a": "① 정기적 수입이 있어야 합니다 (직장인, 자영업자, 프리랜서 등). ② 무담보채무 10억원, 담보채무 15억원 이하. ③ 최저생계비를 제외한 가용소득으로 최소 변제 가능해야 합니다. 일용직, 아르바이트도 정기 수입으로 인정됩니다."
            },
            {
                "q": "개인회생하면 채무를 얼마나 갚아야 하나요?",
                "a": "통상 총 채무의 5~30% 정도를 3~5년에 걸쳐 분할 상환합니다. 변제 비율은 소득, 가족 수, 채무 총액 등에 따라 법원이 결정합니다. 나머지 70~95%는 면제됩니다."
            },
            {
                "q": "개인회생 절차와 기간은?",
                "a": "① 전문가 상담 → ② 서류 준비 (1~2주) → ③ 법원 신청 → ④ 개시결정 (1~2개월) → ⑤ 채권자 이의기간 → ⑥ 변제계획 인가 (2~4개월) → ⑦ 변제 이행 (3~5년) → ⑧ 면책. 신청부터 인가까지 보통 3~6개월 소요됩니다."
            },
            {
                "q": "개인회생 비용(수임료)은 얼마인가요?",
                "a": "법무법인/법률사무소마다 다르지만, 보통 150~300만원 선입니다. 분할 납부가 가능한 곳이 많으며, 법률구조공단을 통하면 무료 또는 저렴하게 진행할 수 있습니다. 여러 업체의 견적을 비교하는 것을 권장합니다."
            },
            {
                "q": "개인회생 중 신용카드 사용이 가능한가요?",
                "a": "개인회생 개시결정 후에는 기존 신용카드 사용이 정지됩니다. 변제기간 중에는 체크카드만 사용 가능하며, 면책 후 신용 회복 과정을 거쳐 다시 카드 발급이 가능합니다."
            },
            {
                "q": "개인회생하면 집이나 차를 빼앗기나요?",
                "a": "개인회생의 가장 큰 장점 중 하나가 재산을 유지할 수 있다는 점입니다. 주택(실거주 목적)과 업무용 차량 등은 보존 가능합니다. 다만 변제계획에서 청산가치 이상을 변제해야 합니다."
            },
            {
                "q": "개인회생 신청 후 채권추심(독촉)이 멈추나요?",
                "a": "네. 법원의 개시결정이 나면 '금지명령'에 따라 모든 채권추심, 강제집행, 급여압류가 즉시 중단됩니다. 신청만 해도 보전처분을 통해 추심을 멈출 수 있습니다."
            },
            {
                "q": "개인회생 변제기간 중 직장을 잃으면?",
                "a": "실직 시 법원에 변제계획 변경을 신청할 수 있습니다. 일시적 유예나 변제금액 조정이 가능합니다. 3개월 이상 변제 불이행 시 폐지될 수 있으므로, 빨리 법원이나 담당 변호사에게 알려야 합니다."
            },
            {
                "q": "개인회생 완료 후 신용은 어떻게 되나요?",
                "a": "면책 후 5년이 지나면 개인회생 기록이 삭제됩니다. 실제로는 면책 후 1~2년 내에 신용등급이 상당히 회복되며, 대출이나 카드 발급도 가능해집니다. 성실한 금융활동으로 회복 속도를 높일 수 있습니다."
            },
        ]
    },
    {
        "category": "개인파산·면책",
        "questions": [
            {
                "q": "개인파산이란 무엇인가요?",
                "a": "개인파산은 채무 변제가 불가능한 상태에서 법원에 파산을 신청하고, 면책(빚 탕감)을 받아 경제적으로 새출발하는 제도입니다. 소득이 없거나 매우 적어 개인회생이 불가능한 경우에 적합합니다."
            },
            {
                "q": "개인파산 신청 자격은?",
                "a": "채무를 갚을 수 없는 상태(지급불능)이면 누구나 신청 가능합니다. 소득이 없는 무직자, 기초생활수급자, 소득이 매우 적은 분들이 주로 신청합니다. 채무 금액 제한은 없습니다."
            },
            {
                "q": "개인파산과 개인회생의 차이점은?",
                "a": "개인회생: 소득이 있는 사람이 3~5년간 일부를 갚고 나머지 면제. 개인파산: 소득이 없거나 적은 사람이 재산을 정리하고 채무 전액 면제. 회생은 재산 보존 가능, 파산은 일부 재산 처분이 필요할 수 있습니다."
            },
            {
                "q": "파산하면 모든 재산을 잃나요?",
                "a": "아닙니다. 생활에 필요한 최소한의 재산(자유재산)은 보호됩니다. 일반적으로 500만원 이하의 예금, 생활필수품, 6개월분 생활비 상당액 등은 보전됩니다. 법원에 자유재산 확대 신청도 가능합니다."
            },
            {
                "q": "파산 면책이란?",
                "a": "면책은 파산 선고 후 법원이 남은 채무를 갚지 않아도 된다고 결정하는 것입니다. 파산 신청과 면책 신청은 동시에 합니다. 면책이 되면 기존 채무에 대한 법적 책임이 모두 사라집니다."
            },
            {
                "q": "면책이 안 되는 경우도 있나요?",
                "a": "네. ① 도박·사치로 인한 과도한 채무, ② 재산 은닉·허위 신고, ③ 특정 채권자에게만 편파 변제, ④ 파산 원인이 사기·횡령인 경우 등은 면책 불허가 사유입니다. 다만 재량면책이 인정되는 경우도 많습니다."
            },
            {
                "q": "파산하면 취업이나 사업에 제한이 있나요?",
                "a": "파산 선고 후 면책까지는 일부 직업 제한이 있습니다 (변호사, 공인회계사, 보험모집인 등). 하지만 면책 후에는 모든 자격이 회복됩니다. 일반 직장인의 경우 취업에 실질적 제한은 없습니다."
            },
            {
                "q": "개인파산 비용은?",
                "a": "보통 100~200만원 선이며, 분할 납부 가능합니다. 기초생활수급자나 저소득자는 법률구조공단에서 무료로 진행할 수 있습니다. 인지대·송달료 등 법원 비용은 약 5~10만원입니다."
            },
            {
                "q": "개인파산 절차는 얼마나 걸리나요?",
                "a": "① 상담·서류준비 (2~4주) → ② 법원 신청 → ③ 파산선고 (2~4개월) → ④ 면책심문 (1~3개월) → ⑤ 면책결정. 전체 6개월~1년 정도 소요됩니다."
            },
        ]
    },
    {
        "category": "기업회생·기업파산",
        "questions": [
            {
                "q": "기업회생(법정관리)이란?",
                "a": "기업회생은 재정난에 빠진 기업이 법원의 관리하에 사업을 계속하면서 채무를 조정하는 제도입니다. 고용을 유지하고 기업 가치를 보존하면서 채무를 감축할 수 있습니다."
            },
            {
                "q": "기업회생 신청 조건은?",
                "a": "① 사업의 계속가치가 청산가치보다 큰 경우, ② 자금난으로 사업 지속이 어려운 경우에 신청합니다. 법인뿐 아니라 개인사업자도 가능합니다."
            },
            {
                "q": "기업파산과 기업회생의 차이는?",
                "a": "기업회생: 사업을 계속하면서 채무 조정. 기업파산: 사업을 정리(청산)하고 잔여 재산으로 채무 변제. 회생은 기업을 살리는 것, 파산은 정리하는 것입니다."
            },
            {
                "q": "기업회생 시 대표이사는 어떻게 되나요?",
                "a": "기존 대표이사가 관리인으로 계속 경영할 수 있습니다(기존경영자관리인 제도). 다만 부실 책임이 큰 경우 제3자가 관리인으로 선임될 수 있습니다."
            },
            {
                "q": "소규모 개인사업자도 기업회생이 가능한가요?",
                "a": "네. '간이회생' 제도가 있어 채무 50억원 이하의 소규모 기업(개인사업자 포함)은 간소화된 절차로 빠르게 진행할 수 있습니다. 비용과 기간이 크게 절감됩니다."
            },
        ]
    },
    {
        "category": "채무조정·신용회복",
        "questions": [
            {
                "q": "개인워크아웃(신용회복위원회)이란?",
                "a": "신용회복위원회를 통해 채권자(은행·카드사 등)와 협의하여 채무를 감면·조정하는 사적 제도입니다. 법원을 거치지 않아 절차가 간단하고, 이자 감면·원금 분할상환이 가능합니다."
            },
            {
                "q": "개인회생 vs 워크아웃, 뭐가 나을까요?",
                "a": "워크아웃: 절차 간단, 신용 영향 적음, 원금 감면율 낮음 (최대 50~70%). 개인회생: 법원 절차, 원금 감면율 높음 (70~95%), 강제력 있음. 채무 규모가 크고 변제 여력이 적으면 개인회생이 유리합니다."
            },
            {
                "q": "채무 통합(대환대출)과 회생의 차이는?",
                "a": "채무통합: 여러 빚을 하나로 묶어 이자율을 낮추는 것. 원금은 그대로. 회생: 법원에서 원금 자체를 감면. 채무통합이 안 되거나 이자율 조정만으로 해결이 안 되면 회생을 고려하세요."
            },
            {
                "q": "불법 사금융(사채) 채무도 회생/파산에 포함되나요?",
                "a": "네. 불법 고금리 사채도 채무에 포함됩니다. 오히려 법정 최고금리(연 20%)를 초과한 이자는 무효이므로, 실제 채무액이 줄어들 수 있습니다. 사채 때문에 힘드시면 빨리 전문가 상담을 받으세요."
            },
            {
                "q": "세금 체납도 면제받을 수 있나요?",
                "a": "세금(국세·지방세)은 면책 대상에서 제외됩니다(비면책채권). 다만 개인회생 변제계획에는 세금 납부를 포함할 수 있고, 세무서에 분할납부를 별도로 신청할 수 있습니다."
            },
        ]
    },
    {
        "category": "실생활 궁금증",
        "questions": [
            {
                "q": "회생/파산하면 가족에게 영향이 있나요?",
                "a": "본인에게만 적용됩니다. 가족의 신용이나 재산에는 영향 없습니다. 다만 가족이 연대보증을 선 경우, 그 채무는 가족에게 청구될 수 있습니다."
            },
            {
                "q": "급여(월급)를 압류당하고 있는데 어떡하나요?",
                "a": "개인회생 신청 후 보전처분·금지명령을 받으면 급여 압류가 즉시 해제됩니다. 월급의 1/2까지 압류 가능하지만, 최저생계비(약 185만원, 2024년 기준)는 보호됩니다."
            },
            {
                "q": "보증채무도 회생/파산으로 해결 가능한가요?",
                "a": "네. 보증채무(연대보증 포함)도 개인회생·파산 신청 시 포함됩니다. 본인이 직접 빌린 돈이 아니더라도 법적으로 갚아야 할 의무가 있는 채무면 모두 포함 가능합니다."
            },
            {
                "q": "회생/파산 기록은 언제 삭제되나요?",
                "a": "개인회생: 면책 후 5년 뒤 신용정보 삭제. 개인파산: 면책 후 5년 뒤 삭제. 한국신용정보원에서 관리하며, 기간 경과 후 자동 삭제됩니다."
            },
            {
                "q": "이미 한번 회생/파산을 했는데 다시 신청 가능한가요?",
                "a": "개인회생: 이전 면책 후 제한 없이 재신청 가능 (다만 법원 심사가 엄격해질 수 있음). 개인파산: 면책 후 7년이 지나야 재신청 가능합니다."
            },
            {
                "q": "빚이 얼마 이상이어야 회생/파산 신청 가능한가요?",
                "a": "최소 금액 제한은 없습니다. 빚이 100만원이라도 갚을 수 없는 상태면 신청 가능합니다. 실제로는 채무 총액이 2,000만원 이상인 경우에 많이 신청합니다."
            },
            {
                "q": "변호사 없이 혼자 신청할 수 있나요?",
                "a": "법적으로는 본인 신청이 가능합니다. 하지만 서류 작성, 변제계획 수립, 법원 대응 등이 복잡하여 전문가 도움을 받는 것이 성공률이 훨씬 높습니다. 법률구조공단을 이용하면 무료로 전문가 도움을 받을 수 있습니다."
            },
            {
                "q": "상담 받으면 바로 진행해야 하나요?",
                "a": "아닙니다. 상담은 현재 상황을 파악하고 가능한 방법을 알아보는 것입니다. 상담 후 충분히 생각하고 결정하시면 됩니다. 새출발에서 여러 업체의 상담을 비교한 후 결정하세요."
            },
        ]
    },
]


@app.get("/faq", response_class=HTMLResponse)
async def faq_page(request: Request):
    return templates.TemplateResponse("faq.html", {
        "request": request,
        "faq_data": FAQ_DATA,
    })


# ============================================================
# API (향후 확장용)
# ============================================================
@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "새출발", "timestamp": datetime.now().isoformat()}
