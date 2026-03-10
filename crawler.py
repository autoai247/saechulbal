"""
네이버 플레이스 GraphQL API를 활용한 회생/파산 업체 크롤링
- 네이버 플레이스 검색 API (페이징 지원, 한번에 50개)
- 키워드 확장 + 구/군 단위 지역 세분화
- 기존 웹 검색도 보조로 사용
"""

import requests
from bs4 import BeautifulSoup
import json
import time
import uuid
import re
from datetime import datetime
from urllib.parse import quote

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ============================================================
# 검색 키워드 (대폭 확장)
# ============================================================
SEARCH_KEYWORDS = [
    # 개인회생
    "개인회생 법무법인",
    "개인회생 법률사무소",
    "개인회생 변호사",
    "개인회생 법무사",
    "개인회생 전문",
    "개인회생 상담",
    "개인회생 무료상담",
    "개인회생 수임료",
    # 개인파산
    "개인파산 법무법인",
    "개인파산 법률사무소",
    "개인파산 변호사",
    "개인파산 법무사",
    "개인파산 전문",
    "개인파산 면책",
    "파산면책 법률사무소",
    "파산면책 전문",
    # 기업회생/파산
    "기업회생 법무법인",
    "기업회생 변호사",
    "기업파산 법무법인",
    "법인회생 전문",
    "간이회생",
    # 채무 관련
    "채무조정 법무법인",
    "채무조정 법률사무소",
    "빚 정리 법무법인",
    "빚 탕감 법률사무소",
    "채무 상담",
    "부채 상담",
    "신용회복 법무사",
    "워크아웃 상담",
    # 복합 키워드
    "회생파산 전문",
    "회생 파산 법무법인",
    "회생 파산 법률사무소",
    "회생 파산 변호사",
    "개인회생 개인파산",
    "면책 전문 변호사",
    "도산 전문 변호사",
    "도산 법무법인",
]

# ============================================================
# 지역 (광역시/도 + 주요 시/군/구 세분화)
# ============================================================
REGIONS_DETAIL = {
    "서울": [
        "서울", "서울 강남", "서울 서초", "서울 송파", "서울 강동",
        "서울 마포", "서울 영등포", "서울 구로", "서울 관악", "서울 동작",
        "서울 강서", "서울 양천", "서울 용산", "서울 성동", "서울 광진",
        "서울 중구", "서울 종로", "서울 노원", "서울 도봉", "서울 강북",
        "서울 성북", "서울 동대문", "서울 중랑", "서울 은평", "서울 서대문",
    ],
    "경기": [
        "경기", "수원", "성남", "고양", "용인", "부천", "안산", "안양",
        "남양주", "화성", "평택", "의정부", "시흥", "파주", "김포",
        "광명", "광주", "군포", "오산", "이천", "양주", "안성",
        "하남", "의왕", "여주", "동두천", "과천",
    ],
    "인천": [
        "인천", "인천 남동", "인천 부평", "인천 서구", "인천 미추홀",
        "인천 연수", "인천 계양", "인천 중구",
    ],
    "부산": [
        "부산", "부산 해운대", "부산 부산진", "부산 동래", "부산 남구",
        "부산 북구", "부산 사상", "부산 사하", "부산 금정", "부산 연제",
    ],
    "대구": [
        "대구", "대구 수성", "대구 달서", "대구 북구", "대구 중구",
        "대구 동구", "대구 서구",
    ],
    "대전": ["대전", "대전 서구", "대전 유성", "대전 중구", "대전 동구"],
    "광주": ["광주", "광주 서구", "광주 북구", "광주 남구", "광주 광산"],
    "울산": ["울산", "울산 남구", "울산 중구", "울산 동구"],
    "세종": ["세종"],
    "강원": ["강원", "춘천", "원주", "강릉", "속초"],
    "충북": ["충북", "청주", "충주", "제천"],
    "충남": ["충남", "천안", "아산", "서산", "당진", "논산", "공주"],
    "전북": ["전북", "전주", "익산", "군산", "정읍"],
    "전남": ["전남", "목포", "여수", "순천", "광양"],
    "경북": ["경북", "포항", "구미", "경주", "안동", "김천"],
    "경남": ["경남", "창원", "김해", "양산", "진주", "거제", "통영"],
    "제주": ["제주", "서귀포"],
}

# 모든 지역 플랫 리스트
ALL_REGIONS = []
for regions in REGIONS_DETAIL.values():
    ALL_REGIONS.extend(regions)


# ============================================================
# 네이버 플레이스 GraphQL API 크롤링
# ============================================================
def create_place_session():
    """네이버 플레이스 GraphQL API용 세션 생성"""
    s = requests.Session()
    s.headers.update(HEADERS)

    # 쿠키 획득
    encoded_query = quote("개인회생")
    try:
        s.get(f"https://pcmap.place.naver.com/place/list?query={encoded_query}", timeout=10)
    except Exception:
        pass

    s.headers.update({
        "Content-Type": "application/json",
        "Referer": f"https://pcmap.place.naver.com/place/list?query={encoded_query}",
    })
    return s


def search_places_graphql(session, keyword: str, start: int = 1, display: int = 50) -> tuple[list[dict], int]:
    """네이버 플레이스 GraphQL API로 검색 (페이징 지원)"""
    url = "https://pcmap-api.place.naver.com/graphql"
    query = [{
        "operationName": "getPlacesList",
        "query": "query getPlacesList($input: PlacesInput) { places(input: $input) { total items { id name address roadAddress category phone } } }",
        "variables": {"input": {"query": keyword, "start": start, "display": display}}
    }]

    try:
        r = session.post(url, json=query, timeout=15)
        if r.status_code == 429:
            # 레이트 리밋 - 잠시 대기 후 재시도
            time.sleep(5)
            r = session.post(url, json=query, timeout=15)

        if r.status_code != 200:
            return [], 0

        data = r.json()
        if not isinstance(data, list) or not data:
            return [], 0

        places_data = data[0].get("data", {}).get("places", {})
        if not places_data:
            return [], 0

        total = places_data.get("total", 0)
        items = places_data.get("items", [])

        results = []
        for item in items:
            if not item.get("name"):
                continue
            results.append({
                "name": item["name"],
                "address": item.get("roadAddress") or item.get("address", ""),
                "phone": item.get("phone", ""),
                "place_id": item.get("id", ""),
                "category": item.get("category", ""),
                "source_url": f"https://pcmap.place.naver.com/place/{item.get('id', '')}",
            })
        return results, total

    except Exception as e:
        print(f"  [!] GraphQL Error: {e}")
        return [], 0


def search_naver_web(keyword: str) -> list[dict]:
    """기존 네이버 웹 검색 (보조 수단)"""
    params = {"where": "nexearch", "query": keyword}
    try:
        r = requests.get(
            "https://search.naver.com/search.naver",
            params=params,
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        results = []

        links = soup.select("a.place_bluelink")
        for link in links:
            name = link.get_text(strip=True)
            href = link.get("href", "")

            place_id = ""
            m = re.search(r"/place/(\d+)", href)
            if m:
                place_id = m.group(1)

            name = re.sub(r"(법률사무소|법무법인|쿠폰|톡톡)$", "", name).strip()
            if not name:
                continue

            container = link
            for _ in range(8):
                container = container.parent
                if container is None:
                    break

            address = ""
            phone = ""
            if container:
                text_parts = container.get_text("|", strip=True).split("|")
                for part in text_parts:
                    part = part.strip()
                    if re.match(r"^(서울|경기|인천|부산|대구|대전|광주|울산|세종|강원|충[북남]|전[북남]|경[북남]|제주)", part):
                        if not address:
                            address = part
                    if re.match(r"^0\d{1,2}[.-]?\d{3,4}[.-]?\d{4}$", part.replace("-", "").replace(".", "")):
                        if not phone:
                            phone = part

            results.append({
                "name": name,
                "address": address,
                "phone": phone,
                "place_id": place_id,
                "category": "",
                "source_url": href,
            })

        return results

    except Exception as e:
        print(f"  [!] Web Error: {e}")
        return []


# ============================================================
# 관련 업종 필터링
# ============================================================
RELEVANT_CATEGORIES = {
    "법률사무소", "법무법인", "법무사사무소", "변호사사무소",
    "법무사", "변호사", "법률상담", "법률구조",
}

RELEVANT_NAME_KEYWORDS = [
    "법무법인", "법률사무소", "법무사", "변호사", "회생", "파산",
    "면책", "채무", "도산", "법률", "리셋", "새출발", "클리어",
    "리스타트", "프리덤", "해방", "자유",
]


def is_relevant(item: dict) -> bool:
    """회생/파산 관련 업체인지 판단"""
    category = item.get("category", "")
    name = item.get("name", "")

    # 카테고리로 판단
    if category in RELEVANT_CATEGORIES:
        return True
    for rc in RELEVANT_CATEGORIES:
        if rc in category:
            return True

    # 이름으로 판단
    for kw in RELEVANT_NAME_KEYWORDS:
        if kw in name:
            return True

    return False


# ============================================================
# 중복 제거 키 생성
# ============================================================
def _normalize_name(name: str) -> str:
    """업체명 정규화 (중복 제거용)"""
    # 톡톡, 쿠폰, 법률사무소/법무사사무소/법무법인 꼬리 제거
    name = re.sub(r"(톡톡|쿠폰)+", "", name)
    # 공백, 특수문자 제거
    name = re.sub(r"[\s\-·.,()（）]", "", name)
    return name.strip()


def _dedup_key(item: dict) -> str:
    """place_id 우선, 없으면 정규화된 이름으로 키 생성"""
    pid = item.get("place_id", "")
    if pid:
        return f"pid:{pid}"
    return f"name:{_normalize_name(item.get('name', ''))}"


# ============================================================
# 메인 크롤링 함수
# ============================================================
def crawl_all() -> list[dict]:
    """전체 크롤링: GraphQL + 웹 검색"""
    all_results = {}  # dedup_key -> item

    # --- Phase 1: GraphQL API 크롤링 (메인) ---
    print("=" * 60)
    print("  Phase 1: 네이버 플레이스 GraphQL API")
    print("=" * 60)

    session = create_place_session()

    # 핵심 키워드만 (GraphQL은 자체적으로 관련 업체를 잘 찾아줌)
    graphql_keywords = [
        "개인회생", "개인파산", "기업회생", "기업파산",
        "회생 파산", "회생파산 전문",
        "개인회생 법무법인", "개인회생 법률사무소", "개인회생 변호사", "개인회생 법무사",
        "개인파산 법무법인", "개인파산 법률사무소", "개인파산 변호사",
        "기업회생 법무법인", "기업회생 변호사",
        "채무조정", "채무 상담", "빚 정리", "파산면책",
        "도산 전문", "면책 전문", "신용회복",
        "간이회생", "법인회생",
    ]

    # 주요 지역 (광역 단위)
    graphql_regions = [
        "", "서울", "경기", "인천", "부산", "대구", "대전", "광주",
        "울산", "세종", "강원", "충북", "충남", "전북", "전남",
        "경북", "경남", "제주",
        "수원", "성남", "고양", "용인", "부천", "안산", "안양",
        "남양주", "화성", "평택", "의정부", "시흥", "파주", "김포",
        "청주", "천안", "전주", "창원", "김해", "포항", "구미",
        "춘천", "원주", "목포", "여수", "순천", "제주", "서귀포",
    ]

    # 키워드 x 지역 조합 생성
    search_queries = []
    for kw in graphql_keywords:
        search_queries.append(kw)  # 지역 없이
    for kw in graphql_keywords[:15]:  # 상위 15개 키워드만 지역 조합
        for region in graphql_regions:
            if region:
                q = f"{region} {kw}"
                if q not in search_queries:
                    search_queries.append(q)

    print(f"GraphQL 검색어: {len(search_queries)}개\n")

    total_api_calls = 0
    for i, query in enumerate(search_queries):
        print(f"[{i+1}/{len(search_queries)}] '{query}'", end="", flush=True)

        # 첫 페이지
        results, total = search_places_graphql(session, query, start=1, display=50)
        total_api_calls += 1
        new_count = 0

        for r in results:
            if not is_relevant(r):
                continue
            key = _dedup_key(r)
            if key not in all_results:
                all_results[key] = r
                new_count += 1

        # 추가 페이지 (50개 이상 결과가 있으면)
        pages_fetched = 1
        max_pages = min((total // 50) + 1, 10)  # 최대 10페이지 (500개)

        while pages_fetched < max_pages:
            start = pages_fetched * 50 + 1
            more_results, _ = search_places_graphql(session, query, start=start, display=50)
            total_api_calls += 1

            if not more_results:
                break

            for r in more_results:
                if not is_relevant(r):
                    continue
                key = _dedup_key(r)
                if key not in all_results:
                    all_results[key] = r
                    new_count += 1

            pages_fetched += 1
            time.sleep(0.5)

        print(f" → total={total}, pages={pages_fetched}, new={new_count}")

        # 레이트 리밋 방지
        time.sleep(0.8)

        # 세션 갱신 (100번마다)
        if total_api_calls % 100 == 0:
            print("  [세션 갱신 중...]")
            time.sleep(3)
            session = create_place_session()

    print(f"\nGraphQL 완료: {len(all_results)}개 업체 (API 호출: {total_api_calls}회)")

    # --- Phase 2: 웹 검색 보조 (놓친 업체 수집) ---
    print("\n" + "=" * 60)
    print("  Phase 2: 네이버 웹 검색 (보조)")
    print("=" * 60)

    web_queries = []
    for kw in SEARCH_KEYWORDS:
        web_queries.append(kw)
    for kw in SEARCH_KEYWORDS[:10]:
        for region_name, sub_regions in REGIONS_DETAIL.items():
            for sr in sub_regions[:3]:  # 각 지역의 상위 3개만
                q = f"{sr} {kw}"
                if q not in web_queries:
                    web_queries.append(q)

    print(f"웹 검색어: {len(web_queries)}개\n")

    web_new = 0
    for i, query in enumerate(web_queries):
        print(f"[{i+1}/{len(web_queries)}] '{query}'", end="", flush=True)
        results = search_naver_web(query)
        new_count = 0

        for r in results:
            key = _dedup_key(r)
            if key not in all_results:
                all_results[key] = r
                new_count += 1
                web_new += 1

        print(f" → {len(results)}개 중 {new_count}개 신규")
        time.sleep(1.0)

    print(f"\n웹 검색 추가: {web_new}개")

    print(f"\n{'='*60}")
    print(f"  총 수집 완료: {len(all_results)}개 고유 업체")
    print(f"{'='*60}")

    return list(all_results.values())


def to_company_format(crawled: list[dict]) -> list[dict]:
    """크롤링 데이터 → 플랫폼 포맷"""
    companies = []
    for item in crawled:
        # 이름 정리
        name = item.get("name", "")
        name = re.sub(r"(톡톡|쿠폰)+", "", name).strip()
        name = re.sub(r"(법률사무소|법무사사무소)$", "", name).strip()
        if not name or len(name) < 2:
            continue

        region = _extract_region(item.get("address", ""))
        debt_types = _guess_debt_types(name)

        companies.append({
            "id": str(uuid.uuid4()),
            "name": name,
            "business_number": "",
            "contact_name": "",
            "contact_phone": item.get("phone", ""),
            "address": item.get("address", ""),
            "homepage": "",
            "status": "listed",
            "tier": "free",
            "verified": False,
            "filters": {
                "debt_types": debt_types,
                "regions": [region] if region else [],
            },
            "description": "",
            "min_fee": "",
            "experience_years": "",
            "success_count": 0,
            "rating": 0.0,
            "review_count": 0,
            "balance": 0,
            "naver_place_id": item.get("place_id", ""),
            "category": item.get("category", ""),
            "source": "naver_crawl",
            "created_at": datetime.now().isoformat(),
        })

    return companies


def _extract_region(address: str) -> str:
    region_map = {
        "서울": "서울", "경기": "경기", "인천": "인천", "부산": "부산",
        "대구": "대구", "대전": "대전", "광주": "광주", "울산": "울산",
        "세종": "세종", "강원": "강원", "충북": "충북", "충남": "충남",
        "전북": "전북", "전남": "전남", "경북": "경북", "경남": "경남",
        "제주": "제주",
        # 시 이름 → 도 매핑
        "수원": "경기", "성남": "경기", "고양": "경기", "용인": "경기",
        "부천": "경기", "안산": "경기", "안양": "경기", "남양주": "경기",
        "화성": "경기", "평택": "경기", "의정부": "경기", "시흥": "경기",
        "파주": "경기", "김포": "경기", "광명": "경기", "군포": "경기",
        "하남": "경기", "오산": "경기", "이천": "경기",
        "청주": "충북", "충주": "충북", "제천": "충북",
        "천안": "충남", "아산": "충남", "서산": "충남",
        "전주": "전북", "익산": "전북", "군산": "전북",
        "목포": "전남", "여수": "전남", "순천": "전남",
        "포항": "경북", "구미": "경북", "경주": "경북", "안동": "경북",
        "창원": "경남", "김해": "경남", "양산": "경남", "진주": "경남",
        "춘천": "강원", "원주": "강원", "강릉": "강원",
        "서귀포": "제주",
    }
    for key, value in region_map.items():
        if key in address:
            return value
    return ""


def _guess_debt_types(name: str) -> list[str]:
    types = []
    if "기업" in name or "법인회생" in name:
        types.append("corporate_recovery")
    if "기업파산" in name:
        types.append("corporate_bankruptcy")
    if "파산" in name or "면책" in name:
        types.append("personal_bankruptcy")
    if "회생" in name:
        types.append("personal_recovery")
    if not types:
        types = ["personal_recovery", "personal_bankruptcy"]
    return types


def save_results(companies: list[dict], filename: str = "crawled_companies.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(companies, f, ensure_ascii=False, indent=2)
    print(f"\n'{filename}'에 {len(companies)}개 업체 저장")


if __name__ == "__main__":
    print("=" * 60)
    print("  회생/파산 업체 크롤러 v2 (GraphQL + 웹 검색)")
    print("=" * 60)

    raw = crawl_all()
    companies = to_company_format(raw)
    save_results(companies)

    # 통계
    regions = {}
    categories = {}
    for c in companies:
        r = c["filters"]["regions"][0] if c["filters"]["regions"] else "기타"
        regions[r] = regions.get(r, 0) + 1
        cat = c.get("category", "") or "미분류"
        categories[cat] = categories.get(cat, 0) + 1

    print("\n지역별:")
    for r, cnt in sorted(regions.items(), key=lambda x: -x[1]):
        print(f"  {r}: {cnt}개")

    print("\n업종별:")
    for cat, cnt in sorted(categories.items(), key=lambda x: -x[1])[:10]:
        print(f"  {cat}: {cnt}개")
