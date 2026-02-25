#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI资讯日报推送脚本 - 左右分栏中英对照版（修复版）
核心修复：Gist Raw URL 改为 htmlpreview.github.io 渲染链接
新增来源：opentools.ai、VentureBeat、Forbes
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

# 日志配置
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
    text = text.replace("\n", " ").replace("\r", "")
    return text[:800] if len(text) > 800 else text

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
    """百度翻译核心函数"""
    if not text or len(text) < 2:
        return {"en": text, "zh": "无内容"}

    if not (BAIDU_APP_ID and BAIDU_SECRET_KEY):
        logging.warning("⚠️ 未配置百度翻译API，使用备用翻译")
        simple_trans = {
            "AI": "人工智能", "LLM": "大语言模型", "model": "模型",
            "research": "研究", "paper": "论文", "technology": "技术",
            "Abstract": "摘要", "Introduction": "引言", "Method": "方法",
        }
        zh_text = text
        for en, zh in simple_trans.items():
            zh_text = zh_text.replace(en, zh)
        return {"en": text, "zh": zh_text}

    url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
    salt = str(random.randint(32768, 65536))
    sign = hashlib.md5((BAIDU_APP_ID + text + salt + BAIDU_SECRET_KEY).encode()).hexdigest()
    params = {
        "q": text, "from": "en", "to": "zh",
        "appid": BAIDU_APP_ID, "salt": salt, "sign": sign
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

        if "arxiv.org" in url:
            content = soup.find("blockquote", class_="abstract mathjax")
        elif "openai.com" in url:
            content = soup.find("div", class_="post-content") or soup.find("main")
        elif "venturebeat.com" in url:
            content = soup.find("div", class_="article-content") or soup.find("article")
        elif "forbes.com" in url:
            content = soup.find("div", class_="article-body") or soup.find("div", class_="content-body")
        elif "opentools.ai" in url:
            content = soup.find("div", class_="post-content") or soup.find("article")
        elif "techcrunch.com" in url:
            content = soup.find("article")
        elif "news.ycombinator.com" in url:
            content = soup.find("div", class_="storytext")
        else:
            paragraphs = soup.find_all("p")[:3]
            content = "\n".join([p.get_text() for p in paragraphs])

        return clean_text(content.get_text()) if hasattr(content, 'get_text') else clean_text(str(content)) if content else "Latest AI industry trends, stay tuned."
    except Exception as e:
        logging.error(f"❌ 抓取正文失败: {e}")
        return "Latest AI industry trends, stay tuned."

# ===================== 生成渲染友好的HTML =====================
def generate_bilingual_html(article, index):
    """
    生成左右分栏的中英对照HTML。
    注意：此HTML将上传至Gist，并通过 htmlpreview.github.io 渲染，
    所以必须是完整自包含的HTML（无外部依赖或只用CDN字体）。
    """
    logging.info(f"\n=== 生成第{index}条资讯HTML ===")
    title_en  = article.get("title",   {}).get("en", "No Title")
    title_zh  = article.get("title",   {}).get("zh", "未获取到中文标题")
    content_en = article.get("content", {}).get("en", "No Content")
    content_zh = article.get("content", {}).get("zh", "未获取到中文摘要")
    source    = article.get("source",    "Unknown Source")
    hot_score = article.get("hot_score", "N/A")
    link      = article.get("link",      "#")
    today     = get_today()

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI资讯日报 - {today} | 第{index}条</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","Helvetica Neue",Arial,sans-serif;
    background:#f0f2f5;color:#1a1a1a;line-height:1.8;min-height:100vh;
    display:flex;flex-direction:column;
  }}
  /* ── 顶部 header ── */
  .header{{
    background:linear-gradient(135deg,#0052cc 0%,#0066ff 100%);
    color:#fff;padding:24px 32px;
  }}
  .header-inner{{max-width:1100px;margin:0 auto;}}
  .header h1{{font-size:22px;font-weight:700;margin-bottom:6px;letter-spacing:0.02em;}}
  .header-meta{{font-size:13px;opacity:.85;display:flex;gap:16px;flex-wrap:wrap;}}
  .badge{{
    background:rgba(255,255,255,0.22);border-radius:20px;
    padding:2px 10px;font-size:12px;
  }}
  /* ── 分栏容器 ── */
  .main{{flex:1;max-width:1100px;width:100%;margin:24px auto;padding:0 16px 40px;}}
  .bilingual-wrapper{{
    display:grid;grid-template-columns:1fr 1fr;
    background:#fff;border-radius:12px;
    box-shadow:0 4px 24px rgba(0,0,0,0.10);
    overflow:hidden;
  }}
  /* ── 每一列 ── */
  .col{{padding:28px 30px;}}
  .col.en{{background:#f8f9fc;border-right:1px solid #e8ecf0;}}
  .col.zh{{background:#ffffff;}}
  .col-lang-tag{{
    display:inline-flex;align-items:center;gap:6px;
    font-size:11px;font-weight:600;letter-spacing:0.12em;text-transform:uppercase;
    color:#0052cc;background:#e8efff;border-radius:4px;
    padding:3px 10px;margin-bottom:16px;
  }}
  .col.zh .col-lang-tag{{color:#c0392b;background:#fdecea;}}
  .col-title{{
    font-size:17px;font-weight:700;line-height:1.55;
    color:#111;margin-bottom:16px;
  }}
  .col-content{{
    font-size:15px;line-height:1.9;color:#444;
  }}
  /* ── 底部 footer ── */
  .footer{{
    max-width:1100px;width:100%;margin:0 auto;padding:0 16px 32px;
    display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;
  }}
  .btn{{
    display:inline-block;padding:10px 22px;border-radius:8px;
    font-size:14px;font-weight:600;text-decoration:none;cursor:pointer;
    transition:all .15s ease;
  }}
  .btn-primary{{background:#0052cc;color:#fff;}}
  .btn-primary:hover{{background:#003d99;}}
  .btn-ghost{{background:#fff;color:#444;border:1px solid #d0d5dd;}}
  .btn-ghost:hover{{background:#f5f5f5;}}
  .footer-note{{font-size:12px;color:#999;}}
  /* ── 响应式：手机竖屏自动堆叠 ── */
  @media(max-width:680px){{
    .bilingual-wrapper{{grid-template-columns:1fr;}}
    .col.en{{border-right:none;border-bottom:1px solid #e8ecf0;}}
    .header{{padding:18px 16px;}}
    .col{{padding:20px 18px;}}
  }}
</style>
</head>
<body>
  <div class="header">
    <div class="header-inner">
      <h1>🤖 AI资讯日报 · 中英双语对照</h1>
      <div class="header-meta">
        <span class="badge">📅 {today}</span>
        <span class="badge">第 {index} 条</span>
        <span class="badge">📡 {source}</span>
        <span class="badge">🔥 热度 {hot_score}</span>
      </div>
    </div>
  </div>

  <div class="main">
    <div class="bilingual-wrapper">
      <!-- 左：英文 -->
      <div class="col en">
        <div class="col-lang-tag">📝 English Original</div>
        <div class="col-title">{title_en}</div>
        <div class="col-content">{content_en}</div>
      </div>
      <!-- 右：中文 -->
      <div class="col zh">
        <div class="col-lang-tag">📝 中文翻译</div>
        <div class="col-title">{title_zh}</div>
        <div class="col-content">{content_zh}</div>
      </div>
    </div>
  </div>

  <div class="footer">
    <div style="display:flex;gap:10px;flex-wrap:wrap;">
      <a class="btn btn-primary" href="{link}" target="_blank">🔗 查看英文原文</a>
      <a class="btn btn-ghost" onclick="window.history.back()">← 返回</a>
    </div>
    <span class="footer-note">来源：{source} · AI资讯日报自动推送</span>
  </div>
</body>
</html>"""
    return html


# ===================== 核心修复：Gist 上传 + 生成可渲染 URL =====================
@retry_wrapper
def upload_to_gist(html, index):
    """
    ✅ 核心修复：
    上传HTML至GitHub Gist后，不再直接使用 Raw URL（会显示源码），
    而是转换为 htmlpreview.github.io 前缀的渲染链接，点击后直接看到渲染页面。

    渲染URL格式：
      https://htmlpreview.github.io/?https://gist.githubusercontent.com/{user}/{gist_id}/raw/{filename}
    """
    if GIST_TOKEN and len(GIST_TOKEN) > 10:
        try:
            file_name = f"ai_news_{index}_{get_today()}.html"
            gist_payload = {
                "files": {file_name: {"content": html}},
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
                gist_id   = res["id"]
                username  = res["owner"]["login"]
                # ✅ 关键修复：Raw URL → htmlpreview 渲染 URL
                raw_url      = f"https://gist.githubusercontent.com/{username}/{gist_id}/raw/{file_name}"
                rendered_url = f"https://htmlpreview.github.io/?{raw_url}"
                logging.info(f"✅ Gist上传成功，渲染链接: {rendered_url}")
                return rendered_url
            else:
                logging.error(f"❌ Gist上传失败: {resp.status_code} - {resp.text[:150]}")
        except Exception as e:
            logging.error(f"❌ Gist上传异常: {e}")

    # 兜底：使用 codepen 风格的 paste 服务
    try:
        resp = requests.post(
            "https://api.paste.fo/",
            headers={"X-Auth-Token": "public"},
            json={"content": html, "title": f"AI_News_{index}_{get_today()}", "syntax": "html"},
            timeout=15
        )
        if resp.status_code == 200:
            paste_url = resp.json().get("url", "")
            if paste_url:
                logging.info(f"✅ 兜底托管成功: {paste_url}")
                return paste_url
    except Exception as e:
        logging.error(f"❌ 兜底托管失败: {e}")

    # 最终兜底：返回一个通用链接
    return "https://htmlpreview.github.io/"


# ===================== 多渠道抓取函数 =====================
def crawl_arxiv():
    """抓取arXiv AI论文"""
    try:
        feed = feedparser.parse("http://export.arxiv.org/rss/cs.AI")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        title   = baidu_translate(clean_text(entry.title))
        content = baidu_translate(fetch_article_content(entry.link))
        return [{"title": title, "content": content, "link": entry.link,
                 "source": "arXiv (AI学术论文)", "hot_score": round(random.uniform(87, 92), 1)}]
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
        title   = baidu_translate(clean_text(entry.title))
        content = baidu_translate(fetch_article_content(entry.link))
        return [{"title": title, "content": content, "link": entry.link,
                 "source": "OpenAI Blog", "hot_score": round(random.uniform(85, 90), 1)}]
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
        title   = baidu_translate(clean_text(entry.title))
        content = baidu_translate(fetch_article_content(entry.link))
        return [{"title": title, "content": content, "link": entry.link,
                 "source": "Google AI", "hot_score": round(random.uniform(84, 89), 1)}]
    except Exception as e:
        logging.error(f"❌ Google AI抓取失败: {e}")
        return []

def crawl_opentools_ai():
    """
    抓取 OpenTools AI — 工具资讯聚合平台
    RSS: https://opentools.ai/rss  (备用: /feed)
    """
    try:
        feed = feedparser.parse("https://opentools.ai/rss")
        if not feed.entries:
            feed = feedparser.parse("https://opentools.ai/feed")
        if not feed.entries:
            logging.warning("⚠️ OpenTools AI RSS 无条目")
            return []
        entry = feed.entries[0]
        title   = baidu_translate(clean_text(entry.title))
        summary = clean_text(getattr(entry, "summary", "Latest AI tools update"))
        content = baidu_translate(summary or fetch_article_content(entry.link))
        return [{"title": title, "content": content, "link": entry.link,
                 "source": "OpenTools AI", "hot_score": round(random.uniform(82, 87), 1)}]
    except Exception as e:
        logging.error(f"❌ OpenTools AI抓取失败: {e}")
        return []

def crawl_venturebeat():
    """
    抓取 VentureBeat AI 频道
    RSS: https://venturebeat.com/category/ai/feed/
    """
    try:
        feed = feedparser.parse("https://venturebeat.com/category/ai/feed/")
        if not feed.entries:
            # 备用路径
            feed = feedparser.parse("https://venturebeat.com/category/artificial-intelligence/feed/")
        if not feed.entries:
            logging.warning("⚠️ VentureBeat RSS 无条目")
            return []
        entry = feed.entries[0]
        title   = baidu_translate(clean_text(entry.title))
        # 优先用 RSS 中的 summary，减少一次 HTTP 抓取
        summary = clean_text(getattr(entry, "summary", ""))
        if len(summary) < 80:
            summary = fetch_article_content(entry.link)
        content = baidu_translate(summary)
        return [{"title": title, "content": content, "link": entry.link,
                 "source": "VentureBeat", "hot_score": round(random.uniform(83, 88), 1)}]
    except Exception as e:
        logging.error(f"❌ VentureBeat抓取失败: {e}")
        return []

def crawl_forbes():
    """
    抓取 Forbes AI 频道
    RSS: https://www.forbes.com/innovation/artificial-intelligence/feed/
    """
    try:
        # Forbes 提供多条 RSS，逐一尝试
        rss_urls = [
            "https://www.forbes.com/innovation/artificial-intelligence/feed/",
            "https://www.forbes.com/technology/artificial-intelligence/feed/",
            "https://www.forbes.com/sites/technology/feed/",
        ]
        feed = None
        for rss in rss_urls:
            feed = feedparser.parse(rss)
            if feed.entries:
                break
        if not feed or not feed.entries:
            logging.warning("⚠️ Forbes RSS 无条目")
            return []
        entry = feed.entries[0]
        title   = baidu_translate(clean_text(entry.title))
        summary = clean_text(getattr(entry, "summary", ""))
        if len(summary) < 80:
            summary = fetch_article_content(entry.link)
        content = baidu_translate(summary)
        return [{"title": title, "content": content, "link": entry.link,
                 "source": "Forbes", "hot_score": round(random.uniform(86, 91), 1)}]
    except Exception as e:
        logging.error(f"❌ Forbes抓取失败: {e}")
        return []

def crawl_hackernews():
    """抓取 HackerNews AI 相关热帖"""
    try:
        resp = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=GLOBAL_TIMEOUT)
        ids = resp.json()[:10]   # 扩大搜索范围以提高命中率
        for story_id in ids:
            item = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                timeout=GLOBAL_TIMEOUT
            ).json()
            if "title" in item and any(kw in item["title"] for kw in ("AI", "LLM", "GPT", "model", "machine learning")):
                title = baidu_translate(clean_text(item["title"]))
                link  = item.get("url", f"https://news.ycombinator.com/item?id={story_id}")
                text  = clean_text(item.get("text", "Latest AI technology trends"))
                content = baidu_translate(text if text else "Trending AI discussion on HackerNews")
                return [{"title": title, "content": content, "link": link,
                         "source": "HackerNews", "hot_score": round(random.uniform(81, 86), 1)}]
        return []
    except Exception as e:
        logging.error(f"❌ HackerNews抓取失败: {e}")
        return []

def crawl_techcrunch():
    """抓取 TechCrunch AI 频道"""
    try:
        feed = feedparser.parse("https://techcrunch.com/category/artificial-intelligence/feed/")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        title   = baidu_translate(clean_text(entry.title))
        summary = clean_text(getattr(entry, "summary", ""))
        if len(summary) < 80:
            summary = fetch_article_content(entry.link)
        content = baidu_translate(summary)
        return [{"title": title, "content": content, "link": entry.link,
                 "source": "TechCrunch", "hot_score": round(random.uniform(82, 87), 1)}]
    except Exception as e:
        logging.error(f"❌ TechCrunch抓取失败: {e}")
        return []


# ===================== 飞书推送函数 =====================
def send_to_feishu(articles):
    """推送至飞书群（卡片消息）"""
    if not FEISHU_WEBHOOK:
        logging.error("❌ 未配置飞书Webhook")
        return False

    card_elements = []
    for idx, article in enumerate(articles, 1):
        bilingual_html = generate_bilingual_html(article, idx)
        rendered_url   = upload_to_gist(bilingual_html, idx)   # ← 已是渲染链接

        card_elements.extend([
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"### {idx}. {article['title']['zh']}\n"
                        f"📈 热度: {article['hot_score']} | 来源: {article['source']}\n\n"
                        f"**英文标题**: {article['title']['en'][:80]}{'...' if len(article['title']['en'])>80 else ''}\n\n"
                        f"**中文摘要**: {article['content']['zh'][:120]}{'...' if len(article['content']['zh'])>120 else ''}"
                    )
                }
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📄 查看中英对照"},
                        "type": "primary",
                        "url": rendered_url          # ✅ 直接打开渲染后的双语页面
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔗 查看英文原文"},
                        "type": "default",
                        "url": article["link"]
                    }
                ]
            },
            {"tag": "hr"}
        ])

    # 移除最后多余的分割线
    if card_elements and card_elements[-1].get("tag") == "hr":
        card_elements.pop()

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🤖 AI资讯日报 | {get_today()}"},
            "template": "blue"
        },
        "elements": card_elements
    }

    payload = {"msg_type": "interactive", "card": card}
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=GLOBAL_TIMEOUT)
        result = resp.json()
        # 飞书成功响应：StatusCode==0 或 code==0
        if resp.status_code == 200 and (result.get("StatusCode") == 0 or result.get("code") == 0):
            logging.info("✅ 飞书推送成功")
            return True
        logging.error(f"❌ 飞书推送失败: {resp.text}")
        return False
    except Exception as e:
        logging.error(f"❌ 飞书推送异常: {e}")
        return False


# ===================== 主函数 =====================
def main():
    logging.info("🚀 开始执行AI资讯日报推送任务")

    # 执行所有渠道抓取（顺序 = 优先级）
    crawlers = [
        crawl_arxiv,
        crawl_openai,
        crawl_venturebeat,    # ✅ 新增
        crawl_forbes,         # ✅ 新增
        crawl_opentools_ai,   # ✅ 新增（已有，加强）
        crawl_google_ai,
        crawl_hackernews,
        crawl_techcrunch,
    ]

    all_articles = []
    for crawler in crawlers:
        try:
            results = crawler()
            if results:
                all_articles.extend(results)
                logging.info(f"✅ [{crawler.__name__}] 获取 {len(results)} 条")
        except Exception as e:
            logging.error(f"❌ [{crawler.__name__}] 抓取出错: {e}")

    # 过滤无效条目
    valid_articles = [a for a in all_articles if a and a.get("title")]

    if not valid_articles:
        logging.warning("⚠️ 未抓取到有效资讯，使用默认占位内容")
        valid_articles = [{
            "title":   {"en": "No AI news today", "zh": "今日暂无AI资讯"},
            "content": {"en": "No AI news available today.", "zh": "今日暂无AI资讯可推送，请明天再来查看。"},
            "link":    "https://ai.google/",
            "source":  "AI Trends",
            "hot_score": 0.0
        }]

    # 最多推送5条，按热度降序排列
    valid_articles = sorted(valid_articles, key=lambda x: float(x.get("hot_score", 0)), reverse=True)[:5]
    logging.info(f"📋 共推送 {len(valid_articles)} 条资讯")

    send_to_feishu(valid_articles)
    logging.info("🏁 任务执行完成")


if __name__ == "__main__":
    main()
