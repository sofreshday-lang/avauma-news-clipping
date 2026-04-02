from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
import re
from difflib import SequenceMatcher

CATEGORY_KEYWORDS = ['유아동', '어패럴', '키즈', '패션', '아동', '유아', '베이비', '브랜드']

# 브랜드별 기본 제외 키워드 사전 (운영하며 추가 가능)
BRAND_EXCLUDE_MAP = {
    '리틀그라운드': ['배틀그라운드', '플레이그라운드', '캠핑그라운드'],
    '미니도우':     ['도우정보', '피자도우', '도우너츠'],
    '타오':         ['타오바오', '알리타오', '타오 모', '타오 씨'],
    '봉통':         ['봉통령', '봉통계'],
    '밍크뮤':       ['뮤지컬', '뮤지션', '뮤직'],
    '블루독':       ['독서', '독립', '독도', '독감'],
    '래핑차일드':   ['래핑 작업', '래핑 필름', '래핑 시공'],
}

def clean_html(raw_html):
    if not raw_html: return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    cleantext = (cleantext
                 .replace('&quot;', '"').replace('&apos;', "'")
                 .replace('&amp;', '&').replace('&lt;', '<')
                 .replace('&gt;', '>').replace('&middot;', '·'))
    return cleantext

def parse_pubdate(pubdate_str):
    try:
        return datetime.strptime(pubdate_str, "%a, %d %b %Y %H:%M:%S %z")
    except:
        return None

def is_similar(a, b, threshold=0.8):
    if not a or not b: return False
    return SequenceMatcher(None, a, b).ratio() >= threshold

def build_query(brand, extra, exclude_all):
    """
    정확 구문 검색(쌍따옴표) + 카테고리 + 사용자 추가어 + 마이너스 연산자
    예: '"리틀그라운드" 유아동 -배틀그라운드'
    """
    parts = [f'"{brand}"']
    if extra:
        parts.append(extra)
    if exclude_all:
        parts += [f'-{kw}' for kw in exclude_all if kw.strip()]
    return ' '.join(parts)

def contains_excluded(text, exclude_list):
    """후처리: 제목/본문에 제외 단어가 있으면 True"""
    if not exclude_list or not text:
        return False
    text_lower = text.lower()
    return any(kw.strip().lower() in text_lower for kw in exclude_list if kw.strip())

def brand_present(text, brand):
    """후처리: 제목/본문에 브랜드명이 실제로 포함되어 있는지 확인"""
    if not text:
        return False
    return brand.lower() in text.lower()

def fetch_raw_items(client_id, client_secret, query, api_count=100):
    try:
        encText = urllib.parse.quote(query)
        url = (f"https://openapi.naver.com/v1/search/news.json"
               f"?query={encText}&display={api_count}&sort=date")
        req = urllib.request.Request(url)
        req.add_header("X-Naver-Client-Id", client_id)
        req.add_header("X-Naver-Client-Secret", client_secret)
        response = urllib.request.urlopen(req)
        if response.getcode() != 200:
            return []
        data = json.loads(response.read().decode('utf-8'))
        return data.get('items', [])
    except:
        return []

def filter_and_deduplicate(raw_items, start_date, end_date,
                           display_count, exclude_list=None, brand=None):
    parsed_items = []
    for item in raw_items:
        p_date = parse_pubdate(item['pubDate'])
        if p_date and start_date <= p_date <= end_date:
            parsed_items.append({'original': item, 'date': p_date})

    parsed_items.sort(key=lambda x: x['date'], reverse=True)

    processed_items = []
    seen_links = set()
    accepted_titles = []

    for p_item in parsed_items:
        if len(processed_items) >= display_count:
            break
        item   = p_item['original']
        p_date = p_item['date']
        title  = clean_html(item['title'])
        desc   = clean_html(item['description'])
        link   = item['link']

        if link in seen_links:
            continue

        # ① 후처리: 제외 키워드 포함 기사 제거
        if contains_excluded(title, exclude_list) or contains_excluded(desc, exclude_list):
            continue

        # ② 후처리: 브랜드명이 제목 또는 본문에 없으면 제거
        if brand and not brand_present(title, brand) and not brand_present(desc, brand):
            continue

        is_duplicate = any(is_similar(title, t, 0.8) for t in accepted_titles)
        if is_duplicate:
            continue

        seen_links.add(link)
        accepted_titles.append(title)
        processed_items.append({
            'title':   title,
            'link':    link,
            'pubDate': p_date.strftime("%Y-%m-%d %H:%M:%S"),
            'description': desc
        })

    return processed_items

def process_news_search(client_id, client_secret, params):
    keywords        = params.get('keywords', [])
    custom_keyword  = params.get('custom_keyword', '').strip()
    logic           = params.get('logic', 'OR')
    display_count   = int(params.get('display', 50))
    # 사용자가 UI에서 직접 입력한 전역 제외 키워드
    user_excludes   = [k.strip() for k in params.get('exclude_keywords', []) if k.strip()]

    start_date_str = params.get('start_date')
    end_date_str   = params.get('end_date')

    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").replace(
            hour=0, minute=0, second=0).astimezone()
    else:
        start_date = (datetime.now() - timedelta(days=14)).astimezone()

    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59).astimezone()
    else:
        end_date = datetime.now().astimezone()

    final_results = {}

    for kw in keywords:
        # 브랜드별 사전 제외어 + 사용자 입력 제외어 합산
        brand_excludes = BRAND_EXCLUDE_MAP.get(kw, [])
        all_excludes   = list(set(brand_excludes + user_excludes))

        all_raw = []
        if logic == 'AND' and custom_keyword:
            for cat in CATEGORY_KEYWORDS:
                query = build_query(kw, f"{custom_keyword} {cat}", all_excludes)
                all_raw.extend(fetch_raw_items(client_id, client_secret, query))
        else:
            for cat in CATEGORY_KEYWORDS:
                query = build_query(kw, cat, all_excludes)
                all_raw.extend(fetch_raw_items(client_id, client_secret, query))

        final_results[kw] = filter_and_deduplicate(
            all_raw, start_date, end_date, display_count,
            exclude_list=all_excludes, brand=kw
        )

    if custom_keyword and logic == 'OR':
        query = build_query(custom_keyword, '', user_excludes)
        raw   = fetch_raw_items(client_id, client_secret, query)
        final_results[custom_keyword] = filter_and_deduplicate(
            raw, start_date, end_date, display_count,
            exclude_list=user_excludes, brand=None
        )

    return final_results


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID")
        CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET")

        if not CLIENT_ID or not CLIENT_SECRET:
            self.wfile.write(json.dumps({"error": "Missing Keys"}).encode('utf-8'))
            return

        try:
            length = int(self.headers.get('Content-Length', 0))
            params = json.loads(self.rfile.read(length).decode('utf-8'))
            results = process_news_search(CLIENT_ID, CLIENT_SECRET, params)
            self.wfile.write(json.dumps(results).encode('utf-8'))
        except Exception as e:
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
