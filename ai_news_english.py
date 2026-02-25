#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI资讯日报推送脚本 v4 - 彻底修复版
==============================================================
修复清单：
  Fix-1  baidu_translate 返回 None 时防护 → safe_translate()
  Fix-2  百度翻译 2000字限制 → 超长文本自动分段翻译
  Fix-3  fetch_article_content 精准段落提取，过滤广告/导航噪声
  Fix-4  get_rich_content 增加 HTML 清洗 + 最终非空校验
  Fix-5  generate_bilingual_html 字段空值全面兜底

新增：
  + 定时运行说明（北京时间 09:30，GitHub Actions cron）
  + 消息来源全面优化，聚焦 AI 技术/应用/投融资
  + 新增 The Information AI / MIT Tech Review / AI News
"""

import requests
import os
import datetime
import time
import random
import hashlib
import re
import logging
import urllib3
import feedparser
from bs4 import BeautifulSoup

# ===================== 基础配置 =====================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

FEISHU_WEBHOOK  = os.getenv("FEISHU_WEBHOOK")
BAIDU_APP_ID    = os.getenv("BAIDU_APP_ID")
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY")
GIST_TOKEN      = os.getenv("AI_NEWS_GIST_TOKEN", "")

GLOBAL_TIMEOUT  = 20
MAX_RETRIES     = 3
RANDOM_DELAY    = (0.8, 1.5)
TRANSLATE_MAX   = 1800   # 百度翻译单次最大字符数（官方上限2000，留余量）
CONTENT_MIN_LEN = 80     # 内容低于此长度则继续尝试下一级

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

# ===================== 工具函数 =====================
def get_today():
    return datetime.date.today().strftime("%Y-%m-%d")

def clean_text(text):
    """清理文本：去除多余空白、控制长度"""
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', str(text)).strip()
    return text[:TRANSLATE_MAX] if len(text) > TRANSLATE_MAX else text

def strip_html(raw_html):
    """将 HTML 字符串转为纯文本"""
    if not raw_html:
        return ""
    return clean_text(BeautifulSoup(str(raw_html), "html.parser").get_text())

def retry(func):
    """重试装饰器，失败返回 None"""
    def wrapper(*args, **kwargs):
        for i in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logging.warning(f"[{func.__name__}] 第{i+1}次失败: {str(e)[:60]}")
                time.sleep(random.uniform(*RANDOM_DELAY))
        logging.error(f"[{func.__name__}] 全部重试失败")
        return None
    return wrapper


# ===================== Fix-1 + Fix-2：翻译函数 =====================
def _call_baidu_api(text):
    """
    单次调用百度翻译 API，返回中文字符串或 None。
    text 长度调用方保证 <= TRANSLATE_MAX。
    """
    url  = "https://fanyi-api.baidu.com/api/trans/vip/translate"
    salt = str(random.randint(32768, 65536))
    sign = hashlib.md5((BAIDU_APP_ID + text + salt + BAIDU_SECRET_KEY).encode()).hexdigest()
    params = {"q": text, "from": "en", "to": "zh",
              "appid": BAIDU_APP_ID, "salt": salt, "sign": sign}
    resp = requests.get(url, params=params, timeout=GLOBAL_TIMEOUT, verify=False)
    res  = resp.json()
    if "trans_result" in res and res["trans_result"]:
        return res["trans_result"][0]["dst"]
    logging.error(f"百度翻译异常响应: {res}")
    return None


def translate_long_text(text):
    """
    Fix-2：超长文本按句子分段翻译（不超过 TRANSLATE_MAX），结果拼接返回。
    """
    if not text or not text.strip():
        return ""

    # 按句号/问号/感叹号分句，尽量保持语义完整
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks, cur = [], ""
    for sent in sentences:
        if len(cur) + len(sent) + 1 <= TRANSLATE_MAX:
            cur = (cur + " " + sent).strip()
        else:
            if cur:
                chunks.append(cur)
            # 单句超长则强制截断
            cur = sent[:TRANSLATE_MAX]
    if cur:
        chunks.append(cur)

    zh_parts = []
    for chunk in chunks:
        zh = _call_baidu_api(chunk)
        if zh:
            zh_parts.append(zh)
        else:
            zh_parts.append(chunk)   # 翻译失败保留原文段
        time.sleep(random.uniform(0.3, 0.6))   # 避免 API 频率限制

    return "".join(zh_parts)


def safe_translate(text):
    """
    Fix-1 + Fix-2：安全翻译函数，始终返回 {"en": ..., "zh": ...}，绝不返回 None。
    - 未配置 API → 返回原文作为 zh（保留英文可读）
    - API 调用失败 → 返回原文作为 zh
    - 超长文本 → 分段翻译后拼接
    """
    en_text = clean_text(text) if text else ""

    if not en_text or len(en_text) < 3:
        return {"en": en_text, "zh": en_text or "暂无内容"}

    # 未配置翻译 API
    if not (BAIDU_APP_ID and BAIDU_SECRET_KEY):
        logging.warning("⚠️ 未配置百度翻译API，中文栏显示英文原文")
        return {"en": en_text, "zh": en_text}

    try:
        zh_text = translate_long_text(en_text)
        if zh_text and zh_text.strip():
            logging.info(f"✅ 翻译完成: {en_text[:25]}... → {zh_text[:25]}...")
            return {"en": en_text, "zh": zh_text}
        else:
            logging.warning("⚠️ 翻译结果为空，使用原文")
            return {"en": en_text, "zh": en_text}
    except Exception as e:
        logging.error(f"❌ 翻译异常: {e}")
        return {"en": en_text, "zh": en_text}


# ===================== Fix-3：精准正文抓取 =====================
@retry
def fetch_article_content(url):
    """
    Fix-3：按站点使用精准 CSS 选择器，失败返回空字符串（不返回占位符）。
    通用兜底：取正文段落（过滤 < 40字的噪声段）。
    """
    try:
        resp = requests.get(
            url, headers=HEADERS, timeout=GLOBAL_TIMEOUT,
            verify=False, allow_redirects=True
        )
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")

        # 移除干扰元素（广告、导航、侧边栏、脚注）
        for tag in soup.find_all(["script", "style", "nav", "header",
                                   "footer", "aside", "figure", "figcaption",
                                   "noscript", "iframe"]):
            tag.decompose()
        for tag in soup.find_all(class_=re.compile(
            r"(ad|ads|advert|sponsor|promo|related|recommend|sidebar|"
            r"newsletter|subscribe|comment|social|share|cookie|banner)",
            re.I
        )):
            tag.decompose()

        # 按站点精准选择正文容器
        content_el = None
        if "arxiv.org" in url:
            content_el = soup.find("blockquote", class_="abstract mathjax")
        elif "openai.com" in url:
            content_el = (soup.find("div", class_=re.compile(r"post.?content", re.I))
                          or soup.find("main"))
        elif "venturebeat.com" in url:
            content_el = (soup.find("div", class_=re.compile(r"article.?content|entry.?content", re.I))
                          or soup.find("article"))
        elif "forbes.com" in url:
            content_el = (soup.find("div", class_=re.compile(r"article.?body|body.?text", re.I))
                          or soup.find("article"))
        elif "opentools.ai" in url:
            content_el = (soup.find("div", class_=re.compile(r"post.?content|entry.?content", re.I))
                          or soup.find("article"))
        elif "techcrunch.com" in url:
            # TechCrunch: 取 <article> 内的 <p> 段落，跳过图片说明等
            article = soup.find("article")
            if article:
                paras = [p.get_text(" ", strip=True) for p in article.find_all("p")
                         if len(p.get_text(strip=True)) > 40]
                return clean_text(" ".join(paras[:8]))   # 取前8段
        elif "technologyreview.com" in url:
            content_el = (soup.find("div", class_=re.compile(r"article.?body|content.?body", re.I))
                          or soup.find("article"))
        elif "news.ycombinator.com" in url:
            content_el = soup.find("div", class_="storytext")
        elif "reuters.com" in url or "bloomberg.com" in url:
            content_el = soup.find("div", attrs={"data-testid": re.compile(r"body|article", re.I)})

        # 有精准容器 → 取段落
        if content_el:
            paras = [p.get_text(" ", strip=True) for p in content_el.find_all("p")
                     if len(p.get_text(strip=True)) > 30]
            text  = " ".join(paras[:8]) if paras else content_el.get_text(" ", strip=True)
            return clean_text(text)

        # 通用兜底：全文搜索 <p>，过滤短段
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")
                 if len(p.get_text(strip=True)) > 40][:6]
        return clean_text(" ".join(paras))

    except Exception as e:
        logging.error(f"❌ 抓取正文失败 [{url[:50]}]: {e}")
        return ""


# ===================== Fix-4：多级内容获取 =====================
def get_rich_content(entry, url):
    """
    Fix-4：多级兜底，确保翻译输入有实质内容。
    级别：RSS full content → RSS summary（HTML剥离）→ 抓取正文 → 标题兜底
    """
    # 1️⃣ RSS content:encoded（部分站点提供全文）
    if hasattr(entry, "content") and entry.content:
        raw = entry.content[0].get("value", "")
        text = strip_html(raw)
        if len(text) >= CONTENT_MIN_LEN:
            logging.info(f"  [内容] RSS full content ({len(text)}字)")
            return text

    # 2️⃣ RSS summary / description（HTML剥离）
    raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
    summary = strip_html(raw_summary)
    if len(summary) >= CONTENT_MIN_LEN:
        logging.info(f"  [内容] RSS summary ({len(summary)}字)")
        return summary

    # 3️⃣ 抓取原文正文
    logging.info(f"  [内容] RSS不足({len(summary)}字)，抓取原文...")
    fetched = fetch_article_content(url) or ""
    if len(fetched) >= CONTENT_MIN_LEN:
        logging.info(f"  [内容] 抓取正文 ({len(fetched)}字)")
        return fetched

    # 4️⃣ 拼接已有内容
    combined = (summary or fetched).strip()
    if combined:
        logging.warning(f"  [内容] 拼接兜底 ({len(combined)}字)")
        return combined

    # 5️⃣ 标题扩展（绝对兜底，保证不翻译空字符串）
    title = clean_text(getattr(entry, "title", ""))
    fallback = f"{title}. For more details, please visit the original article." if title else "AI industry latest update."
    logging.warning(f"  [内容] 标题兜底")
    return fallback


# ===================== HTML 生成 =====================
def generate_bilingual_html(article, index):
    """
    Fix-5：所有字段增加空值兜底，确保任何情况下页面都能正常渲染。
    """
    # 安全取值（防止 content 为 None 导致 .get 崩溃）
    def safe_get(obj, *keys, default=""):
        val = obj
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
            if val is None:
                return default
        return str(val) if val else default

    title_en   = safe_get(article, "title",   "en", default="No Title")
    title_zh   = safe_get(article, "title",   "zh", default=title_en)
    content_en = safe_get(article, "content", "en", default="No content available.")
    content_zh = safe_get(article, "content", "zh", default=content_en)
    source     = article.get("source",    "Unknown")
    hot_score  = article.get("hot_score", "N/A")
    link       = article.get("link",      "#")
    today      = get_today()

    # 异常兜底：zh 仍为空时用 en
    if not content_zh.strip() or content_zh in ("无内容", "暂无内容", "翻译失败，显示原文", "翻译异常，显示原文"):
        content_zh = content_en
    if not title_zh.strip():
        title_zh = title_en

    logging.info(f"[HTML] #{index} 标题EN={title_en[:30]} 标题ZH={title_zh[:30]}")
    logging.info(f"[HTML] #{index} 内容EN={len(content_en)}字 内容ZH={len(content_zh)}字")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI资讯日报 {today} · 第{index}条</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",
              "Helvetica Neue",Arial,sans-serif;
  background:#f0f2f5;color:#1a1a1a;line-height:1.8;min-height:100vh;
  display:flex;flex-direction:column;
}}
.header{{
  background:linear-gradient(135deg,#0052cc 0%,#1a75ff 100%);
  color:#fff;padding:22px 32px;flex-shrink:0;
}}
.header-inner{{max-width:1100px;margin:0 auto;}}
.header h1{{font-size:20px;font-weight:700;margin-bottom:8px;letter-spacing:.02em;}}
.badges{{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px;}}
.badge{{
  background:rgba(255,255,255,.20);border-radius:20px;
  padding:3px 12px;font-size:12px;white-space:nowrap;
}}
.main{{
  flex:1;max-width:1100px;width:100%;
  margin:24px auto;padding:0 16px 16px;
}}
.bilingual-wrapper{{
  display:grid;grid-template-columns:1fr 1fr;
  background:#fff;border-radius:12px;
  box-shadow:0 2px 20px rgba(0,0,0,.10);
  overflow:hidden;min-height:260px;
}}
.col{{padding:28px 28px;}}
.col.en{{background:#f7f9fc;border-right:1px solid #e5eaf0;}}
.col.zh{{background:#fff;}}
.lang-tag{{
  display:inline-flex;align-items:center;gap:5px;
  font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
  color:#0052cc;background:#e6eeff;border-radius:4px;
  padding:3px 10px;margin-bottom:14px;
}}
.col.zh .lang-tag{{color:#c0392b;background:#fdecea;}}
.col-title{{
  font-size:17px;font-weight:700;line-height:1.5;
  color:#111;margin-bottom:14px;
}}
.col-content{{font-size:14px;line-height:1.95;color:#444;}}
.footer{{
  max-width:1100px;width:100%;margin:0 auto;
  padding:14px 16px 32px;
  display:flex;align-items:center;justify-content:space-between;
  flex-wrap:wrap;gap:10px;
}}
.btn{{
  display:inline-block;padding:9px 20px;border-radius:8px;
  font-size:13px;font-weight:600;text-decoration:none;
  cursor:pointer;transition:all .15s ease;border:none;
}}
.btn-primary{{background:#0052cc;color:#fff;}}
.btn-primary:hover{{background:#003d99;}}
.btn-ghost{{
  background:#fff;color:#333;
  border:1px solid #d0d5dd;cursor:pointer;
}}
.btn-ghost:hover{{background:#f4f5f7;}}
.footer-note{{font-size:12px;color:#aaa;}}
@media(max-width:640px){{
  .bilingual-wrapper{{grid-template-columns:1fr;}}
  .col.en{{border-right:none;border-bottom:1px solid #e5eaf0;}}
  .header{{padding:16px;}}
  .col{{padding:18px 16px;}}
  .header h1{{font-size:17px;}}
}}
</style>
</head>
<body>
<div class="header">
  <div class="header-inner">
    <h1>🤖 AI资讯日报 · 中英双语对照</h1>
    <div class="badges">
      <span class="badge">📅 {today}</span>
      <span class="badge">第 {index} 条</span>
      <span class="badge">📡 {source}</span>
      <span class="badge">🔥 热度 {hot_score}</span>
    </div>
  </div>
</div>

<div class="main">
  <div class="bilingual-wrapper">
    <div class="col en">
      <div class="lang-tag">📝 English Original</div>
      <div class="col-title">{title_en}</div>
      <div class="col-content">{content_en}</div>
    </div>
    <div class="col zh">
      <div class="lang-tag">📝 中文翻译</div>
      <div class="col-title">{title_zh}</div>
      <div class="col-content">{content_zh}</div>
    </div>
  </div>
</div>

<div class="footer">
  <div style="display:flex;gap:10px;flex-wrap:wrap;">
    <a class="btn btn-primary" href="{link}" target="_blank">🔗 查看英文原文</a>
    <button class="btn btn-ghost"
      onclick="try{{if(window.history.length>1){{window.history.back();}}else{{window.close();}}}}catch(e){{window.close();}}">
      ← 关闭
    </button>
  </div>
  <span class="footer-note">来源：{source} · AI资讯日报自动推送</span>
</div>
</body>
</html>"""
    return html


# ===================== Gist 上传 =====================
@retry
def upload_to_gist(html, index):
    """上传 HTML 到 Gist 并返回 htmlpreview 渲染链接"""
    if not (GIST_TOKEN and len(GIST_TOKEN) > 10):
        logging.error("❌ GIST_TOKEN 未配置或过短")
        return "#"

    file_name = f"ai_news_{index}_{get_today()}.html"
    resp = requests.post(
        "https://api.github.com/gists",
        headers={
            "Authorization": f"token {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "AI-News-Daily/4.0"
        },
        json={
            "files": {file_name: {"content": html}},
            "public": True,
            "description": f"AI资讯日报第{index}条 - {get_today()}"
        },
        timeout=25
    )
    if resp.status_code == 201:
        res      = resp.json()
        gist_id  = res["id"]
        username = res["owner"]["login"]
        raw_url  = f"https://gist.githubusercontent.com/{username}/{gist_id}/raw/{file_name}"
        rendered = f"https://htmlpreview.github.io/?{raw_url}"
        logging.info(f"✅ Gist上传成功: {rendered}")
        return rendered
    logging.error(f"❌ Gist上传失败 {resp.status_code}: {resp.text[:120]}")
    return "#"


# ===================== 爬虫：聚焦 AI 技术/应用/投融资 =====================
#
# 来源选择原则：
#   ① 技术前沿：arXiv cs.AI / cs.LG / cs.CL（模型、算法）
#   ② 产品动态：OpenAI Blog、Anthropic News、Google DeepMind Blog
#   ③ 行业资讯：TechCrunch AI、VentureBeat AI、MIT Tech Review AI
#   ④ 投融资：The Information AI（需订阅可换）、Forbes AI
#   ⑤ 工具聚合：OpenTools AI、AI News（ainews.io）
#   ⑥ 社区热点：HackerNews（AI/LLM相关）
#
def _make_article(entry, source, hot_range):
    """通用文章构建：title翻译 + 正文获取翻译"""
    title   = safe_translate(clean_text(entry.title))
    raw_content = get_rich_content(entry, entry.link)
    content = safe_translate(raw_content)
    return {
        "title":     title,
        "content":   content,
        "link":      entry.link,
        "source":    source,
        "hot_score": round(random.uniform(*hot_range), 1)
    }


def crawl_arxiv():
    """arXiv AI/ML 论文 — 技术前沿"""
    try:
        # cs.AI + cs.LG（机器学习）+ cs.CL（自然语言处理）
        for category in ["cs.AI", "cs.LG", "cs.CL"]:
            feed = feedparser.parse(f"http://export.arxiv.org/rss/{category}")
            if feed.entries:
                entry = feed.entries[0]
                logging.info(f"arXiv [{category}]: {entry.title[:50]}")
                return [_make_article(entry, "arXiv 学术论文", (88, 93))]
        return []
    except Exception as e:
        logging.error(f"❌ arXiv: {e}")
        return []


def crawl_openai():
    """OpenAI 官方博客 — 产品/模型动态"""
    try:
        feed = feedparser.parse("https://openai.com/blog/rss/")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        logging.info(f"OpenAI: {entry.title[:50]}")
        return [_make_article(entry, "OpenAI 官方博客", (86, 92))]
    except Exception as e:
        logging.error(f"❌ OpenAI: {e}")
        return []


def crawl_anthropic():
    """Anthropic 官方新闻 — Claude/安全研究"""
    try:
        # Anthropic 暂无标准 RSS，使用其 news 页面的 Atom
        feed = feedparser.parse("https://www.anthropic.com/news/rss")
        if not feed.entries:
            feed = feedparser.parse("https://www.anthropic.com/feed")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        logging.info(f"Anthropic: {entry.title[:50]}")
        return [_make_article(entry, "Anthropic 官方", (85, 91))]
    except Exception as e:
        logging.error(f"❌ Anthropic: {e}")
        return []


def crawl_google_deepmind():
    """Google DeepMind Blog — 前沿模型/研究"""
    try:
        for rss in [
            "https://deepmind.google/blog/rss.xml",
            "https://blog.google/technology/ai/rss/",
            "https://developers.googleblog.com/feeds/posts/default?alt=rss",
        ]:
            feed = feedparser.parse(rss)
            if feed.entries:
                entry = feed.entries[0]
                logging.info(f"Google/DeepMind: {entry.title[:50]}")
                return [_make_article(entry, "Google DeepMind", (84, 90))]
        return []
    except Exception as e:
        logging.error(f"❌ Google DeepMind: {e}")
        return []


def crawl_mit_tech_review():
    """MIT Technology Review AI — 深度技术分析"""
    try:
        feed = feedparser.parse("https://www.technologyreview.com/feed/")
        # 过滤 AI 相关文章
        ai_entries = [e for e in feed.entries
                      if any(kw in (e.title + getattr(e, "summary", "")).lower()
                             for kw in ["ai", "artificial intelligence", "machine learning",
                                        "llm", "model", "neural", "robot", "generative"])]
        if not ai_entries:
            ai_entries = feed.entries[:1]
        if not ai_entries:
            return []
        entry = ai_entries[0]
        logging.info(f"MIT Tech Review: {entry.title[:50]}")
        return [_make_article(entry, "MIT Technology Review", (85, 90))]
    except Exception as e:
        logging.error(f"❌ MIT Tech Review: {e}")
        return []


def crawl_venturebeat():
    """VentureBeat AI — 产品发布/行业动态"""
    try:
        for rss in [
            "https://venturebeat.com/category/ai/feed/",
            "https://venturebeat.com/category/artificial-intelligence/feed/",
        ]:
            feed = feedparser.parse(rss)
            if feed.entries:
                entry = feed.entries[0]
                logging.info(f"VentureBeat: {entry.title[:50]}")
                return [_make_article(entry, "VentureBeat", (83, 89))]
        return []
    except Exception as e:
        logging.error(f"❌ VentureBeat: {e}")
        return []


def crawl_techcrunch():
    """TechCrunch AI — 投融资/创业/产品"""
    try:
        feed = feedparser.parse("https://techcrunch.com/category/artificial-intelligence/feed/")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        logging.info(f"TechCrunch: {entry.title[:50]}")
        return [_make_article(entry, "TechCrunch", (82, 88))]
    except Exception as e:
        logging.error(f"❌ TechCrunch: {e}")
        return []


def crawl_forbes():
    """Forbes AI — 投融资/商业应用"""
    try:
        for rss in [
            "https://www.forbes.com/innovation/artificial-intelligence/feed/",
            "https://www.forbes.com/technology/artificial-intelligence/feed/",
        ]:
            feed = feedparser.parse(rss)
            if feed.entries:
                entry = feed.entries[0]
                logging.info(f"Forbes: {entry.title[:50]}")
                return [_make_article(entry, "Forbes", (83, 89))]
        return []
    except Exception as e:
        logging.error(f"❌ Forbes: {e}")
        return []


def crawl_opentools_ai():
    """OpenTools AI — 新工具发布/应用动态"""
    try:
        for rss in ["https://opentools.ai/rss", "https://opentools.ai/feed"]:
            feed = feedparser.parse(rss)
            if feed.entries:
                entry = feed.entries[0]
                logging.info(f"OpenTools AI: {entry.title[:50]}")
                return [_make_article(entry, "OpenTools AI", (81, 87))]
        return []
    except Exception as e:
        logging.error(f"❌ OpenTools AI: {e}")
        return []


def crawl_hackernews():
    """HackerNews — 社区热点（AI/LLM/模型相关）"""
    AI_KEYWORDS = {
        "ai", "llm", "gpt", "claude", "gemini", "mistral", "llama",
        "machine learning", "neural", "transformer", "model", "openai",
        "anthropic", "deepmind", "diffusion", "generative", "rag",
        "inference", "fine.tun", "embedding", "agent", "multimodal"
    }
    try:
        resp = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            timeout=GLOBAL_TIMEOUT
        )
        ids = resp.json()[:20]  # 搜索前20条确保命中
        for story_id in ids:
            item = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                timeout=GLOBAL_TIMEOUT
            ).json()
            title = item.get("title", "")
            if any(kw in title.lower() for kw in AI_KEYWORDS):
                link    = item.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
                body    = strip_html(item.get("text", "")) or title
                content = safe_translate(body)
                logging.info(f"HackerNews: {title[:50]}")
                return [{
                    "title":     safe_translate(title),
                    "content":   content,
                    "link":      link,
                    "source":    "HackerNews",
                    "hot_score": round(random.uniform(80, 86), 1)
                }]
        return []
    except Exception as e:
        logging.error(f"❌ HackerNews: {e}")
        return []


# ===================== 飞书推送 =====================
def send_to_feishu(articles):
    if not FEISHU_WEBHOOK:
        logging.error("❌ 未配置 FEISHU_WEBHOOK")
        return False

    elements = []
    for idx, article in enumerate(articles, 1):
        rendered_url = upload_to_gist(generate_bilingual_html(article, idx), idx)

        # 安全取值
        title_zh   = (article.get("title")   or {}).get("zh") or (article.get("title")   or {}).get("en") or "无标题"
        title_en   = (article.get("title")   or {}).get("en") or ""
        content_zh = (article.get("content") or {}).get("zh") or (article.get("content") or {}).get("en") or "暂无摘要"
        source     = article.get("source",    "未知来源")
        hot_score  = article.get("hot_score", "N/A")
        orig_link  = article.get("link", "#")

        elements.extend([
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"### {idx}. {title_zh}\n"
                        f"🔥 热度: {hot_score} | 📡 来源: {source}\n\n"
                        f"**英文标题**：{title_en[:90]}{'...' if len(title_en) > 90 else ''}\n\n"
                        f"**中文摘要**：{content_zh[:150]}{'...' if len(content_zh) > 150 else ''}"
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
                        "url": rendered_url
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔗 查看英文原文"},
                        "type": "default",
                        "url": orig_link
                    }
                ]
            },
            {"tag": "hr"}
        ])

    # 移除末尾多余分割线
    while elements and elements[-1].get("tag") == "hr":
        elements.pop()

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title":    {"tag": "plain_text", "content": f"🤖 全球AI资讯日报 | {get_today()}"},
            "template": "blue"
        },
        "elements": elements
    }

    try:
        resp   = requests.post(FEISHU_WEBHOOK, json={"msg_type": "interactive", "card": card},
                               timeout=GLOBAL_TIMEOUT)
        result = resp.json()
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
    """
    ============================================================
    问题2 解答：北京时间 09:30 自动触发
    ------------------------------------------------------------
    在 GitHub Actions 的 workflow YAML 中设置如下 cron：

      on:
        schedule:
          - cron: '30 1 * * *'   # UTC 01:30 = 北京时间 09:30

    注意：GitHub Actions 使用 UTC 时间，北京时间（CST）= UTC + 8
    因此 北京 09:30 → UTC 01:30
    ============================================================
    """
    logging.info("🚀 AI资讯日报 v4 启动")
    logging.info(f"📅 今日日期：{get_today()}")

    # 爬虫列表（优先级顺序）
    crawlers = [
        crawl_arxiv,           # 学术前沿
        crawl_openai,          # OpenAI 动态
        crawl_anthropic,       # Anthropic / Claude
        crawl_google_deepmind, # Google AI / DeepMind
        crawl_mit_tech_review, # MIT 深度分析
        crawl_venturebeat,     # 行业资讯
        crawl_techcrunch,      # 投融资/产品
        crawl_forbes,          # 商业/投融资
        crawl_opentools_ai,    # 工具聚合
        crawl_hackernews,      # 社区热点
    ]

    all_articles = []
    for crawler in crawlers:
        try:
            results = crawler() or []
            if results:
                all_articles.extend(results)
                logging.info(f"✅ {crawler.__name__} → {len(results)} 条")
            else:
                logging.warning(f"⚠️ {crawler.__name__} → 0 条")
        except Exception as e:
            logging.error(f"❌ {crawler.__name__} 崩溃: {e}")

    # 过滤：必须有标题
    valid = [a for a in all_articles
             if a and isinstance(a.get("title"), dict) and a["title"].get("en")]

    if not valid:
        logging.warning("⚠️ 未获取到任何有效资讯，使用兜底占位")
        valid = [{
            "title":   {"en": "No AI news today", "zh": "今日暂无AI资讯"},
            "content": {"en": "No AI news available today.", "zh": "今日暂无AI资讯可推送，请明日再查看。"},
            "link":    "https://news.google.com/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGRqTVhZU0FtVnVHZ0pWVXlnQVAB",
            "source":  "占位",
            "hot_score": 0.0
        }]

    # 按热度降序，取前5条
    valid = sorted(valid, key=lambda x: float(x.get("hot_score", 0) or 0), reverse=True)[:5]
    logging.info(f"📋 最终推送 {len(valid)} 条资讯")

    send_to_feishu(valid)
    logging.info("🏁 任务完成")


if __name__ == "__main__":
    main()
