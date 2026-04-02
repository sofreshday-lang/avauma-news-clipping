from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
import re
from difflib import SequenceMatcher

# 브랜드 키워드 검색 시 자동으로 AND 조합되는 카테고리 키워드 (이 중 하나만 포함되면 검색됨)
CATEGORY_KEYWORDS = ['유아동', '어패럴', '키즈', '패션']

def clean_html(raw_html):
    if not raw_html: return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    cleantext = cleantext.replace('&quot;', '"').replace('&apos;', "'").replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&middot;', '·')
    return cleantext

def parse_pubdate(pubdate_str):
    try:
        return datetime.strptime(pubdate_str, "%a, %d %b %Y %H:%M:%S %z")
    except:
        return None

def is_similar(a, b, threshold=0.8):
    if not a or not b: return False
    return SequenceMatcher(None, a, b).ratio() >= threshold

def fetch_raw_items(client_id, client_secret, query, api_count=100):
    try:
        encText = urllib.parse.quote(query)
        url = f"https://openapi.naver.com/v1/search/news.json?query={encText}&display={api_count}&sort=date"
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

def filter_and_deduplicate(raw_items, start_date, end_date, display_count):
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
        item = p_item['original']
        p_date = p_item['date']
        title = clean_html(item['title'])
        link = item['link']

        if link in seen_links:
            continue

        is_duplicate = any(is_similar(title, t, 0.8) for t in accepted_titles)
        if is_duplicate:
            continue

        seen_links.add(link)
        accepted_titles.append(title)
        processed_items.append({
            'title': title,
            'link': link,
            'pubDate': p_date.strftime("%Y-%m-%d %H:%M:%S"),
            'description': clean_html(item['description'])
        })

    return processed_items

def process_news_search(client_id, client_secret, params):
    keywords = params.get('keywords', [])
    custom_keyword = params.get('custom_keyword', '').strip()
    logic = params.get('logic', 'OR')
    display_count = int(params.get('display', 50))

    start_date_str = params.get('start_date')
    end_date_str = params.get('end_date')

    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").replace(hour=0, minute=0, second=0).astimezone()
    else:
        start_date = (datetime.now() - timedelta(days=14)).astimezone()

    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59).astimezone()
    else:
        end_date = datetime.now().astimezone()

    final_results = {}

    for kw in keywords:
        all_raw = []
        if logic == 'AND' and custom_keyword:
            for cat in CATEGORY_KEYWORDS:
                query = f"{kw} {custom_keyword} {cat}"
                all_raw.extend(fetch_raw_items(client_id, client_secret, query))
        else:
            for cat in CATEGORY_KEYWORDS:
                query = f"{kw} {cat}"
                all_raw.extend(fetch_raw_items(client_id, client_secret, query))

        final_results[kw] = filter_and_deduplicate(all_raw, start_date, end_date, display_count)

    if custom_keyword and logic == 'OR':
        raw = fetch_raw_items(client_id, client_secret, custom_keyword)
        final_results[custom_keyword] = filter_and_deduplicate(raw, start_date, end_date, display_count)

    return final_results


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        CLIENT_ID = os.environ.get("NAVER_CLIENT_ID")
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
