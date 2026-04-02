from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
import re
from difflib import SequenceMatcher

# Helper to remove HTML tags and decode entities
def clean_html(raw_html):
    if not raw_html:
        return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    # Basic entity decoding
    cleantext = cleantext.replace('&quot;', '"').replace('&apos;', "'").replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&middot;', '·')
    return cleantext

def parse_pubdate(pubdate_str):
    try:
        return datetime.strptime(pubdate_str, "%a, %d %b %Y %H:%M:%S %z")
    except:
        return None

def is_similar(a, b, threshold=0.8):
    len_a, len_b = len(a), len(b)
    if len_a == 0 or len_b == 0: return False
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= threshold

def process_news_search(client_id, client_secret, keywords, display_count=50):
    now = datetime.now().astimezone()
    fourteen_days_ago = now - timedelta(days=14)
    final_results = {}

    for keyword in keywords:
        try:
            encText = urllib.parse.quote(keyword)
            url = f"https://openapi.naver.com/v1/search/news.json?query={encText}&display={display_count}&sort=date"
            
            req = urllib.request.Request(url)
            req.add_header("X-Naver-Client-Id", client_id)
            req.add_header("X-Naver-Client-Secret", client_secret)
            
            response = urllib.request.urlopen(req)
            if response.getcode() != 200:
                final_results[keyword] = []
                continue
                
            data = json.loads(response.read().decode('utf-8'))
            items = data.get('items', [])
            
            # 1. Parse & Sort (Newest First)
            parsed_items = []
            for item in items:
                p_date = parse_pubdate(item['pubDate'])
                if p_date:
                    parsed_items.append({'original': item, 'date': p_date})
            
            parsed_items.sort(key=lambda x: x['date'], reverse=True)

            processed_items = []
            
            # 2. Filter & Advanced Deduplication
            seen_links = set()
            accepted_titles = [] 

            for p_item in parsed_items:
                item = p_item['original']
                p_date = p_item['date']

                if p_date < fourteen_days_ago:
                    continue

                title = clean_html(item['title'])
                link = item['link']
                
                # A. Exact Link Check
                if link in seen_links: continue

                # B. Fuzzy Title Check
                is_duplicate_title = False
                for acc_title in accepted_titles:
                    if is_similar(title, acc_title, 0.8):
                        is_duplicate_title = True
                        break
                
                if is_duplicate_title:
                    continue

                seen_links.add(link)
                accepted_titles.append(title)
                
                processed_items.append({
                    'title': title,
                    'link': link,
                    'pubDate': p_date.strftime("%Y-%m-%d %H:%M:%S"),
                    'description': clean_html(item['description'])
                })
            
            final_results[keyword] = processed_items
            
        except Exception:
            final_results[keyword] = []
            
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
            self.wfile.write(json.dumps({"error": "Missing Server API Keys"}).encode('utf-8'))
            return

        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length).decode('utf-8'))
        except:
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode('utf-8'))
            return

        keywords = body.get('keywords', [])
        display = body.get('display', 50)
        
        results = process_news_search(CLIENT_ID, CLIENT_SECRET, keywords, display)
        self.wfile.write(json.dumps(results).encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
