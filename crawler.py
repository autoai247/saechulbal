"""
네이버 검색에서 회생/파산 관련 업체 크롤링
네이버 웹 검색 결과의 플레이스 섹션 + 개별 place 상세 페이지 파싱
"""

import requests
from bs4 import BeautifulSoup
import json
import time
import uuid
import re
from datetime import datetime
from urllib.parse import quote, urljoin

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

SEARCH_KEYWORDS = [
    "개인회생 법무법인",
    "개인회생 법률사무소",
    "개인파산 법무법인",
    "개인파산 법률사무소",
    "기업회생 법무법인",
    "회생파산 전문",
    "개인회생 전문 변호사",
    "채무조정 법무법인",
    "개인회생 상담",
    "파산면책 법률사무소",
]

REGIONS = [
    "서울", "경기", "인천", "부산", "대구", "대전", "광주",
    "울산", "수원", "성남", "고양", "용인", "창원", "청주",
    "전주", "천안", "제주",
]


def search_naver_places(keyword: str) -> list[dict]:
    """네이버 검색에서 플레이스 결과 추출"""
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

        # place_bluelink 링크에서 업체 정보 추출
        links = soup.select("a.place_bluelink")
        for link in links:
            name = link.get_text(strip=True)
            href = link.get("href", "")

            # place ID 추출
            place_id = ""
            m = re.search(r"/place/(\d+)", href)
            if m:
                place_id = m.group(1)

            # 이름에서 카테고리 태그 제거
            name = re.sub(r"(법률사무소|법무법인|쿠폰|톡톡)$", "", name).strip()
            if not name:
                continue

            # 부모 컨테이너에서 주소/전화번호 추출
            container = link
            for _ in range(8):
                container = container.parent
                if container is None:
                    break

            address = ""
            phone = ""
            category = ""
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
                "source_url": href,
            })

        return results

    except Exception as e:
        print(f"  [!] Error: {e}")
        return []


def get_place_detail(place_id: str) -> dict:
    """네이버 플레이스 상세 정보 가져오기"""
    if not place_id:
        return {}

    try:
        url = f"https://pcmap.place.naver.com/place/{place_id}/home"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return {}

        soup = BeautifulSoup(r.text, "html.parser")

        # JSON-LD 또는 script 태그에서 구조화된 데이터 추출
        scripts = soup.select("script")
        for script in scripts:
            text = script.string or ""
            if '"name"' in text and '"address"' in text:
                try:
                    # window.__APOLLO_STATE__ 등에서 데이터 추출
                    match = re.search(r'\{["\']@type["\'].*?\}', text)
                    if match:
                        data = json.loads(match.group())
                        return data
                except:
                    pass

        return {}
    except:
        return {}


def crawl_all() -> list[dict]:
    """모든 키워드 + 지역으로 크롤링"""
    all_results = {}  # name -> data (중복 제거)

    # 키워드 + 지역 조합
    search_queries = []
    for kw in SEARCH_KEYWORDS:
        search_queries.append(kw)
    for kw in SEARCH_KEYWORDS[:5]:
        for region in REGIONS:
            search_queries.append(f"{region} {kw}")

    print(f"총 {len(search_queries)}개 검색어\n")

    for i, query in enumerate(search_queries):
        print(f"[{i+1}/{len(search_queries)}] '{query}'")
        results = search_naver_places(query)

        for r in results:
            key = r["name"]
            # 중복 체크 (이름 기반)
            if key not in all_results:
                all_results[key] = r
                print(f"  + {r['name']} | {r['address']} | {r['phone']}")

        time.sleep(1.0)  # 차단 방지

    print(f"\n수집 완료: {len(all_results)}개 업체")
    return list(all_results.values())


def to_company_format(crawled: list[dict]) -> list[dict]:
    """크롤링 데이터 → 플랫폼 포맷"""
    companies = []
    for item in crawled:
        region = _extract_region(item.get("address", ""))
        debt_types = _guess_debt_types(item.get("name", ""))

        companies.append({
            "id": str(uuid.uuid4()),
            "name": item["name"],
            "business_number": "",
            "contact_name": "",
            "contact_phone": item.get("phone", ""),
            "address": item.get("address", ""),
            "homepage": "",
            "status": "listed",  # 크롤링 등록 (미인증)
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
    }
    for key, value in region_map.items():
        if key in address:
            return value
    return ""


def _guess_debt_types(name: str) -> list[str]:
    types = []
    if "기업" in name:
        types.append("corporate_recovery")
    if "파산" in name:
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
    print("=" * 50)
    print("  회생/파산 업체 크롤러 (네이버 검색)")
    print("=" * 50)

    raw = crawl_all()
    companies = to_company_format(raw)
    save_results(companies)

    # 통계
    regions = {}
    for c in companies:
        r = c["filters"]["regions"][0] if c["filters"]["regions"] else "기타"
        regions[r] = regions.get(r, 0) + 1
    print("\n지역별:")
    for r, cnt in sorted(regions.items(), key=lambda x: -x[1]):
        print(f"  {r}: {cnt}개")
