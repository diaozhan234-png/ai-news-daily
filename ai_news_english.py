#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI资讯日报推送脚本 v7
==============================================================
核心改进：
  - Google News URL Base64解码，直接获取真实文章链接
  - 抓取真实文章全文，中英对照显示完整内容
  - 来源分层：前3条来自高质量固定渠道，后2条来自重点公司
  - 跨天去重关闭，每天独立运行
==============================================================
"""

import requests
import os
import base64
import struct
import datetime
import time
import random
import hashlib
import re
import json
import logging
import urllib3
import feedparser
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===================== 配置 =====================
FEISHU_WEBHOOK   = os.getenv("FEISHU_WEBHOOK")
BAIDU_APP_ID     = os.getenv("BAIDU_APP_ID")
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY")
GIST_TOKEN       = os.getenv("AI_NEWS_GIST_TOKEN", "")

GLOBAL_TIMEOUT  = 20
MAX_RETRIES     = 3
TRANSLATE_MAX   = 1800
CONTENT_MAX     = 6000
CONTENT_MIN_LEN = 80

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

def clean_text(text, max_len=None):
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', str(text)).strip()
    if max_len and len(text) > max_len:
        truncated = text[:max_len]
        last_period = max(truncated.rfind('. '), truncated.rfind('。'))
        return truncated[:last_period + 1] if last_period > max_len * 0.7 else truncated
    return text

def clean_content(text):
    return clean_text(text, max_len=CONTENT_MAX)

def clean_title(text):
    return clean_text(text, max_len=300)

def strip_html(raw_html):
    if not raw_html:
        return ""
    return clean_text(BeautifulSoup(str(raw_html), "html.parser").get_text())

def retry(func):
    def wrapper(*args, **kwargs):
        for i in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logging.warning(f"[{func.__name__}] 第{i+1}次失败: {str(e)[:60]}")
                time.sleep(random.uniform(0.8, 1.5))
        return None
    return wrapper


# ===================== Google News URL 解码 =====================
def decode_google_news_url(google_url):
    """
    方案A：Base64解码 Google News RSS 链接，直接获取真实文章 URL。
    Google News 将真实 URL 编码在 articles/ 后的 Base64 字符串中。
    解码格式参考开源项目 nytud/gazeteer 及社区逆向工程结果。
    """
    if "news.google.com" not in google_url:
        return google_url
    try:
        # 提取编码部分
        match = re.search(r'articles/([^?&#/]+)', google_url)
        if not match:
            return None
        encoded = match.group(1)

        # URL-safe Base64 → 标准 Base64
        encoded = encoded.replace('-', '+').replace('_', '/')
        # 补齐 padding
        padding = (4 - len(encoded) % 4) % 4
        encoded += '=' * padding

        decoded = base64.b64decode(encoded)

        # 真实 URL 藏在解码字节流中，以 https:// 或 http:// 开头
        # Google News 编码格式：前几字节为 protobuf header，URL 从某偏移开始
        for prefix in [b'https://', b'http://']:
            idx = decoded.find(prefix)
            if idx != -1:
                url_bytes = decoded[idx:]
                url = url_bytes.decode('utf-8', errors='ignore')
                # 截断到第一个控制字符或非 printable 字符
                url = re.split(r'[\x00-\x1f\x7f]', url)[0]
                url = url.strip()
                if len(url) > 20 and '.' in url:
                    logging.info(f"  [URL解码] Base64解码成功: {url[:80]}")
                    return url

        logging.warning(f"  [URL解码] 未找到 URL: {google_url[:60]}")
        return None

    except Exception as e:
        logging.warning(f"  [URL解码] 解码失败: {e}")
        return None


def resolve_google_news_url(url):
    """
    解析 Google News 链接为真实文章 URL。
    优先用 Base64 解码（方案A，0网络请求），失败则 HTTP 跟随重定向。
    中文落地页返回 None 表示跳过。
    """
    if "news.google.com" not in url:
        return url

    # 方案A：Base64解码
    decoded = decode_google_news_url(url)
    if decoded and "google.com" not in decoded:
        if is_chinese_url(decoded):
            logging.warning(f"  [URL过滤] 中文落地页跳过: {decoded[:60]}")
            return None
        return decoded

    # 方案B：HTTP 跟随重定向（兜底）
    try:
        resp = requests.get(
            url, headers=HEADERS, timeout=GLOBAL_TIMEOUT,
            allow_redirects=True, verify=False
        )
        final_url = resp.url
        if "google.com" not in final_url:
            if is_chinese_url(final_url):
                logging.warning(f"  [URL过滤] 中文落地页跳过: {final_url[:60]}")
                return None
            logging.info(f"  [URL解析] HTTP重定向: {final_url[:80]}")
            return final_url
    except Exception as e:
        logging.warning(f"  [URL解析] HTTP重定向失败: {e}")

    return None  # 无法解析，跳过此文章


# ===================== 翻译 =====================
def _call_baidu_api(text):
    url  = "https://fanyi-api.baidu.com/api/trans/vip/translate"
    salt = str(random.randint(32768, 65536))
    sign = hashlib.md5((BAIDU_APP_ID + text + salt + BAIDU_SECRET_KEY).encode()).hexdigest()
    params = {"q": text, "from": "en", "to": "zh",
              "appid": BAIDU_APP_ID, "salt": salt, "sign": sign}
    resp = requests.get(url, params=params, timeout=GLOBAL_TIMEOUT, verify=False)
    res  = resp.json()
    if "trans_result" in res and res["trans_result"]:
        translated = res["trans_result"][0]["dst"]
        ERROR_PATTERNS = ["服务错误", "服务目前不可用", "那是个错误", "错误-", "error_code"]
        if any(p in translated for p in ERROR_PATTERNS):
            return None
        return translated
    return None


def translate_long_text(text):
    if not text or not text.strip():
        return ""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks, cur = [], ""
    for sent in sentences:
        if len(cur) + len(sent) + 1 <= TRANSLATE_MAX:
            cur = (cur + " " + sent).strip()
        else:
            if cur:
                chunks.append(cur)
            cur = sent[:TRANSLATE_MAX]
    if cur:
        chunks.append(cur)
    zh_parts = []
    for chunk in chunks:
        zh = _call_baidu_api(chunk)
        zh_parts.append(zh if zh else chunk)
        time.sleep(random.uniform(0.3, 0.6))
    return "".join(zh_parts)


def safe_translate(text):
    en_text = clean_text(text) if text else ""
    if not en_text or len(en_text) < 3:
        return {"en": en_text, "zh": en_text or "暂无内容"}
    if not (BAIDU_APP_ID and BAIDU_SECRET_KEY):
        return {"en": en_text, "zh": en_text}
    try:
        zh_text = translate_long_text(en_text)
        if zh_text and zh_text.strip():
            logging.info(f"✅ 翻译完成({len(en_text)}字→{len(zh_text)}字): {en_text[:20]}...")
            return {"en": en_text, "zh": zh_text}
        return {"en": en_text, "zh": en_text}
    except Exception as e:
        logging.error(f"❌ 翻译异常: {e}")
        return {"en": en_text, "zh": en_text}


# ===================== 正文抓取 =====================
@retry
def fetch_article_content(url):
    ERROR_PAGE_SIGNS = [
        "503", "502", "500", "404",
        "that's an error", "service error", "not available at this time",
        "access denied", "forbidden", "cloudflare", "just a moment",
        "please enable cookies", "enable javascript",
        "our systems have detected unusual traffic",
    ]
    try:
        resp = requests.get(
            url, headers=HEADERS, timeout=GLOBAL_TIMEOUT,
            verify=False, allow_redirects=True
        )
        if resp.status_code != 200:
            logging.warning(f"⚠️ 抓取返回 {resp.status_code}: {url[:60]}")
            return ""
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")
        page_text_sample = soup.get_text()[:500].lower()
        if any(sign in page_text_sample for sign in ERROR_PAGE_SIGNS):
            logging.warning(f"⚠️ 检测到错误页面: {url[:60]}")
            return ""
        for tag in soup.find_all(["script", "style", "nav", "header",
                                   "footer", "aside", "figure", "figcaption",
                                   "noscript", "iframe"]):
            tag.decompose()
        for tag in soup.find_all(class_=re.compile(
            r"(ad|ads|advert|sponsor|promo|related|recommend|sidebar|"
            r"newsletter|subscribe|comment|social|share|cookie|banner)", re.I
        )):
            tag.decompose()

        content_el = None
        if "arxiv.org" in url:
            content_el = soup.find("blockquote", class_="abstract mathjax")
        elif "openai.com" in url:
            content_el = soup.find("div", class_=re.compile(r"post.?content", re.I)) or soup.find("main")
        elif "anthropic.com" in url:
            content_el = soup.find("div", class_=re.compile(r"post.?content|article.?body", re.I)) or soup.find("main")
        elif "deepmind.google" in url:
            content_el = soup.find("div", class_=re.compile(r"article.?body|post.?content", re.I)) or soup.find("main")
        elif "venturebeat.com" in url:
            content_el = soup.find("div", class_=re.compile(r"article.?content|entry.?content", re.I)) or soup.find("article")
        elif "techcrunch.com" in url:
            article = soup.find("article")
            if article:
                paras = [p.get_text(" ", strip=True) for p in article.find_all("p") if len(p.get_text(strip=True)) > 40]
                return clean_content(" ".join(paras))
        elif "technologyreview.com" in url:
            content_el = soup.find("div", class_=re.compile(r"article.?body|content.?body", re.I)) or soup.find("article")
        elif "forbes.com" in url:
            content_el = soup.find("div", class_=re.compile(r"article.?body|body.?text", re.I)) or soup.find("article")
        elif "reuters.com" in url or "bloomberg.com" in url:
            content_el = soup.find("div", attrs={"data-testid": re.compile(r"body|article", re.I)})
        elif "axios.com" in url:
            content_el = soup.find("div", class_=re.compile(r"story.?content|article.?body", re.I)) or soup.find("article")
        elif "cnbc.com" in url:
            content_el = soup.find("div", class_=re.compile(r"article.?body|story.?body", re.I)) or soup.find("article")
        elif "wired.com" in url:
            content_el = soup.find("div", class_=re.compile(r"article.?body|content.?body", re.I)) or soup.find("article")
        elif "arstechnica.com" in url:
            content_el = soup.find("div", class_="article-content") or soup.find("article")

        if content_el:
            paras = [p.get_text(" ", strip=True) for p in content_el.find_all("p") if len(p.get_text(strip=True)) > 30]
            text  = " ".join(paras) if paras else content_el.get_text(" ", strip=True)
            return clean_content(text)

        # 通用兜底
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 40][:15]
        return clean_content(" ".join(paras))

    except Exception as e:
        logging.error(f"❌ 抓取正文失败 [{url[:50]}]: {e}")
        return ""


def get_rich_content(entry, url):
    """
    多级内容获取：
    1. 若 URL 已是真实文章 URL（非 Google News），直接抓取全文
    2. RSS full content（arXiv 等）
    3. RSS summary（足够长时使用）
    4. 抓取原文
    5. 标题兜底
    """
    is_google_news = "news.google.com" in url

    # Google News 链接说明解码失败，只能用 RSS summary
    if is_google_news:
        raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
        summary = strip_html(raw_summary)
        if summary and len(summary) >= 20:
            logging.info(f"  [内容] Google News RSS summary ({len(summary)}字)")
            return summary
        title = clean_text(getattr(entry, "title", ""))
        return title or "Visit the original article for more details."

    # 真实 URL：截断型站点直接抓取
    FORCE_FETCH_DOMAINS = [
        "techcrunch.com", "venturebeat.com", "forbes.com",
        "technologyreview.com", "reuters.com", "bloomberg.com",
        "axios.com", "cnbc.com", "wired.com", "arstechnica.com",
        "theverge.com", "businessinsider.com",
    ]
    force_fetch = any(d in url for d in FORCE_FETCH_DOMAINS)

    if not force_fetch:
        # RSS full content（arXiv、官方博客等）
        if hasattr(entry, "content") and entry.content:
            raw  = entry.content[0].get("value", "")
            text = strip_html(raw)
            if len(text) >= CONTENT_MIN_LEN:
                logging.info(f"  [内容] RSS full content ({len(text)}字)")
                return text
        # RSS summary（足够长）
        raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
        summary = strip_html(raw_summary)
        if len(summary) >= 200:
            logging.info(f"  [内容] RSS summary ({len(summary)}字)")
            return summary

    # 抓取原文
    logging.info(f"  [内容] 抓取原文: {url[:70]}")
    fetched = fetch_article_content(url) or ""
    if len(fetched) >= CONTENT_MIN_LEN:
        logging.info(f"  [内容] 抓取成功 ({len(fetched)}字)")
        return fetched

    # 降级 RSS summary
    raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
    summary = strip_html(raw_summary)
    if summary:
        logging.warning(f"  [内容] 降级RSS summary ({len(summary)}字)")
        return summary

    title = clean_text(getattr(entry, "title", ""))
    return f"{title}. Visit the original article for more details." if title else "AI industry update."


# ===================== HTML 生成 =====================
def generate_bilingual_html(article, index):
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

    if not content_zh.strip():
        content_zh = content_en
    if not title_zh.strip():
        title_zh = title_en

    logging.info(f"[HTML] #{index} EN={len(content_en)}字 ZH={len(content_zh)}字")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI资讯日报 {today} · 第{index}条</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;background:#f0f2f5;color:#1a1a1a;line-height:1.8;min-height:100vh;display:flex;flex-direction:column;}}
.header{{background:linear-gradient(135deg,#0052cc 0%,#1a75ff 100%);color:#fff;padding:22px 32px;}}
.header-inner{{max-width:1100px;margin:0 auto;}}
.header h1{{font-size:20px;font-weight:700;margin-bottom:8px;}}
.badges{{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px;}}
.badge{{background:rgba(255,255,255,.20);border-radius:20px;padding:3px 12px;font-size:12px;white-space:nowrap;}}
.main{{flex:1;max-width:1100px;width:100%;margin:24px auto;padding:0 16px 16px;}}
.bilingual-wrapper{{display:grid;grid-template-columns:1fr 1fr;background:#fff;border-radius:12px;box-shadow:0 2px 20px rgba(0,0,0,.10);overflow:hidden;min-height:260px;}}
.col{{padding:28px;}}
.col.en{{background:#f7f9fc;border-right:1px solid #e5eaf0;}}
.lang-tag{{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#0052cc;background:#e6eeff;border-radius:4px;padding:3px 10px;margin-bottom:14px;}}
.col.zh .lang-tag{{color:#c0392b;background:#fdecea;}}
.col-title{{font-size:17px;font-weight:700;line-height:1.5;color:#111;margin-bottom:14px;}}
.col-content{{font-size:14px;line-height:1.95;color:#444;}}
.footer{{max-width:1100px;width:100%;margin:0 auto;padding:14px 16px 32px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;}}
.btn{{display:inline-block;padding:9px 20px;border-radius:8px;font-size:13px;font-weight:600;text-decoration:none;cursor:pointer;transition:all .15s ease;border:none;}}
.btn-primary{{background:#0052cc;color:#fff;}}
.btn-ghost{{background:#fff;color:#333;border:1px solid #d0d5dd;}}
.footer-note{{font-size:12px;color:#aaa;}}
@media(max-width:640px){{.bilingual-wrapper{{grid-template-columns:1fr;}}.col.en{{border-right:none;border-bottom:1px solid #e5eaf0;}}.header{{padding:16px;}}.col{{padding:18px 16px;}}}}
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
    <button class="btn btn-ghost" onclick="try{{if(window.history.length>1){{window.history.back();}}else{{window.close();}}}}catch(e){{window.close();}}">← 关闭</button>
  </div>
  <span class="footer-note">来源：{source} · AI资讯日报自动推送</span>
</div>
</body>
</html>"""
    return html


OSS_ACCESS_KEY_ID     = os.getenv("OSS_ACCESS_KEY_ID", "")
OSS_ACCESS_KEY_SECRET = os.getenv("OSS_ACCESS_KEY_SECRET", "")
OSS_BUCKET            = "ai-news-daily"
OSS_ENDPOINT          = "oss-cn-beijing.aliyuncs.com"
OSS_BASE_URL          = f"https://{OSS_BUCKET}.{OSS_ENDPOINT}"


def upload_to_oss(html, index):
    """
    上传 HTML 到阿里云 OSS，返回公网可访问的 URL。
    使用 OSS REST API + HMAC-SHA1 签名。
    """
    if not (OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET):
        logging.error("❌ OSS_ACCESS_KEY_ID 或 OSS_ACCESS_KEY_SECRET 未配置")
        return None

    import hmac
    import hashlib
    import base64
    from datetime import datetime, timezone

    file_name    = f"ai_news_{index}_{get_today()}.html"
    object_key   = file_name
    content      = html.encode("utf-8")
    content_type = "text/html; charset=utf-8"

    # GMT 时间格式（OSS 要求）
    date_str = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    # 签名字符串：PUT\n\ncontent-type\ndate\n/bucket/key
    # content-md5 留空（不传则为空行）
    string_to_sign = "\n".join([
        "PUT",
        "",               # Content-MD5 留空
        content_type,
        date_str,
        f"/{OSS_BUCKET}/{object_key}"
    ])

    signature = base64.b64encode(
        hmac.new(
            OSS_ACCESS_KEY_SECRET.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1
        ).digest()
    ).decode()

    url = f"https://{OSS_BUCKET}.{OSS_ENDPOINT}/{object_key}"
    headers = {
        "Authorization": f"OSS {OSS_ACCESS_KEY_ID}:{signature}",
        "Content-Type":  content_type,
        "Date":          date_str,
    }

    try:
        resp = requests.put(url, data=content, headers=headers, timeout=25)
        if resp.status_code == 200:
            logging.info(f"✅ OSS上传成功: {url}")
            return url
        logging.error(f"❌ OSS上传失败 {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logging.error(f"❌ OSS上传异常: {e}")
    return None


# ===================== 过滤函数 =====================
CHINESE_DOMAINS = [
    "sina.com.cn", "sina.cn", "sohu.com", "163.com", "qq.com",
    "weibo.com", "zhihu.com", "36kr.com", "ifeng.com", "xinhua",
    "people.com.cn", "cnbeta", "sspai.com", "jiemian.com",
    "jiqizhixin.com", "leiphone.com", "infoq.cn", "oschina.net",
    "baidu.com", "toutiao.com", "csdn.net", "juejin.cn",
]

def is_chinese_url(url):
    return any(d in url for d in CHINESE_DOMAINS)


TARGET_COMPANIES = {
    "OpenAI":    ["openai", "chatgpt", "gpt-4", "gpt-5", "sora", "o1", "o3", "sam altman"],
    "Google":    ["google ai", "google deepmind", "deepmind", "gemini", "sundar pichai"],
    "Anthropic": ["anthropic", "claude ", "dario amodei"],
    "Microsoft": ["microsoft ai", "copilot", "azure ai"],
    "Meta":      ["meta ai", "llama", "meta llm"],
    "DeepSeek":  ["deepseek", "deep seek"],
    "Manus":     ["manus ai", "manus agent"],
    "腾讯":      ["tencent ai", "tencent", "hunyuan"],
    "字节跳动":  ["bytedance", "doubao", "coze"],
    "阿里巴巴":  ["alibaba ai", "qwen", "tongyi"],
    "Kimi":      ["kimi", "moonshot ai"],
    "智谱AI":    ["zhipu", "chatglm", "glm-"],
    "MiniMax":   ["minimax", "abab"],
}

HARD_EXCLUDE = [
    "cyberwar", "cyber war", "espionage", "surveillance state",
    "disinformation", "propaganda", "influence operation",
    "election interference", "congressional", "senate hearing",
    "geopolitical", "sanctions", "export ban", "trade war",
    "national security", "warfare", "bioweapon", "nuclear weapon",
    "chinese government", "chinese official", "beijing government",
    "cia", "nsa", "fbi", "doj ", "white house", "lawmaker", "legislat",
    "election", "congress", "senate", "trump", "biden", "immigration",
    "cancer", "tumor", "gene therapy", "vaccine", "drug trial",
    "surgery", "clinical trial", "patient", "hospital",
    "earthquake", "hurricane", "flood", "wildfire",
]

CORE_AI_WORDS = [
    "large language model", "llm", "foundation model", "generative ai",
    "ai model", "ai system", "ai tool", "machine learning model",
    "neural network", "transformer model", "diffusion model",
    "ai chip", "nvidia gpu", "ai infrastructure", "ai startup",
    "ai funding", "ai investment", "ai agent", "ai assistant", "ai platform",
]

BROAD_AI_WORDS = [
    " ai ", "artificial intelligence", "machine learning",
    "neural", "autonomous", "automation", "inference",
    "fine-tun", "embedding", "multimodal", "rag ", "hugging face",
]


def is_target_company_news(title, summary=""):
    text = (title + " " + summary[:200]).lower()
    for company, keywords in TARGET_COMPANIES.items():
        if any(kw in text for kw in keywords):
            return True, company
    return False, None


def is_ai_related(title, summary=""):
    text        = (title + " " + summary).lower()
    title_lower = title.lower()
    if any(kw in title_lower for kw in HARD_EXCLUDE):
        return False
    is_target, _ = is_target_company_news(title, summary)
    if is_target:
        return True
    if any(kw in text for kw in CORE_AI_WORDS):
        return True
    return any(kw in text for kw in BROAD_AI_WORDS)


# ===================== 文章构建 =====================
def _make_article(entry, source, hot_range, real_link=None):
    """构建文章：解析真实URL → 抓取全文 → 翻译"""
    link = real_link or getattr(entry, "link", "") or ""

    # Google News 链接需先解码
    if "news.google.com" in link:
        resolved = resolve_google_news_url(link)
        if resolved is None:
            logging.warning(f"  🚫 中文落地页跳过")
            return None
        link = resolved

    if is_chinese_url(link):
        logging.warning(f"  🚫 中文站点跳过: {link[:60]}")
        return None

    title       = safe_translate(clean_title(entry.title))
    raw_content = get_rich_content(entry, link)
    content     = safe_translate(raw_content)

    return {
        "title":     title,
        "content":   content,
        "link":      link,
        "source":    source,
        "hot_score": round(random.uniform(*hot_range), 1)
    }


# ===================== 爬虫 =====================
COMPANY_BADGE = {
    "OpenAI": "🟢", "Anthropic": "🟠", "Google": "🔵",
    "DeepSeek": "🔴", "字节跳动": "⚫", "腾讯": "🟣",
    "阿里巴巴": "🟡", "Kimi": "🌙", "智谱AI": "💎",
    "MiniMax": "🌊", "Manus": "⚡", "Microsoft": "🔷", "Meta": "🔸",
}


def crawl_target_company_news():
    """重点公司新闻：Google News RSS + Base64解码获取真实URL + 抓全文"""
    results = []
    COMPANY_QUERIES = [
        ("OpenAI",            "OpenAI",   (88, 95)),
        ("Anthropic Claude",  "Anthropic",(87, 94)),
        ("Google Gemini AI",  "Google",   (86, 93)),
        ("DeepSeek AI model", "DeepSeek", (87, 94)),
        ("ByteDance Doubao",  "字节跳动", (85, 92)),
        ("Tencent Hunyuan AI","腾讯",     (84, 91)),
        ("Alibaba Qwen AI",   "阿里巴巴", (84, 91)),
        ("Kimi Moonshot AI",  "Kimi",     (83, 90)),
        ("MiniMax AI model",  "MiniMax",  (82, 89)),
        ("Manus AI agent",    "Manus",    (83, 90)),
    ]

    for query, company, hot_range in COMPANY_QUERIES:
        if len(results) >= 2:
            break
        try:
            rss_url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(rss_url)
            if not feed.entries:
                continue

            for entry in feed.entries[:15]:
                title   = getattr(entry, "title", "")
                summary = getattr(entry, "summary", "")
                if len(title) < 10:
                    continue
                if not is_ai_related(title, summary):
                    continue

                article = _make_article(entry, f"Google News · {company}", hot_range)
                if article is None:
                    continue

                content_en = (article.get("content") or {}).get("en", "")
                if len(content_en.strip()) < 20:
                    logging.warning(f"  ⚠️ 内容过短，跳过: {title[:40]}")
                    continue

                article["company_tag"] = company
                logging.info(f"🎯 重点公司 [{company}]: {title[:60]}")
                results.append(article)
                break

        except Exception as e:
            logging.warning(f"⚠️ 公司爬虫 [{company}]: {e}")

    logging.info(f"重点公司爬虫: 获取{len(results)}条")
    return results


def crawl_openai():
    try:
        feed = feedparser.parse("https://openai.com/blog/rss/")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        logging.info(f"OpenAI: {entry.title[:60]}")
        return [_make_article(entry, "OpenAI 官方博客", (86, 92))]
    except Exception as e:
        logging.error(f"❌ OpenAI: {e}")
        return []


def crawl_anthropic():
    try:
        feed = feedparser.parse("https://www.anthropic.com/news/rss")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        logging.info(f"Anthropic: {entry.title[:60]}")
        return [_make_article(entry, "Anthropic 官方", (85, 91))]
    except Exception as e:
        logging.error(f"❌ Anthropic: {e}")
        return []


def crawl_google_deepmind():
    try:
        feed = feedparser.parse("https://deepmind.google/blog/rss.xml")
        if not feed.entries:
            return []
        entry = feed.entries[0]
        logging.info(f"Google/DeepMind: {entry.title[:60]}")
        return [_make_article(entry, "Google DeepMind", (85, 91))]
    except Exception as e:
        logging.error(f"❌ DeepMind: {e}")
        return []


def crawl_arxiv():
    ARXIV_MUST_HAVE = [
        "language model", "llm", "large language", "neural network",
        "deep learning", "transformer", "diffusion model", "generative model",
        "reinforcement learning", "fine-tuning", "pre-train", "foundation model",
        "prompt", "chatgpt", "gpt", "bert", "attention mechanism",
        "multimodal", "text generation", "image generation", "reasoning",
        "alignment", "rlhf", "in-context learning", "chain-of-thought",
        "ai agent", "retrieval augmented", "embedding model",
    ]
    try:
        for category in ["cs.AI", "cs.CL", "cs.LG"]:
            feed = feedparser.parse(f"https://rss.arxiv.org/rss/{category}")
            for entry in feed.entries[:15]:
                title   = entry.title.replace("\n", " ")
                summary = getattr(entry, "summary", "")
                text    = (title + " " + summary).lower()
                if any(kw in text for kw in ARXIV_MUST_HAVE):
                    logging.info(f"arXiv [{category}]: {title[:60]}")
                    return [_make_article(entry, "arXiv 学术论文", (88, 93))]
        logging.warning("⚠️ arXiv: 未找到符合条件的论文")
        return []
    except Exception as e:
        logging.error(f"❌ arXiv: {e}")
        return []


def crawl_the_verge():
    try:
        feed = feedparser.parse("https://www.theverge.com/rss/index.xml")
        for entry in feed.entries[:15]:
            title   = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            if is_ai_related(title, summary):
                logging.info(f"The Verge: {title[:60]}")
                return [_make_article(entry, "The Verge", (83, 89))]
        return []
    except Exception as e:
        logging.error(f"❌ The Verge: {e}")
        return []


def crawl_ars_technica():
    try:
        feed = feedparser.parse("https://feeds.arstechnica.com/arstechnica/index")
        for entry in feed.entries[:15]:
            title   = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            if is_ai_related(title, summary):
                logging.info(f"Ars Technica: {title[:60]}")
                return [_make_article(entry, "Ars Technica", (83, 89))]
        return []
    except Exception as e:
        logging.error(f"❌ Ars Technica: {e}")
        return []


def crawl_venturebeat():
    try:
        feed = feedparser.parse("https://venturebeat.com/feed/")
        for entry in feed.entries[:15]:
            title   = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            if is_ai_related(title, summary):
                logging.info(f"VentureBeat: {title[:60]}")
                return [_make_article(entry, "VentureBeat", (82, 88))]
        return []
    except Exception as e:
        logging.error(f"❌ VentureBeat: {e}")
        return []


def crawl_techcrunch():
    try:
        feed = feedparser.parse("https://techcrunch.com/feed/")
        for entry in feed.entries[:15]:
            title   = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            if is_ai_related(title, summary):
                logging.info(f"TechCrunch: {title[:60]}")
                return [_make_article(entry, "TechCrunch", (82, 88))]
        return []
    except Exception as e:
        logging.error(f"❌ TechCrunch: {e}")
        return []


def crawl_hackernews():
    try:
        feed = feedparser.parse("https://news.ycombinator.com/rss")
        for entry in feed.entries[:30]:
            title   = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            if is_ai_related(title, summary):
                logging.info(f"HackerNews: {title[:60]}")
                return [_make_article(entry, "HackerNews", (79, 85))]
        return []
    except Exception as e:
        logging.error(f"❌ HackerNews: {e}")
        return []


# ===================== 飞书推送 =====================
def send_to_feishu(articles):
    if not FEISHU_WEBHOOK:
        logging.error("❌ FEISHU_WEBHOOK 未配置")
        return

    IDX_EMOJI = {1:"1️⃣", 2:"2️⃣", 3:"3️⃣", 4:"4️⃣", 5:"5️⃣"}
    SOURCE_ICON = {
        "arXiv 学术论文":  "📐",
        "OpenAI 官方博客": "🤖",
        "Anthropic 官方":  "🧠",
        "Google DeepMind": "🔬",
        "The Verge":       "⚡",
        "Ars Technica":    "🛠️",
        "VentureBeat":     "📊",
        "TechCrunch":      "💡",
        "HackerNews":      "🔥",
    }

    elements = []
    for idx, article in enumerate(articles, 1):
        title_zh    = (article.get("title")   or {}).get("zh") or (article.get("title") or {}).get("en") or "无标题"
        title_en    = (article.get("title")   or {}).get("en") or ""
        content_zh  = (article.get("content") or {}).get("zh") or (article.get("content") or {}).get("en") or "暂无摘要"
        source      = article.get("source",    "未知来源")
        hot_score   = article.get("hot_score", "N/A")
        orig_link   = article.get("link",      "#")
        company_tag = article.get("company_tag", "")

        num_emoji  = IDX_EMOJI.get(idx, f"{idx}.")
        src_icon   = SOURCE_ICON.get(source, "📰")
        # 取 source 的简短名（去掉 "Google News · " 前缀）
        src_display = source.replace("Google News · ", "") if "Google News" in source else source
        summary_zh  = content_zh[:200] + "..." if len(content_zh) > 200 else content_zh
        title_line  = f"**英文标题**：{title_en[:100]}\n\n" if title_en else ""

        company_line = ""
        if company_tag:
            badge = COMPANY_BADGE.get(company_tag, "🏢")
            company_line = f"{badge} **{company_tag}**　"

        bilingual_url = upload_to_oss(generate_bilingual_html(article, idx), idx)

        action_buttons = []
        if bilingual_url:
            action_buttons.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "📄 查看中英对照"},
                "type": "primary",
                "url": bilingual_url
            })
        if orig_link and orig_link != "#":
            action_buttons.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔗 查看原文"},
                "type": "default",
                "url": orig_link
            })

        card_elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**{num_emoji} {title_zh}**\n"
                        f"{company_line}{src_icon} {src_display}　🔥 热度 {hot_score}\n\n"
                        f"{title_line}"
                        f"**中文摘要**：{summary_zh}"
                    )
                }
            },
        ]
        if action_buttons:
            card_elements.append({"tag": "action", "actions": action_buttons})
        card_elements.append({"tag": "hr"})
        elements.extend(card_elements)

    # 去掉最后一个 hr
    if elements and elements[-1].get("tag") == "hr":
        elements.pop()

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"🤖 全球AI资讯日报 | {get_today()}"
                },
                "template": "blue"
            },
            "elements": elements
        }
    }

    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=15)
        if resp.status_code == 200 and resp.json().get("StatusCode") == 0:
            logging.info("✅ 飞书推送成功")
        else:
            logging.error(f"❌ 飞书推送失败: {resp.text[:200]}")
    except Exception as e:
        logging.error(f"❌ 飞书推送异常: {e}")


# ===================== 主函数 =====================
def main():
    logging.info("🚀 AI资讯日报 v7 启动")
    logging.info(f"📅 今日日期：{get_today()}")

    # 爬虫列表（顺序决定优先级）
    editorial_crawlers = [
        crawl_openai,
        crawl_anthropic,
        crawl_google_deepmind,
        crawl_arxiv,
        crawl_the_verge,
        crawl_ars_technica,
        crawl_venturebeat,
        crawl_techcrunch,
        crawl_hackernews,
    ]

    # 分别爬取
    editorial_articles = []
    for crawler in editorial_crawlers:
        try:
            results = crawler() or []
            if results:
                editorial_articles.extend(results)
                logging.info(f"✅ {crawler.__name__} → {len(results)} 条")
            else:
                logging.warning(f"⚠️ {crawler.__name__} → 0 条")
        except Exception as e:
            logging.error(f"❌ {crawler.__name__} 崩溃: {e}")

    company_articles = []
    try:
        company_articles = crawl_target_company_news() or []
    except Exception as e:
        logging.error(f"❌ crawl_target_company_news 崩溃: {e}")

    all_articles = editorial_articles + company_articles

    # 过滤
    QUALITY_BLACKLIST = [
        "服务错误", "error_code", "unauthorized", "rate limit",
        "that's an error", "service error", "503 service",
        "access denied", "enable javascript", "cloudflare",
        "our systems have detected",
    ]

    seen_titles = set()

    def filter_articles(articles):
        valid = []
        for a in articles:
            if not (a and isinstance(a.get("title"), dict) and a["title"].get("en")):
                continue
            title_en   = a["title"].get("en", "").strip()
            content_en = (a.get("content") or {}).get("en", "")
            content_zh = (a.get("content") or {}).get("zh", "")

            if is_chinese_url(a.get("link", "")):
                continue
            title_key = title_en.lower().strip()
            if title_key in seen_titles:
                logging.warning(f"🚫 重复标题跳过: {title_en[:50]}")
                continue
            seen_titles.add(title_key)
            if not is_ai_related(title_en, content_en[:500]):
                logging.warning(f"🚫 非AI内容过滤: {title_en[:50]}")
                continue
            check_text = (content_zh + content_en).lower()
            if any(p in check_text for p in [q.lower() for q in QUALITY_BLACKLIST]):
                logging.warning(f"🚫 内容质量过滤: {title_en[:50]}")
                continue
            if len(content_zh.strip()) < 20:
                logging.warning(f"🚫 内容过短过滤: {title_en[:50]}")
                continue
            if len(title_en) < 10:
                continue
            valid.append(a)
        return valid

    valid_editorial = filter_articles(editorial_articles)
    valid_company   = filter_articles(company_articles)

    # 按热度排序
    valid_editorial = sorted(valid_editorial, key=lambda x: float(x.get("hot_score", 0) or 0), reverse=True)
    valid_company   = sorted(valid_company,   key=lambda x: float(x.get("hot_score", 0) or 0), reverse=True)

    # 分槽位：前3条优质渠道 + 后2条重点公司
    top3 = valid_editorial[:3]
    top2 = valid_company[:2]

    # 互补：优质渠道不足3条，用公司文章补
    if len(top3) < 3:
        used = {(a.get("title") or {}).get("en","").lower().strip() for a in top3}
        for a in valid_company[len(top2):]:
            if len(top3) >= 3:
                break
            key = (a.get("title") or {}).get("en","").lower().strip()
            if key not in used:
                top3.append(a)
                used.add(key)

    # 互补：公司文章不足2条，用优质渠道补
    if len(top2) < 2:
        used = {(a.get("title") or {}).get("en","").lower().strip() for a in top2}
        for a in valid_editorial[len(top3):]:
            if len(top2) >= 2:
                break
            key = (a.get("title") or {}).get("en","").lower().strip()
            if key not in used:
                top2.append(a)
                used.add(key)

    final = top3 + top2

    # 还不足5条从剩余里补
    if len(final) < 5:
        logging.warning(f"⚠️ 仅 {len(final)} 条，尝试补足至5条")
        used = {(a.get("title") or {}).get("en","").lower().strip() for a in final}
        remaining = sorted(
            filter_articles([a for a in all_articles if (a.get("title") or {}).get("en","").lower().strip() not in used]),
            key=lambda x: float(x.get("hot_score", 0) or 0), reverse=True
        )
        for a in remaining:
            if len(final) >= 5:
                break
            final.append(a)

    logging.info(f"📋 最终推送 {len(final)} 条（优质渠道{len(top3)}条 + 重点公司{len(top2)}条）")
    if len(final) < 5:
        logging.warning(f"⚠️ 最终只有 {len(final)} 条")

    send_to_feishu(final)
    logging.info("🏁 任务完成")


if __name__ == "__main__":
    main()
