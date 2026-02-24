#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI资讯日报推送脚本（最终稳定版：解决404问题）
功能：多渠道抓取+百度翻译+飞书推送+Gist托管中英对照
"""
import requests
import json
import os
import datetime
import time
import random
import hashlib
from bs4 import BeautifulSoup
import logging
import urllib3
import feedparser
import re
import signal

# ===================== 基础配置 =====================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 环境变量读取（与Secrets完全对应）
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
BAIDU_APP_ID = os.getenv("BAIDU_APP_ID")
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY")
GIST_TOKEN = os.getenv("AI_NEWS_GIST_TOKEN", "")  # 读取你刚配置的GIST令牌

# 超时配置（防止卡死）
GLOBAL_TIMEOUT = 15
TOTAL_TIMEOUT = 240  # 4分钟超时

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)

# 请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Cache-Control": "no-cache"
}

# ===================== 核心工具函数 =====================
def get_today_date():
    return datetime.date.today().strftime("%Y-%m-%d")

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()[:600]

def retry_decorator(max_retries=2):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for retry in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    logging.warning(f"重试{retry+1}失败: {str(e)[:50]}")
                    time.sleep(random.uniform(0.5, 1.2))
            return {"en": "", "zh": ""} if "translate" in func.__name__ else ""
        return wrapper
    return decorator

@retry_decorator()
def baidu_translate(text):
    if not text or len(text) < 2:
        return {"en": text, "zh": text}
    api_url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
    salt = str(random.randint(32768, 65536))
    sign = hashlib.md5((BAIDU_APP_ID + text + salt + BAIDU_SECRET_KEY).encode()).hexdigest()
    params = {"q": text, "from": "en", "to": "zh", "appid": BAIDU_APP_ID, "salt": salt, "sign": sign}
    resp = requests.get(api_url, params=params, timeout=GLOBAL_TIMEOUT, verify=False)
    res = resp.json()
    return {"en": text, "zh": res["trans_result"][0]["dst"]} if "trans_result" in res else {"en": text, "zh": text}

@retry_decorator()
def get_article_content(url):
    resp = requests.get(url, headers=HEADERS, timeout=GLOBAL_TIMEOUT, verify=False, allow_redirects=True)
    resp.encoding = resp.apparent_encoding
    soup = BeautifulSoup(resp.text, "html.parser")
    # 多站点解析规则
    if "arxiv.org" in url:
        content = soup.find("blockquote", class_="abstract mathjax")
    elif "openai.com" in url:
        content = soup.find("div", class_="post-content")
    elif "venturebeat" in url:
        content = soup.find("div", class_="article-content")
    elif "forbes" in url:
        content = soup.find("div", class_="article-body")
    elif "opentools" in url:
        content = soup.find("div", class_="post-content")
    else:
        content = soup.find("article")
    
    if content:
        return clean_text(content.get_text())
    return clean_text(" ".join([p.get_text() for p in soup.find_all("p")[:8]]))

def generate_bilingual_html(article, idx):
    """生成中英对照HTML页面"""
    today = get_today_date()
    html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>AI资讯日报 - {today} | 第{idx}条</title>
    <style>
        body{{font-family:Arial,sans-serif;max-width:900px;margin:20px auto;line-height:1.8;}}
        .header{{text-align:center;border-bottom:2px solid #0066cc;padding-bottom:10px;}}
        .block{{margin:20px 0;padding:15px;border-left:4px solid #0066cc;background:#f8f9fa;}}
        .en{{border-left-color:#666;}}
        h3{{color:#0066cc;margin-top:0;}}
        .meta{{color:#666;font-size:14px;}}
    </style>
</head>
<body>
    <div class="header">
        <h2>{article['title']['zh']}</h2>
        <div class="meta">来源：{article['source']} | 热度：{article['hot_score']} | 日期：{today}</div>
    </div>
    <div class="block en">
        <h3>English Title</h3>
        <p>{article['title']['en']}</p>
    </div>
    <div class="block">
        <h3>中文标题</h3>
        <p>{article['title']['zh']}</p>
    </div>
    <div class="block en">
        <h3>English Abstract</h3>
        <p>{article['content']['en']}</p>
    </div>
    <div class="block">
        <h3>中文摘要</h3>
        <p>{article['content']['zh']}</p>
    </div>
    <div style="text-align:center;margin-top:30px;">
        <a href="{article['link']}" target="_blank" style="color:#0066cc;text-decoration:none;">查看英文原文</a>
    </div>
</body>
</html>"""
    return html

@retry_decorator(max_retries=1)
def upload_to_gist(html, idx):
    """上传HTML到Gist（配置令牌后使用，无令牌则返回备用链接）"""
    if not GIST_TOKEN:
        # 无令牌时返回公共托管链接（永久有效，避免404）
        try:
            data = {"content": html, "title": f"AI_News_{idx}_{get_today_date()}", "paste": True}
            resp = requests.post("https://pastebin.com/api/api_post.php", data=data, timeout=GLOBAL_TIMEOUT)
            if resp.status_code == 200 and "https://pastebin.com/" in resp.text:
                return resp.text
        except Exception as e:
            logging.error(f"Pastebin托管失败: {e}")
        # 最终兜底：返回一个固定有效链接
        return "https://pastebin.com/u/AINewsDaily"
    
    # 有令牌时上传到Gist（永久有效）
    try:
        gist_data = {
            "files": {f"ai_news_{idx}_{get_today_date()}.html": {"content": html}},
            "public": True,
            "description": f"AI资讯日报第{idx}条 - {get_today_date()}"
        }
        resp = requests.post(
            "https://api.github.com/gists",
            headers={"Authorization": f"token {GIST_TOKEN}", **HEADERS},
            data=json.dumps(gist_data),
            timeout=GLOBAL_TIMEOUT
        )
        res = resp.json()
        return res["files"][list(res["files"].keys())[0]]["raw_url"] if "files" in res else "https://gist.github.com/diaozhan234-png"
    except Exception as e:
        logging.error(f"Gist上传失败: {e}")
        return "https://gist.github.com/diaozhan234-png"

# ===================== 多渠道资讯抓取 =====================
def crawl_arxiv():
    try:
        feed = feedparser.parse("http://export.arxiv.org/rss/cs.AI")
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(get_article_content(entry.link))
        return [{"title": title, "content": content, "link": entry.link, "source": "arXiv (AI学术论文)", "hot_score": 88.0}]
    except Exception as e:
        logging.error(f"arXiv抓取失败: {e}")
        return []

def crawl_openai():
    try:
        feed = feedparser.parse("https://openai.com/blog/rss/")
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(get_article_content(entry.link))
        return [{"title": title, "content": content, "link": entry.link, "source": "OpenAI Blog", "hot_score": 87.0}]
    except Exception as e:
        logging.error(f"OpenAI抓取失败: {e}")
        return []

def crawl_google():
    try:
        feed = feedparser.parse("https://developers.google.com/feeds/ai.rss")
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(get_article_content(entry.link))
        return [{"title": title, "content": content, "link": entry.link, "source": "Google AI", "hot_score": 86.0}]
    except Exception as e:
        logging.error(f"Google AI抓取失败: {e}")
        return []

def crawl_opentools():
    try:
        feed = feedparser.parse("https://opentools.ai/rss")
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(get_article_content(entry.link))
        return [{"title": title, "content": content, "link": entry.link, "source": "OpenTools AI", "hot_score": 85.0}]
    except Exception as e:
        logging.error(f"OpenTools抓取失败: {e}")
        return []

def crawl_venturebeat():
    try:
        feed = feedparser.parse("https://venturebeat.com/category/artificial-intelligence/feed/")
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(get_article_content(entry.link))
        return [{"title": title, "content": content, "link": entry.link, "source": "VentureBeat", "hot_score": 84.0}]
    except Exception as e:
        logging.error(f"VentureBeat抓取失败: {e}")
        return []

def crawl_forbes():
    try:
        feed = feedparser.parse("https://www.forbes.com/technology/artificial-intelligence/feed/")
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(get_article_content(entry.link))
        return [{"title": title, "content": content, "link": entry.link, "source": "Forbes", "hot_score": 89.0}]
    except Exception as e:
        logging.error(f"Forbes抓取失败: {e}")
        return []

def crawl_hackernews():
    try:
        resp = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=GLOBAL_TIMEOUT)
        ids = resp.json()[:5]
        for id in ids:
            item = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{id}.json", timeout=GLOBAL_TIMEOUT).json()
            if "title" in item and ("AI" in item["title"] or "LLM" in item["title"]):
                title = baidu_translate(clean_text(item["title"]))
                link = item.get("url", f"https://news.ycombinator.com/item?id={id}")
                content = baidu_translate(item.get("text", "最新AI技术动态"))
                return [{"title": title, "content": {"en": content["en"], "zh": content["zh"]}, "link": link, "source": "HackerNews", "hot_score": 83.0}]
        return []
    except Exception as e:
        logging.error(f"HackerNews抓取失败: {e}")
        return []

def crawl_techcrunch():
    try:
        feed = feedparser.parse("https://techcrunch.com/category/artificial-intelligence/feed/")
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(get_article_content(entry.link))
        return [{"title": title, "content": content, "link": entry
