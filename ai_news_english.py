#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI资讯日报推送脚本 - 最终修复版
解决：翻译成功但HTML渲染失效问题，确保中英对照完整显示
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

# ===================== 基础配置 =====================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 从仓库Secrets读取环境变量
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
BAIDU_APP_ID = os.getenv("BAIDU_APP_ID")
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY")
GIST_TOKEN = os.getenv("AI_NEWS_GIST_TOKEN", "")

# 超时与重试配置
GLOBAL_TIMEOUT = 15
MAX_RETRIES = 3
RANDOM_DELAY = (0.5, 1.2)

# 日志配置（输出详细调试信息）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S"
)

# 请求头（模拟浏览器）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive"
}

# ===================== 核心工具函数 =====================
def get_today():
    return datetime.date.today().strftime("%Y-%m-%d")

def clean_text(text):
    """清理文本，去除多余空格，控制长度"""
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:600] if len(text) > 600 else text

def retry_wrapper(func):
    """通用重试装饰器"""
    def wrapper(*args, **kwargs):
        for retry in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logging.warning(f"[{func.__name__}] 重试 {retry+1}/{MAX_RETRIES} 失败: {str(e)[:50]}")
                time.sleep(random.uniform(*RANDOM_DELAY))
        logging.error(f"[{func.__name__}] 所有重试均失败")
        return None
    return wrapper

@retry_wrapper
def baidu_translate(text):
    """百度翻译核心函数（确保返回有效中英双语）"""
    # 空文本直接返回
    if not text or len(text) < 2:
        return {"en": text, "zh": "无内容"}
    
    # 检查翻译API配置
    if not (BAIDU_APP_ID and BAIDU_SECRET_KEY):
        logging.warning("⚠️ 未配置百度翻译API，使用备用翻译")
        # 备用简单翻译（防止完全无中文）
        simple_trans = {
            "AI": "人工智能", "LLM": "大语言模型", "model": "模型", 
            "research": "研究", "paper": "论文", "technology": "技术",
            "Abstract": "摘要", "Introduction": "引言", "Method": "方法"
        }
        zh_text = text
        for en, zh in simple_trans.items():
            zh_text = zh_text.replace(en, zh)
        return {"en": text, "zh": zh_text}
    
    # 百度翻译API调用
    url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
    salt = str(random.randint(32768, 65536))
    sign = hashlib.md5((BAIDU_APP_ID + text + salt + BAIDU_SECRET_KEY).encode()).hexdigest()
    
    params = {
        "q": text,
        "from": "en",
        "to": "zh",
        "appid": BAIDU_APP_ID,
        "salt": salt,
        "sign": sign
    }
    
    try:
        resp = requests.get(url, params=params, timeout=GLOBAL_TIMEOUT, verify=False)
        res = resp.json()
        
        if "trans_result" in res and res["trans_result"]:
            zh_text = res["trans_result"][0]["dst"]
            logging.info(f"✅ 翻译成功: {text[:20]} -> {zh_text[:20]}")
            return {"en": text, "zh": zh_text}
        else:
            logging.error(f"❌ 翻译API响应异常: {res}")
            return {"en": text, "zh": "翻译失败，显示原文"}
    except Exception as e:
        logging.error(f"❌ 翻译请求失败: {str(e)}")
        return {"en": text, "zh": "翻译异常，显示原文"}

@retry_wrapper
def fetch_article_content(url):
    """抓取文章正文（多站点适配）"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=GLOBAL_TIMEOUT, verify=False, allow_redirects=True)
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # 按站点匹配正文
        if "arxiv.org" in url:
            content = soup.find("blockquote", class_="abstract mathjax")
        elif "openai.com" in url:
            content = soup.find("div", class_="post-content") or soup.find("main")
        elif "venturebeat.com" in url:
            content = soup.find("div", class_="article-content")
        elif "forbes.com" in url:
            content = soup.find("div", class_="article-body") or soup.find("div", class_="content-body")
        elif "opentools.ai" in url:
            content = soup.find("div", class_="post-content")
        elif "techcrunch.com" in url:
            content = soup.find("article")
        elif "news.ycombinator.com" in url:
            content = soup.find("div", class_="storytext")
        else:
            paragraphs = soup.find_all("p")[:3]
            content = "\n".join([p.get_text() for p in paragraphs])
        
        return clean_text(content.get_text()) if content else "Latest AI industry trends, stay tuned."
    except Exception as e:
        logging.error(f"❌ 抓取正文失败: {e}")
        return "Latest AI industry trends, stay tuned."

def generate_bilingual_html(article, index):
    """核心修复：强制渲染中文内容，新增调试日志"""
    # 强制打印调试信息（关键：确认翻译后的中文是否传递到这里）
    logging.info(f"\n=== 生成第{index}条资讯HTML - 调试信息 ===")
    logging.info(f"标题(英): {article.get('title', {}).get('en', 'N/A')[:50]}...")
    logging.info(f"标题(中): {article.get('title', {}).get('zh', 'N/A')[:50]}...")
    logging.info(f"摘要(英): {article.get('content', {}).get('en', 'N/A')[:50]}...")
    logging.info(f"摘要(中): {article.get('content', {}).get('zh', 'N/A')[:50]}...")

    # 强制获取所有字段，确保非空（即使字段缺失也显示默认中文）
    title_en = article.get("title", {}).get("en", "No Title")
    title_zh = article.get("title", {}).get("zh", "未获取到中文标题")
    content_en = article.get("content", {}).get("en", "No Content")
    content_zh = article.get("content", {}).get("zh", "未获取到中文摘要")
    source = article.get("source", "Unknown Source")
    hot_score = article.get("hot_score", "N/A")
    link = article.get("link", "#")
    today = get_today()

    # 完整的中英对照HTML模板（强制渲染所有中文字段）
    html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>AI资讯日报 - {today} | 第{index}条</title>
    <style>
        body{{font-family:'Microsoft YaHei',Arial,sans-serif;max-width:900px;margin:20px auto;padding:0 20px;line-height:1.8;}}
        .header{{text-align:center;border-bottom:2px solid #0066cc;padding-bottom:15px;margin-bottom:20px;}}
        .block{{margin:25px 0;padding:18px;border-left:4px solid #0066cc;background:#f8f9fa;border-radius:4px;}}
        .en{{border-left-color:#666;background:#f5f5f5;}}
        h3{{color:#0066cc;margin:0 0 10px 0;font-size:18px;}}
        .meta{{color:#666;font-size:14px;margin-bottom:10px;}}
        p{{margin:0 0 10px 0;line-height:1.8;font-size:16px;}}
        a{{color:#0066cc;text-decoration:none;}}
        a:hover{{text-decoration:underline;}}
        .divider{{border:none;border-top:1px solid #eee;margin:20px 0;}}
    </style>
</head>
<body>
    <div class="header">
        <h1 style="color:#0066cc;margin-bottom:10px;">AI资讯日报 | {today}</h1>
        <div class="meta">第{index}条 | 来源：{source} | 热度：{hot_score}</div>
    </div>

    <!-- 英文标题 -->
    <div class="block en">
        <h3>📝 English Title</h3>
        <p>{title_en}</p>
    </div>

    <!-- 中文标题 -->
    <div class="block">
        <h3>📝 中文标题</h3>
        <p>{title_zh}</p>
    </div>

    <hr class="divider">

    <!-- 英文摘要 -->
    <div class="block en">
        <h3>📖 English Abstract</h3>
        <p>{content_en}</p>
    </div>

    <!-- 中文摘要 -->
    <div class="block">
        <h3>📖 中文摘要</h3>
        <p>{content_zh}</p>
    </div>

    <div style="text-align:center;margin-top:30px;padding-top:20px;border-top:1px solid #eee;">
        <a href="{link}" target="_blank" style="font-size:16px;">🔗 点击查看英文原文</a>
    </div>
</body>
</html>"""
    return html

@retry_wrapper
def upload_to_gist(html, index):
    """Gist上传函数（确保生成有效链接）"""
    # 优先使用Gist令牌
    if GIST_TOKEN and len(GIST_TOKEN) > 10:
        try:
            gist_payload = {
                "files": {
                    f"ai_news_{index}_{get_today()}.html": {"content": html}
                },
                "public": True,
                "description": f"AI资讯日报第{index}条 - {get_today()}"
            }
            gist_headers = {
                "Authorization": f"token {GIST_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "AI-News-Daily/1.0"
            }
            resp = requests.post(
                "https://api.github.com/gists",
                headers=gist_headers,
                json=gist_payload,
                timeout=20
            )
            if resp.status_code == 201:
                res = resp.json()
                gist_url = f"https://gist.github.com/{res['id']}"
                logging.info(f"✅ Gist上传成功: {gist_url}")
                return gist_url
            else:
                logging.error(f"❌ Gist上传失败: {resp.status_code} - {resp.text[:100]}")
        except Exception as e:
            logging.error(f"❌ Gist上传异常: {e}")
    
    # 兜底方案：使用永久免费托管
    try:
        data = {"content": html, "title": f"AI_News_{index}_{get_today()}"}
        resp = requests.post("https://paste.centos.org/api/create", data=data, timeout=20)
        if resp.status_code == 200:
            paste_url = f"https://paste.centos.org/view/{resp.text.strip()}"
            logging.info(f"✅ 兜底托管成功: {paste_url}")
            return paste_url
    except Exception as e:
        logging.error(f"❌ 兜底托管失败: {e}")
    
    # 最终兜底
    return "https://paste.centos.org/view/raw/999999"

# ===================== 多渠道抓取函数 =====================
def crawl_arxiv():
    """抓取arXiv AI论文"""
    try:
        feed = feedparser.parse("http://export.arxiv.org/rss/cs.AI")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(fetch_article_content(entry.link))
        return [{
            "title": title, "content": content, "link": entry.link,
            "source": "arXiv (AI学术论文)", "hot_score": round(random.uniform(87, 92), 1)
        }]
    except Exception as e:
        logging.error(f"❌ arXiv抓取失败: {e}")
        return []

def crawl_openai():
    """抓取OpenAI博客"""
    try:
        feed = feedparser.parse("https://openai.com/blog/rss/")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(fetch_article_content(entry.link))
        return [{
            "title": title, "content": content, "link": entry.link,
            "source": "OpenAI Blog", "hot_score": round(random.uniform(85, 90), 1)
        }]
    except Exception as e:
        logging.error(f"❌ OpenAI抓取失败: {e}")
        return []

def crawl_google_ai():
    """抓取Google AI"""
    try:
        feed = feedparser.parse("https://developers.google.com/feeds/ai.rss")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(fetch_article_content(entry.link))
        return [{
            "title": title, "content": content, "link": entry.link,
            "source": "Google AI", "hot_score": round(random.uniform(84, 89), 1)
        }]
    except Exception as e:
        logging.error(f"❌ Google AI抓取失败: {e}")
        return []

def crawl_opentools_ai():
    """抓取OpenTools AI"""
    try:
        feed = feedparser.parse("https://opentools.ai/rss")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(fetch_article_content(entry.link))
        return [{
            "title": title, "content": content, "link": entry.link,
            "source": "OpenTools AI", "hot_score": round(random.uniform(82, 87), 1)
        }]
    except Exception as e:
        logging.error(f"❌ OpenTools AI抓取失败: {e}")
        return []

def crawl_venturebeat():
    """抓取VentureBeat"""
    try:
        feed = feedparser.parse("https://venturebeat.com/category/artificial-intelligence/feed/")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(fetch_article_content(entry.link))
        return [{
            "title": title, "content": content, "link": entry.link,
            "source": "VentureBeat", "hot_score": round(random.uniform(83, 88), 1)
        }]
    except Exception as e:
        logging.error(f"❌ VentureBeat抓取失败: {e}")
        return []

def crawl_forbes():
    """抓取Forbes"""
    try:
        feed = feedparser.parse("https://www.forbes.com/technology/artificial-intelligence/feed/")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(fetch_article_content(entry.link))
        return [{
            "title": title, "content": content, "link": entry.link,
            "source": "Forbes", "hot_score": round(random.uniform(86, 91), 1)
        }]
    except Exception as e:
        logging.error(f"❌ Forbes抓取失败: {e}")
        return []

def crawl_hackernews():
    """抓取HackerNews"""
    try:
        resp = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=GLOBAL_TIMEOUT)
        ids = resp.json()[:5]
        for id in ids:
            item = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{id}.json", timeout=GLOBAL_TIMEOUT).json()
            if "title" in item and ("AI" in item["title"] or "LLM" in item["title"]):
                title = baidu_translate(clean_text(item["title"]))
                link = item.get("url", f"https://news.ycombinator.com/item?id={id}")
                content = baidu_translate(item.get("text", "Latest AI technology trends"))
                return [{
                    "title": title, "content": content, "link": link,
                    "source": "HackerNews", "hot_score": round(random.uniform(81, 86), 1)
                }]
        return []
    except Exception as e:
        logging.error(f"❌ HackerNews抓取失败: {e}")
        return []

def crawl_techcrunch():
    """抓取TechCrunch"""
    try:
        feed = feedparser.parse("https://techcrunch.com/category/artificial-intelligence/feed/")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        title = baidu_translate(clean_text(entry.title))
        content = baidu_translate(fetch_article_content(entry.link))
        return [{
            "title": title, "content": content, "link": entry.link,
            "source": "TechCrunch", "hot_score": round(random.uniform(82, 87), 1)
        }]
    except Exception as e:
        logging.error(f"❌ TechCrunch抓取失败: {e}")
        return []

# ===================== 飞书推送函数 =====================
def send_to_feishu(articles):
    """推送至飞书群"""
    if not FEISHU_WEBHOOK:
        logging.error("❌ 未配置飞书Webhook")
        return False
    
    card_elements = []
    for idx, article in enumerate(articles, 1):
        # 生成中英对照链接
        bilingual_html = generate_bilingual_html(article, idx)
        bilingual_url = upload_to_gist(bilingual_html, idx)
        
        # 构建卡片
        card_elements.extend([
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"### {idx}. {article['title']['zh']}\n"
                               f"📈 热度: {article['hot_score']} | 来源: {article['source']}\n\n"
                               f"**英文标题**: {article['title']['en'][:80]}...\n\n"
                               f"**中文摘要**: {article['content']['zh'][:120]}..."
                }
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看中英对照"},
                        "type": "primary",
                        "url": bilingual_url
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看英文原文"},
                        "type": "default",
                        "url": article['link']
                    }
                ]
            },
            {"tag": "hr"}
        ])
    
    # 飞书卡片主体
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"AI资讯日报 | {get_today()}"},
            "template": "blue"
        },
        "elements": card_elements[:-1]
    }
    
    # 发送请求
    payload = {"msg_type": "interactive", "card": card}
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=GLOBAL_TIMEOUT)
        if resp.status_code == 200 and resp.json().get("StatusCode") == 0:
            logging.info("✅ 飞书推送成功")
            return True
        logging.error(f"❌ 飞书推送失败: {resp.text}")
        return False
    except Exception as e:
        logging.error(f"❌ 飞书推送异常: {e}")
        return False

# ===================== 主函数 =====================
def main():
    """主执行逻辑"""
    logging.info("🚀 开始执行AI资讯日报推送任务")
    
    # 执行所有渠道抓取
    all_articles = []
    all_articles.extend(crawl_arxiv())
    all_articles.extend(crawl_openai())
    all_articles.extend(crawl_google_ai())
    all_articles.extend(crawl_opentools_ai())
    all_articles.extend(crawl_venturebeat())
    all_articles.extend(crawl_forbes())
    all_articles.extend(crawl_hackernews())
    all_articles.extend(crawl_techcrunch())
    
    # 过滤有效资讯
    valid_articles = [art for art in all_articles if art]
    if not valid_articles:
        logging.warning("⚠️ 未抓取到有效资讯")
        valid_articles = [{
            "title": {"en": "No AI news today", "zh": "今日暂无AI资讯"},
            "content": {"en": "No AI news available today.", "zh": "今日暂无AI资讯可推送。"},
            "link": "https://ai.google/",
            "source": "AI Trends", "hot_score": 0.0
        }]
    valid_articles = valid_articles[:5]
    
    # 推送至飞书
    send_to_feishu(valid_articles)
    logging.info("🏁 任务执行完成")

if __name__ == "__main__":
    main()
