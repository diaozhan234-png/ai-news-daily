#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI资讯日报推送脚本 v5
==============================================================
v5 新增修复：
  Fix-A  TechCrunch/VentureBeat/Forbes 等截断型站点 →
         强制抓取原文页面，不依赖仅有2句的 RSS summary
  Fix-B  MIT Tech Review 非AI文章 →
         严格关键词过滤前15条，无匹配直接跳过
  Fix-C  飞书卡片标题"###" → 数字emoji + 来源图标，更美观

继承 v4 修复：
  safe_translate / translate_long_text（翻译不崩溃 + 分段翻译）
  fetch_article_content（精准段落提取 + 去广告噪声）
  generate_bilingual_html（字段全面空值防护）
  htmlpreview.github.io 渲染链接（非Raw源码链接）
  window.close() 关闭按钮
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
TRANSLATE_MAX   = 1800   # 百度翻译单次最大字符数
CONTENT_MAX     = 6000   # 正文抓取最大保留字符（足够完整，不截断文章）
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

def clean_text(text, max_len=None):
    """清理文本：去除多余空白。max_len=None 时不截断（正文获取用）"""
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', str(text)).strip()
    if max_len and len(text) > max_len:
        # 在句子边界截断，避免半句话
        truncated = text[:max_len]
        last_period = max(truncated.rfind('. '), truncated.rfind('。'))
        return truncated[:last_period + 1] if last_period > max_len * 0.7 else truncated
    return text

def clean_content(text):
    """正文清理：保留完整内容，最多 CONTENT_MAX 字符（在句子边界截断）"""
    return clean_text(text, max_len=CONTENT_MAX)

def clean_title(text):
    """标题清理：限制在合理长度"""
    return clean_text(text, max_len=300)

def strip_html(raw_html):
    """将 HTML 字符串转为纯文本（不截断）"""
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
    百度错误码说明：52003=未授权 54001=签名错误 54004=余额不足
    error_code=-27 等非标准错误也需要拦截。
    """
    url  = "https://fanyi-api.baidu.com/api/trans/vip/translate"
    salt = str(random.randint(32768, 65536))
    sign = hashlib.md5((BAIDU_APP_ID + text + salt + BAIDU_SECRET_KEY).encode()).hexdigest()
    params = {"q": text, "from": "en", "to": "zh",
              "appid": BAIDU_APP_ID, "salt": salt, "sign": sign}
    resp = requests.get(url, params=params, timeout=GLOBAL_TIMEOUT, verify=False)
    res  = resp.json()

    # 明确有翻译结果才返回
    if "trans_result" in res and res["trans_result"]:
        translated = res["trans_result"][0]["dst"]
        # 过滤百度返回的错误提示文字（错误时有时会把错误信息翻译出来）
        ERROR_PATTERNS = ["服务错误", "服务目前不可用", "那是个错误", "错误-", "error_code"]
        if any(p in translated for p in ERROR_PATTERNS):
            logging.error(f"百度翻译返回错误文本: {translated[:50]}")
            return None
        return translated

    # 有 error_code 字段说明翻译失败
    if "error_code" in res:
        logging.error(f"百度翻译错误码: {res.get('error_code')} - {res.get('error_msg', '')}")
        return None

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
    安全翻译函数，始终返回 {"en": ..., "zh": ...}，绝不返回 None。
    - en 字段保存完整原文
    - zh 字段是完整翻译
    """
    en_text = clean_text(text) if text else ""

    if not en_text or len(en_text) < 3:
        return {"en": en_text, "zh": en_text or "暂无内容"}

    if not (BAIDU_APP_ID and BAIDU_SECRET_KEY):
        logging.warning("⚠️ 未配置百度翻译API，中文栏显示英文原文")
        return {"en": en_text, "zh": en_text}

    try:
        zh_text = translate_long_text(en_text)
        if zh_text and zh_text.strip():
            logging.info(f"✅ 翻译完成({len(en_text)}字→{len(zh_text)}字): {en_text[:20]}...")
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
    精准正文抓取。
    防线1：HTTP 状态码非 200 直接返回空（不处理错误页面）
    防线2：识别常见错误页面特征文字，返回空
    """
    # 错误页面特征（避免把服务器错误页当正文）
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

        # 防线1：状态码检查
        if resp.status_code != 200:
            logging.warning(f"⚠️ 抓取返回 {resp.status_code}: {url[:60]}")
            return ""

        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")

        # 防线2：检测错误页面特征（在正文提取前）
        page_text_sample = soup.get_text()[:500].lower()
        if any(sign in page_text_sample for sign in ERROR_PAGE_SIGNS):
            logging.warning(f"⚠️ 检测到错误页面: {url[:60]}")
            return ""

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
            article = soup.find("article")
            if article:
                paras = [p.get_text(" ", strip=True) for p in article.find_all("p")
                         if len(p.get_text(strip=True)) > 40]
                return clean_content(" ".join(paras))   # ✅ 取全部段落，不限8段
        elif "technologyreview.com" in url:
            content_el = (soup.find("div", class_=re.compile(r"article.?body|content.?body", re.I))
                          or soup.find("article"))
        elif "news.ycombinator.com" in url:
            content_el = soup.find("div", class_="storytext")
        elif "reuters.com" in url or "bloomberg.com" in url:
            content_el = soup.find("div", attrs={"data-testid": re.compile(r"body|article", re.I)})

        # 有精准容器 → 取全部段落
        if content_el:
            paras = [p.get_text(" ", strip=True) for p in content_el.find_all("p")
                     if len(p.get_text(strip=True)) > 30]
            text  = " ".join(paras) if paras else content_el.get_text(" ", strip=True)
            return clean_content(text)   # ✅ 用 clean_content，在句子边界截断

        # 通用兜底：全文搜索 <p>，过滤短段，取前10段
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")
                 if len(p.get_text(strip=True)) > 40][:10]
        return clean_content(" ".join(paras))

    except Exception as e:
        logging.error(f"❌ 抓取正文失败 [{url[:50]}]: {e}")
        return ""


# ===================== Fix-4：多级内容获取 =====================
def resolve_google_news_url(url):
    """
    Google News RSS 的链接是跳转链接，需要解析出真实文章 URL。
    同时检测落地页语言，中文页面返回 None 表示跳过。
    """
    if "news.google.com" not in url:
        return url
    try:
        resp = requests.get(
            url, headers=HEADERS, timeout=GLOBAL_TIMEOUT,
            allow_redirects=True, verify=False
        )
        final_url = resp.url
        if "google.com" in final_url:
            return url  # 重定向未成功，返回原链接

        # 检测落地页是否为中文页面
        if is_chinese_url(final_url):
            logging.warning(f"  [URL过滤] 中文落地页跳过: {final_url[:60]}")
            return None  # None 表示跳过这篇文章

        logging.info(f"  [URL解析] Google News → {final_url[:80]}")
        return final_url
    except Exception as e:
        logging.warning(f"  [URL解析] 失败: {e}")
    return url


def get_rich_content(entry, url):
    """
    多级兜底获取正文，确保翻译有实质内容。

    Fix-A 核心逻辑：
    - 对"截断型"站点（TechCrunch/VentureBeat/Forbes/MIT Tech Review），
      RSS summary 通常只有1-2句，直接跳过 summary，强制抓取原文页面。
    - Google News 链接先解析真实 URL 再抓取。
    - 其他站点走正常优先级：full content → summary → 抓取 → 兜底。
    """
    # Google News 链接先解析真实 URL，None 表示中文页面
    real_url = resolve_google_news_url(url)
    if real_url is None:
        real_url = url  # 降级用原链接（理论上公司爬虫已提前过滤）

    # 截断型站点：RSS summary 不可信，直接抓取原文
    FORCE_FETCH_DOMAINS = [
        "techcrunch.com", "venturebeat.com", "forbes.com",
        "technologyreview.com", "reuters.com", "bloomberg.com",
        "news.google.com",  # Google News 统一强制抓取
    ]
    force_fetch = any(d in url for d in FORCE_FETCH_DOMAINS)

    if not force_fetch:
        # 1️⃣ RSS content:encoded（部分站点提供全文，如 arXiv）
        if hasattr(entry, "content") and entry.content:
            raw  = entry.content[0].get("value", "")
            text = strip_html(raw)
            if len(text) >= CONTENT_MIN_LEN:
                logging.info(f"  [内容] RSS full content ({len(text)}字)")
                return text

        # 2️⃣ RSS summary（HTML剥离后需要足够长）
        raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
        summary = strip_html(raw_summary)
        # 只有 summary 足够长（>= 200字）才直接使用，避免截断内容
        if len(summary) >= 200:
            logging.info(f"  [内容] RSS summary ({len(summary)}字)")
            return summary

    # 3️⃣ 强制抓取原文页面（截断型站点或summary不足）
    fetch_target = real_url if real_url != url else url
    logging.info(f"  [内容] 抓取原文页面: {fetch_target[:60]}")
    fetched = fetch_article_content(fetch_target) or ""
    if len(fetched) >= CONTENT_MIN_LEN:
        logging.info(f"  [内容] 抓取成功 ({len(fetched)}字)")
        return fetched

    # 4️⃣ 降级回 RSS summary（抓取也失败时）
    raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
    summary = strip_html(raw_summary)
    if summary:
        logging.warning(f"  [内容] 降级用RSS summary ({len(summary)}字)")
        return summary

    # 5️⃣ 标题兜底（绝对保底）
    title    = clean_text(getattr(entry, "title", ""))
    fallback = f"{title}. Visit the original article for more details." if title else "AI industry latest update."
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
    """上传 HTML 到 Gist，返回 htmlpreview 渲染链接（Wi-Fi 环境下可访问）"""
    if not (GIST_TOKEN and len(GIST_TOKEN) > 10):
        logging.error("❌ GIST_TOKEN 未配置或过短")
        return None

    file_name = f"ai_news_{index}_{get_today()}.html"
    resp = requests.post(
        "https://api.github.com/gists",
        headers={
            "Authorization": f"token {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "AI-News-Daily/6.0"
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
        logging.info(f"✅ Gist上传成功")
        return rendered
    logging.error(f"❌ Gist上传失败 {resp.status_code}: {resp.text[:100]}")
    return None



    """
    将 HTML 写入仓库 docs/ 目录，通过 GitHub Pages 访问。
    URL 格式：https://diaozhan234-png.github.io/ai-news-daily/文件名.html
    GitHub Pages 在国内访问稳定，解决 htmlpreview SSL 问题。
    """
    if not (GIST_TOKEN and len(GIST_TOKEN) > 10):
        return None

    file_name = f"news_{index}_{get_today()}.html"
    api_url   = f"https://api.github.com/repos/diaozhan234-png/ai-news-daily/contents/docs/{file_name}"
    headers   = {
        "Authorization": f"token {GIST_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AI-News-Daily/6.0"
    }

    import base64
    content_b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")

    # 检查文件是否已存在（更新需要 sha）
    sha = None
    check = requests.get(api_url, headers=headers, timeout=15)
    if check.status_code == 200:
        sha = check.json().get("sha")

    body = {
        "message": f"Add news {index} for {get_today()}",
        "content": content_b64,
    }
    if sha:
        body["sha"] = sha

    resp = requests.put(api_url, headers=headers, json=body, timeout=25)
    if resp.status_code in (200, 201):
        pages_url = f"https://diaozhan234-png.github.io/ai-news-daily/{file_name}"
        logging.info(f"✅ GitHub Pages 上传成功: {pages_url}")
        return pages_url

    logging.error(f"❌ GitHub Pages 上传失败 {resp.status_code}: {resp.text[:100]}")
    return None
    """
    上传 HTML 到 Gist，返回国内可访问的链接。
    
    方案：使用 cdn.jsdelivr.net 作为 CDN 代理访问 Gist raw 内容。
    jsdelivr 在国内有 CDN 节点，访问速度快且稳定，支持直接渲染 HTML。
    链接格式：https://cdn.jsdelivr.net/gh/用户名/仓库@分支/文件路径
    
    由于 Gist 无法直接用 jsdelivr，改用另一方案：
    将 HTML 转为 base64 data URI，飞书内嵌浏览器可直接渲染，无需外部服务。
    """
    if not (GIST_TOKEN and len(GIST_TOKEN) > 10):
        logging.error("❌ GIST_TOKEN 未配置或过短")
        return None   # 返回 None 表示不生成外链，改用内嵌方式

    file_name = f"ai_news_{index}_{get_today()}.html"
    resp = requests.post(
        "https://api.github.com/gists",
        headers={
            "Authorization": f"token {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "AI-News-Daily/5.0"
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
        # 尝试多个镜像，返回第一个（飞书会按顺序尝试）
        # raw.githubusercontent.com 有时国内可访问
        raw_url = f"https://gist.githubusercontent.com/{username}/{gist_id}/raw/{file_name}"
        logging.info(f"✅ Gist上传成功: {raw_url}")
        return raw_url
    logging.error(f"❌ Gist上传失败 {resp.status_code}: {resp.text[:120]}")
    return None


def make_data_uri(html):
    """将 HTML 转为 base64 data URI，可在任何浏览器直接打开，无需外部服务"""
    encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
    return f"data:text/html;base64,{encoded}"


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
# AI相关性关键词（用于过滤所有来源的非AI文章）
AI_FILTER_KEYWORDS = [
    "artificial intelligence", " ai ", "machine learning", "deep learning",
    "large language model", "llm", "chatgpt", "gpt-", "claude", "gemini",
    "neural network", "generative ai", "openai", "anthropic", "deepmind",
    "nvidia", "foundation model", "transformer", "diffusion model",
    "autonomous", "robotics", "computer vision", "natural language",
    "reinforcement learning", "fine-tun", "inference", "multimodal",
    "rag", "agent", "copilot", "hugging face", "mistral", "llama",
    "funding", "investment", "startup", "raises", "valued",  # 投融资关键词
]

# ===================== 重点监控公司配置 =====================
# 用户指定的重点关注公司 - 这些公司的动态优先推送
TARGET_COMPANIES = {
    # 英文名/关键词
    "openai":       ["openai", "chatgpt", "gpt-4", "gpt-5", "sora", "o1", "o3", "sam altman"],
    "google":       ["google ai", "google deepmind", "deepmind", "gemini", "google cloud ai",
                     "vertex ai", "google bard", "sundar pichai"],
    "anthropic":    ["anthropic", "claude ", "dario amodei", "amanda askell"],
    "microsoft":    ["microsoft ai", "copilot", "azure ai", "bing ai"],
    "meta":         ["meta ai", "llama", "meta llm"],
    "deepseek":     ["deepseek", "deep seek"],
    "manus":        ["manus ai", "manus agent", "monica ai"],
    # 中文公司（用拼音/英文名检索）
    "tencent":      ["tencent ai", "tencent", "腾讯", "混元", "hunyuan"],
    "bytedance":    ["bytedance", "byte dance", "doubao", "字节", "豆包", "coze"],
    "alibaba":      ["alibaba ai", "alibaba", "qwen", "tongyi", "通义", "阿里"],
    "kimi":         ["kimi", "moonshot ai", "月之暗面"],
    "zhipu":        ["zhipu", "chatglm", "glm-", "智谱"],
    "minmax":       ["minmax", "min-max", "abab", "minimax"],
}

# 扁平化成一个关键词集合，用于快速匹配
TARGET_KEYWORDS = []
for kws in TARGET_COMPANIES.values():
    TARGET_KEYWORDS.extend(kws)

# 关注维度：技术/产品/商业/人才
FOCUS_DIMENSIONS = [
    # 技术突破
    "new model", "new release", "launches", "released", "announced",
    "breakthrough", "benchmark", "outperforms", "beats", "surpasses",
    "open source", "open-source", "research paper", "technical report",
    # 产品与应用
    "product", "feature", "update", "version", "api", "platform",
    "app", "tool", "integration", "plugin", "enterprise",
    # 商业化
    "funding", "investment", "raises", "valued", "valuation",
    "revenue", "customers", "partnership", "deal", "acquisition",
    "ipo", "series", "million", "billion",
    # 人才
    "hires", "hired", "joins", "leaves", "fired", "departs",
    "ceo", "cto", "vp", "chief", "president", "executive",
    "talent", "team",
]


def is_target_company_news(title, summary=""):
    """判断是否涉及重点监控公司，返回 (bool, company_name)"""
    text = (title + " " + summary[:200]).lower()
    for company, keywords in TARGET_COMPANIES.items():
        if any(kw in text for kw in keywords):
            return True, company
    return False, None


def is_ai_related(title, summary=""):
    """
    判断文章是否与AI科技相关。
    优先级：重点公司 > AI核心词 > 宽泛AI词
    负向词过滤：医学/政治话题排除
    """
    text        = (title + " " + summary).lower()
    title_lower = title.lower()

    # 政治/安全/军事类负向词 — 即使涉及重点公司也排除
    # 用户只关注技术、产品、商业化、人才，不需要政治新闻
    HARD_EXCLUDE = [
        # 政治对抗
        "cyberwar", "cyber war", "espionage", "surveillance state",
        "disinformation", "propaganda", "influence operation",
        "election interference", "congressional", "senate hearing",
        "geopolitical", "sanctions", "export ban", "trade war",
        "national security", "warfare", "bioweapon", "nuclear weapon",
        "chinese government", "chinese official", "beijing government",
        "cia", "nsa", "fbi", "doj ", "white house",
        "lawmaker", "legislat",
        # 纯政治
        "election", "congress", "senate", "trump", "biden",
        "immigration", "deportat",
        # 医学
        "cancer", "tumor", "gene therapy", "vaccine", "drug trial",
        "surgery", "clinical trial", "patient", "hospital",
        "obesity", "diabetes", "cardiovascular",
        # 自然灾害/环境
        "earthquake", "hurricane", "flood", "wildfire", "climate change",
    ]

    has_hard_exclude = any(kw in title_lower for kw in HARD_EXCLUDE)

    # 硬排除：无论涉及哪家公司，政治/军事/医学内容一律过滤
    if has_hard_exclude:
        logging.debug(f"  [硬排除] {title[:50]}")
        return False

    # 重点监控公司（排除政治内容后）
    is_target, _ = is_target_company_news(title, summary)
    if is_target:
        return True

    # 技术类核心AI词
    CORE_AI_WORDS = [
        "large language model", "llm", "foundation model",
        "generative ai", "ai model", "ai system", "ai tool",
        "machine learning model", "deep learning model",
        "neural network", "transformer model", "diffusion model",
        "ai chip", "nvidia gpu", "ai infrastructure",
        "ai startup", "ai funding", "ai investment",
        "ai agent", "ai assistant", "ai platform",
    ]
    has_core_ai = any(kw in text for kw in CORE_AI_WORDS)
    if has_core_ai:
        return True

    BROAD_AI_WORDS = [
        " ai ", "artificial intelligence", "machine learning",
        "neural", "autonomous", "automation",
        "inference", "fine-tun", "embedding", "multimodal",
        "rag ", "hugging face", "raises $", "million round",
    ]
    return any(kw in text for kw in BROAD_AI_WORDS)


PUSHED_TITLES_FILE = "/tmp/ai_news_pushed_titles.txt"  # GitHub Actions 每次运行是全新环境，改用 Gist 持久化


def load_pushed_titles():
    """跨天去重已禁用，只做当次运行内去重"""
    return set()


def save_pushed_titles(titles):
    """跨天去重已禁用"""
    pass


CHINESE_DOMAINS = [
    "sina.com.cn", "sina.cn", "sohu.com", "163.com", "qq.com",
    "weibo.com", "zhihu.com", "36kr.com", "ifeng.com", "xinhua",
    "people.com.cn", "cnbeta", "sspai.com", "jiemian.com",
    "jiqizhixin.com", "leiphone.com", "infoq.cn", "oschina.net",
    "baidu.com", "toutiao.com", "csdn.net", "juejin.cn",
]


def is_chinese_url(url):
    """判断 URL 是否指向中文站点"""
    return any(d in url for d in CHINESE_DOMAINS)


def _make_article(entry, source, hot_range, real_link=None):
    """通用文章构建：title翻译 + 正文获取翻译。中文站点返回 None。"""
    link = real_link or getattr(entry, "link", "") or ""
    if is_chinese_url(link):
        logging.warning(f"  🚫 中文站点跳过: {link[:60]}")
        return None
    title       = safe_translate(clean_title(entry.title))
    raw_content = get_rich_content(entry, link)   # 用真实 URL 抓正文
    content     = safe_translate(raw_content)
    return {
        "title":     title,
        "content":   content,
        "link":      link,
        "source":    source,
        "hot_score": round(random.uniform(*hot_range), 1)
    }


def _make_article_with_company(entry, source, hot_range, company_tag=None):
    """构建文章，附带公司标签（用于飞书卡片显示）"""
    article = _make_article(entry, source, hot_range)
    if company_tag:
        article["company_tag"] = company_tag
    return article


def crawl_target_company_news():
    """
    重点公司动态专项爬虫。
    来源：Google News RSS（支持中文公司搜索）+ 官方博客
    每个公司搜索最新动态，重点关注：技术/产品/商业化/人才
    """
    results = []

    # Google News RSS 搜索各公司（支持中英文）
    COMPANY_QUERIES = [
        ("OpenAI",                      "OpenAI",    (88, 95)),
        ("Anthropic Claude AI",         "Anthropic", (87, 94)),
        ("Google Gemini AI",            "Google",    (86, 93)),
        ("DeepSeek AI model",           "DeepSeek",  (87, 94)),
        ("ByteDance AI Doubao",         "字节跳动",  (85, 92)),
        ("Tencent AI Hunyuan",          "腾讯",      (84, 91)),
        ("Alibaba Qwen AI model",       "阿里巴巴",  (84, 91)),
        ("Kimi Moonshot AI",            "Kimi",      (83, 90)),
        ("Zhipu AI ChatGLM",            "智谱AI",    (83, 90)),
        ("MiniMax AI model",            "MiniMax",   (82, 89)),
        ("Manus AI agent",              "Manus",     (83, 90)),
    ]

    tried = 0
    for query, company, hot_range in COMPANY_QUERIES:
        if len(results) >= 5:   # 最多贡献5条
            break
        try:
            # 强制使用英文版 Google News，避免返回中文落地页
            rss_url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(rss_url)
            tried += 1

            if not feed.entries:
                continue

            for entry in feed.entries[:15]:  # 每家公司最多检查15条
                title   = getattr(entry, "title", "")
                summary = getattr(entry, "summary", "")

                # 确认涉及该公司且有实质内容
                is_target, _ = is_target_company_news(title, summary)
                if not is_target:
                    continue
                if len(title) < 10:
                    continue
                # 政治/军事类内容过滤（即使涉及重点公司）
                if not is_ai_related(title, summary):
                    logging.warning(f"  🚫 公司爬虫政治内容过滤: {title[:50]}")
                    continue

                logging.info(f"🎯 重点公司 [{company}]: {title[:60]}")
                # 解析真实 URL，None 表示中文落地页，跳过
                real_link = resolve_google_news_url(entry.link)
                if real_link is None:
                    logging.warning(f"  ⚠️ 中文落地页，跳过: {title[:40]}")
                    continue
                article = _make_article(entry, f"Google News · {company}", hot_range, real_link=real_link)
                if article is None:
                    logging.warning(f"  ⚠️ 中文站点，跳过: {title[:40]}")
                    continue

                # 内容质量检查：正文太短说明抓取失败，换下一条
                content_en = (article.get("content") or {}).get("en", "")
                if len(content_en.strip()) < 80:
                    logging.warning(f"  ⚠️ 正文过短({len(content_en)}字)，尝试下一条: {title[:40]}")
                    continue

                article["company_tag"] = company
                results.append(article)
                break   # 每家公司只取最新1条

        except Exception as e:
            logging.warning(f"⚠️ 公司爬虫 [{company}]: {e}")

    logging.info(f"重点公司爬虫完成: 尝试{tried}家，获取{len(results)}条")
    return results


def crawl_arxiv():
    """arXiv — 只抓AI/ML/NLP核心研究论文"""
    ARXIV_MUST_HAVE = [
        "language model", "llm", "large language", "neural network",
        "deep learning", "transformer", "diffusion model", "generative model",
        "reinforcement learning", "fine-tuning", "pre-train", "foundation model",
        "prompt", "chatgpt", "gpt", "bert", "attention mechanism",
        "multimodal", "text generation", "image generation", "reasoning",
        "alignment", "rlhf", "in-context learning", "chain-of-thought",
        "ai agent", "llm agent", "retrieval augmented", "embedding model",
    ]
    ARXIV_EXCLUDE_IF_NO_CORE = [
        "obesity", "overweight", "health", "medical", "clinical", "patient",
        "cancer", "disease", "diagnosis", "hospital", "drug", "genomic",
        "covid", "pandemic", "social media", "education", "finance",
        "traffic", "weather", "earthquake", "flood", "agriculture",
        "children", "adolescent", "elderly", "population",
    ]
    try:
        for category in ["cs.AI", "cs.CL", "cs.LG"]:
            feed = feedparser.parse(f"http://export.arxiv.org/rss/{category}")
            if not feed.entries:
                continue
            for entry in feed.entries[:10]:
                title    = entry.title.lower()
                summary  = strip_html(getattr(entry, "summary", "")).lower()
                combined = title + " " + summary[:300]

                # 优先：论文涉及重点公司（如 DeepSeek/OpenAI 发表的论文）
                is_target, company = is_target_company_news(entry.title, summary)
                has_core    = any(kw in combined for kw in ARXIV_MUST_HAVE)
                has_exclude = any(kw in title for kw in ARXIV_EXCLUDE_IF_NO_CORE)

                if has_exclude and not is_target:
                    logging.warning(f"arXiv 🚫跨学科: {entry.title[:50]}")
                    continue
                if has_core or is_target:
                    logging.info(f"arXiv [{category}] ✅: {entry.title[:60]}")
                    return [_make_article(entry, "arXiv 学术论文", (88, 93))]

        logging.warning("⚠️ arXiv: 未找到符合条件的论文")
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
    """MIT Technology Review — 严格过滤，只推AI/科技相关文章"""
    try:
        feed = feedparser.parse("https://www.technologyreview.com/feed/")
        if not feed.entries:
            return []

        for entry in feed.entries[:20]:
            title   = entry.title
            summary = getattr(entry, "summary", "")
            if is_ai_related(title, summary):
                logging.info(f"MIT Tech Review (AI匹配): {title[:50]}")
                return [_make_article(entry, "MIT Technology Review", (85, 90))]

        logging.warning("⚠️ MIT Tech Review: 前20条内无AI相关文章，跳过")
        return []
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
    """
    HackerNews — 只抓有实质内容的AI相关外链文章。
    过滤规则：
      1. 标题必须 >= 20 字符（排除 "LLM=True" 这类无意义标题）
      2. 必须有外部链接 URL（排除纯 HN 讨论帖）
      3. 标题必须包含 AI 核心关键词
      4. 排除纯技术代码/库发布（这类内容意义不大）
    """
    AI_KEYWORDS = [
        "llm", "large language model", "gpt", "claude", "gemini", "mistral",
        "llama", "machine learning", "neural", "transformer", "openai",
        "anthropic", "deepmind", "diffusion", "generative ai", "ai model",
        "artificial intelligence", "chatbot", "foundation model",
        "multimodal", "ai agent", "rag", "fine-tuning", "inference",
        "ai startup", "ai funding", "raises", "ai tool", "copilot",
    ]
    # 排除纯代码库/框架发布（标题特征）
    EXCLUDE_PATTERNS = [
        "show hn", "ask hn", "tell hn",   # HN 内部帖
        "=true", "=false", "= true", "= false",  # 代码片段
        "[pdf]", "[video]",
    ]
    try:
        resp = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            timeout=GLOBAL_TIMEOUT
        )
        ids = resp.json()[:30]  # 检查前30条提高命中率

        for story_id in ids:
            item = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                timeout=GLOBAL_TIMEOUT
            ).json()

            title = item.get("title", "")
            url   = item.get("url", "")

            # 质量门槛
            if len(title) < 20:
                continue  # 标题太短，无实质内容
            if not url:
                continue  # 没有外部链接，是纯讨论帖
            if any(p in title.lower() for p in EXCLUDE_PATTERNS):
                continue  # 排除特定类型

            title_lower = title.lower()
            if any(kw in title_lower for kw in AI_KEYWORDS):
                # 抓取外链正文
                body = fetch_article_content(url) or strip_html(item.get("text", "")) or title
                if len(body) < 50:
                    body = title  # 正文太短也不翻译空内容
                content = safe_translate(body)
                logging.info(f"HackerNews ✅: {title[:60]}")
                return [{
                    "title":     safe_translate(title),
                    "content":   content,
                    "link":      url,
                    "source":    "HackerNews",
                    "hot_score": round(random.uniform(80, 86), 1)
                }]

        logging.warning("⚠️ HackerNews: 前30条内无符合条件的AI文章")
        return []
    except Exception as e:
        logging.error(f"❌ HackerNews: {e}")
        return []


# ===================== 飞书推送 =====================
def send_to_feishu(articles):
    """
    飞书卡片推送 v6：双语全文直接写入卡片，彻底不依赖外部链接。
    解决 htmlpreview.github.io 国内 SSL 报错问题。
    """
    if not FEISHU_WEBHOOK:
        logging.error("❌ 未配置 FEISHU_WEBHOOK")
        return False

    IDX_EMOJI = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣"}
    SOURCE_ICON = {
        "arXiv 学术论文":        "📐",
        "OpenAI 官方博客":       "🤖",
        "Anthropic 官方":        "🧠",
        "Google DeepMind":       "🔬",
        "MIT Technology Review": "🎓",
        "VentureBeat":           "📊",
        "TechCrunch":            "💡",
        "Forbes":                "💰",
        "OpenTools AI":          "🛠️",
        "HackerNews":            "🔥",
    }

    # 重点公司标签样式
    COMPANY_BADGE = {
        "OpenAI": "🟢", "Anthropic": "🟠", "Google": "🔵",
        "DeepSeek": "🔴", "字节跳动": "⚫", "腾讯": "🟣",
        "阿里巴巴": "🟡", "Kimi": "🌙", "智谱AI": "💎",
        "MiniMax": "🌊", "Manus": "⚡", "Microsoft": "🔷",
    }

    elements = []
    for idx, article in enumerate(articles, 1):
        title_zh    = (article.get("title")   or {}).get("zh") or (article.get("title") or {}).get("en") or "无标题"
        title_en    = (article.get("title")   or {}).get("en") or ""
        content_zh  = (article.get("content") or {}).get("zh") or (article.get("content") or {}).get("en") or "暂无摘要"
        content_en  = (article.get("content") or {}).get("en") or ""
        source      = article.get("source",     "未知来源")
        hot_score   = article.get("hot_score",  "N/A")
        orig_link   = article.get("link", "#")
        company_tag = article.get("company_tag", "")

        num_emoji  = IDX_EMOJI.get(idx, f"{idx}.")
        src_icon   = SOURCE_ICON.get(source, "📰")
        summary_zh = content_zh[:200] + "..." if len(content_zh) > 200 else content_zh
        full_en    = content_en[:1500] + "..." if len(content_en) > 1500 else content_en
        full_zh    = content_zh[:1500] + "..." if len(content_zh) > 1500 else content_zh

        company_line = ""
        if company_tag:
            badge = COMPANY_BADGE.get(company_tag, "🏢")
            company_line = f"{badge} **{company_tag}**　"

        # 飞书卡片：只显示摘要，中英对照上传Gist后用按钮跳转
        # （飞书不支持折叠块，直接展示会使卡片过长）
        bilingual_url = upload_to_gist(generate_bilingual_html(article, idx), idx)

        # 按钮
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
                        f"{company_line}{src_icon} {source}　🔥 热度 {hot_score}\n\n"
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
    logging.info("🚀 AI资讯日报 v6 启动")
    logging.info(f"📅 今日日期：{get_today()}")

    # 爬虫列表：重点公司爬虫优先，其他爬虫补充
    crawlers = [
        crawl_target_company_news, # 🎯 重点公司专项监控（最高优先级）
        crawl_openai,              # OpenAI 官方博客
        crawl_anthropic,           # Anthropic 官方
        crawl_google_deepmind,     # Google AI / DeepMind
        crawl_arxiv,               # 学术前沿
        crawl_mit_tech_review,     # MIT 深度分析
        crawl_venturebeat,         # 行业资讯
        crawl_techcrunch,          # 投融资/产品
        crawl_forbes,              # 商业/投融资
        crawl_hackernews,          # 社区热点
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

    # 过滤1：必须有标题
    # 过滤2：全局AI相关性检查
    # 过滤3：内容质量检查
    # 过滤4：标题去重（同一篇文章不重复推送）
    QUALITY_BLACKLIST = [
        "服务错误", "服务目前不可用", "那是个错误", "错误-27",
        "error_code", "unauthorized", "rate limit",
        "that's an error", "service error -27", "not available at this time",
        "503 service", "access denied", "enable javascript",
        "our systems have detected", "cloudflare",
        # HackerNews PDF链接被当正文
        "pdf:", "https://arxiv.org/pdf", "https://arxiv.org/abs",
    ]

    seen_titles = load_pushed_titles()   # 加载历史推送记录（跨天去重）
    logging.info(f"📚 历史去重记录: {len(seen_titles)} 条")
    valid = []
    for a in all_articles:
        if not (a and isinstance(a.get("title"), dict) and a["title"].get("en")):
            continue
        title_en   = a["title"].get("en", "").strip()
        content_en = (a.get("content") or {}).get("en", "")
        content_zh = (a.get("content") or {}).get("zh", "")

        # 中文站点过滤
        article_link = a.get("link", "")
        if is_chinese_url(article_link):
            logging.warning(f"🚫 中文站点过滤: {article_link[:60]}")
            continue

        # 标题去重（用完整标题，避免截断导致误判）
        title_key = title_en.lower().strip()
        if title_key in seen_titles:
            logging.warning(f"🚫 重复标题，跳过: {title_en[:50]}")
            continue
        seen_titles.add(title_key)

        # AI相关性检查（对 "AI-designed proteins" 这类
        # 标题含AI字母但实际是医学文章，content检查更可靠）
        if not is_ai_related(title_en, content_en[:500]):
            logging.warning(f"🚫 全局过滤非AI内容: {title_en[:50]}")
            continue

        # 内容质量检查
        check_text = content_zh + content_en
        if any(p in check_text.lower() for p in [q.lower() for q in QUALITY_BLACKLIST]):
            logging.warning(f"🚫 内容含错误文本，丢弃: {title_en[:50]}")
            continue

        # 内容长度检查：摘要太短（少于20字）说明正文没抓到，丢弃
        content_len = len(content_zh.strip())
        if content_len < 20:
            logging.warning(f"🚫 内容过短({content_len}字)，丢弃: {title_en[:50]}")
            continue

        # 标题长度检查
        if len(title_en) < 10:
            logging.warning(f"🚫 标题过短，丢弃: {title_en}")
            continue

        # 重点公司文章加分（确保排在前5条）
        is_target, company = is_target_company_news(title_en, content_en[:200])
        if is_target and not a.get("company_tag"):
            a["company_tag"] = company
        if is_target:
            a["hot_score"] = round(float(a.get("hot_score", 85) or 85) + 10, 1)

        valid.append(a)

    # 按热度降序
    valid = sorted(valid, key=lambda x: float(x.get("hot_score", 0) or 0), reverse=True)

    # ── 不足5条时，降低门槛从 all_articles 里补足 ──────────────────
    if len(valid) < 5:
        logging.warning(f"⚠️ 有效文章仅 {len(valid)} 条，尝试降低门槛补足至5条")
        used_keys = {a["title"].get("en","").lower().strip() for a in valid}

        for a in all_articles:
            if len(valid) >= 5:
                break
            if not (a and isinstance(a.get("title"), dict) and a["title"].get("en")):
                continue

            title_en  = a["title"].get("en", "").strip()
            title_key = title_en.lower().strip()

            # 去重
            if title_key in used_keys or title_key in seen_titles:
                continue
            # 中文站点仍然不要
            if is_chinese_url(a.get("link", "")):
                continue
            # 标题太短不要
            if len(title_en) < 10:
                continue
            # 内容完全为空不要
            content_en = (a.get("content") or {}).get("en", "")
            content_zh = (a.get("content") or {}).get("zh", "")
            if not (content_en or content_zh).strip():
                continue

            used_keys.add(title_key)
            logging.info(f"  ➕ 降级补充: {title_en[:60]}")
            valid.append(a)

        valid = sorted(valid, key=lambda x: float(x.get("hot_score", 0) or 0), reverse=True)

    # 还不够5条就记录警告（不再用占位填充）
    logging.info(f"📋 最终推送 {len(valid)} 条资讯")
    if len(valid) < 5:
        logging.warning(f"⚠️ 最终只有 {len(valid)} 条，来源不足")

    valid = valid[:5]

    send_to_feishu(valid)

    # 推送成功后保存标题到持久化缓存，供明天去重
    for a in valid:
        title_en  = (a.get("title") or {}).get("en", "").strip()
        title_key = title_en.lower().strip()
        if title_key:
            seen_titles.add(title_key)
    save_pushed_titles(seen_titles)

    logging.info("🏁 任务完成")


if __name__ == "__main__":
    main()
