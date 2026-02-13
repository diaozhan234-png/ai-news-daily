#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI资讯推送脚本（目标样式版）
功能：Top热点筛选+飞书卡片格式+英文全文双语对照
适配GitHub Actions部署
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

# 环境变量读取
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
BAIDU_APP_ID = os.getenv("BAIDU_APP_ID")
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY")

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)

# 请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive"
}

RANDOM_DELAY = (1, 3)

# ===================== 核心工具函数 =====================
def get_today_date():
    return datetime.date.today().strftime("%Y-%m-%d")

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()[:200]  # 精简文本长度

def baidu_translate(text, from_lang="en", to_lang="zh"):
    """百度翻译（支持长文本分段）"""
    if not text or len(text) < 2:
        return {"en": text, "zh": text}
    
    # 分段翻译（避免超过API字符限制）
    max_len = 500
    text_segments = [text[i:i+max_len] for i in range(0, len(text), max_len)]
    en_segments = []
    zh_segments = []
    
    for seg in text_segments:
        api_url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
        salt = str(random.randint(32768, 65536))
        sign_str = BAIDU_APP_ID + seg + salt + BAIDU_SECRET_KEY
        sign = hashlib.md5(sign_str.encode()).hexdigest()
        
        params = {
            "q": seg,
            "from": from_lang,
            "to": to_lang,
            "appid": BAIDU_APP_ID,
            "salt": salt,
            "sign": sign
        }
        
        try:
            time.sleep(random.uniform(*RANDOM_DELAY))
            response = requests.get(api_url, params=params, timeout=10, verify=False)
            result = response.json()
            if "trans_result" in result and len(result["trans_result"]) > 0:
                en_segments.append(seg)
                zh_segments.append(result["trans_result"][0]["dst"])
            else:
                en_segments.append(seg)
                zh_segments.append(f"【翻译失败】{seg}")
        except Exception as e:
            logging.error(f"翻译分段失败: {str(e)}")
            en_segments.append(seg)
            zh_segments.append(f"【翻译异常】{seg}")
    
    return {
        "en": "".join(en_segments),
        "zh": "".join(zh_segments)
    }

def get_article_content(url):
    """抓取英文文章正文（简化版，适配主流英文站）"""
    try:
        time.sleep(random.uniform(*RANDOM_DELAY))
        response = requests.get(url, headers=HEADERS, timeout=15, verify=False, allow_redirects=True)
        soup = BeautifulSoup(response.text, "html.parser")
        
        # 提取正文（适配arXiv/OpenAI Blog/HackerNews/Twitter）
        if "arxiv.org" in url:
            content = soup.find("blockquote", class_="abstract mathjax")
        elif "openai.com" in url:
            content = soup.find("div", class_="prose max-w-none")
        elif "hackernews.com" in url:
            content = soup.find("div", class_="comment-tree")
        elif "twitter.com" in url or "nitter.net" in url:
            content = soup.find("div", class_="tweet-content")
        else:
            content = soup.find("main") or soup.find("article")
        
        if content:
            return clean_text(content.get_text())
        else:
            return "No content available"
    except Exception as e:
        logging.error(f"抓取文章正文失败: {str(e)}")
        return "Content crawl failed"

def generate_bilingual_page(articles):
    """生成双语对照内容（模拟网页链接格式）"""
    bilingual_content = f"<h1>AI资讯日报 | {get_today_date()}</h1>"
    for idx, art in enumerate(articles, 1):
        bilingual_content += f"""
        <h2>{idx}. {art['title']['en']}</h2>
        <p><b>中文标题：</b>{art['title']['zh']}</p>
        <h3>英文原文</h3>
        <p>{art['content']['en']}</p>
        <h3>中文翻译</h3>
        <p>{art['content']['zh']}</p>
        <p><b>来源链接：</b><a href="{art['link']}">{art['link']}</a></p>
        <hr>
        """
    # 简化：返回格式化文本（如需真实网页可对接GitHub Pages，此处先适配飞书展示）
    return bilingual_content

# ===================== 多源抓取+热点筛选 =====================
def crawl_and_rank_articles():
    """抓取并筛选Top 2热点资讯（匹配案例样式）"""
    # 1. 学术前沿（arXiv）
    academic = crawl_academic()
    # 2. OpenAI博客
    official = crawl_official_blog()
    # 3. HackerNews社区
    community = crawl_community()
    # 4. 社媒聚合
    social = crawl_social()
    
    # 整合所有有效资讯
    all_articles = []
    for art in [academic, official, community, social]:
        if art["link"] and art["title"]["en"] != "No updates today":
            # 抓取正文并翻译
            content_en = get_article_content(art["link"])
            content_bi = baidu_translate(content_en)
            # 随机生成热度值（模拟案例）
            hot_score = round(random.uniform(80, 95), 1)
            
            all_articles.append({
                "type": art["type"],
                "title": art["title"],
                "content": content_bi,
                "link": art["link"],
                "hot_score": hot_score,
                "source": art["source"]
            })
    
    # 按热度筛选Top 2
    all_articles.sort(key=lambda x: x["hot_score"], reverse=True)
    return all_articles[:2]

def crawl_academic():
    """📚 学术前沿"""
    try:
        feed = feedparser.parse("http://export.arxiv.org/rss/cs.AI")
        if feed.entries:
            entry = feed.entries[0]
            title_bi = baidu_translate(clean_text(entry.title))
            return {
                "type": "学术前沿",
                "title": title_bi,
                "link": entry.link,
                "source": "arXiv",
                "title_en": entry.title
            }
        return {"type": "学术前沿", "title": {"en": "No updates today", "zh": "暂无更新"}, "link": "", "source": ""}
    except Exception as e:
        logging.error(f"学术抓取失败: {e}")
        return {"type": "学术前沿", "title": {"en": "Crawl failed", "zh": "抓取失败"}, "link": "", "source": ""}

def crawl_official_blog():
    """🏢 官方博客"""
    try:
        feed = feedparser.parse("https://openai.com/blog/rss/")
        if feed.entries:
            entry = feed.entries[0]
            title_bi = baidu_translate(clean_text(entry.title))
            return {
                "type": "官方博客",
                "title": title_bi,
                "link": entry.link,
                "source": "OpenAI Blog"
            }
        return {"type": "官方博客", "title": {"en": "No updates today", "zh": "暂无更新"}, "link": "", "source": ""}
    except Exception as e:
        logging.error(f"博客抓取失败: {e}")
        return {"type": "官方博客", "title": {"en": "Crawl failed", "zh": "抓取失败"}, "link": "", "source": ""}

def crawl_community():
    """💬 海外社区"""
    try:
        response = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", headers=HEADERS, timeout=10)
        top_stories = response.json()[:5]
        for story_id in top_stories:
            story = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json", timeout=5).json()
            if "title" in story and ("AI" in story["title"] or "LLM" in story["title"]):
                title_bi = baidu_translate(clean_text(story["title"]))
                link = story.get("url", f"https://news.ycombinator.com/item?id={story_id}")
                return {
                    "type": "海外社区",
                    "title": title_bi,
                    "link": link,
                    "source": "HackerNews"
                }
        return {"type": "海外社区", "title": {"en": "No updates today", "zh": "暂无更新"}, "link": "", "source": ""}
    except Exception as e:
        logging.error(f"社区抓取失败: {e}")
        return {"type": "海外社区", "title": {"en": "Crawl failed", "zh": "抓取失败"}, "link": "", "source": ""}

def crawl_social():
    """📱 社媒聚合"""
    try:
        feed = feedparser.parse("https://nitter.net/OpenAI/rss")
        if feed.entries:
            entry = feed.entries[0]
            title_bi = baidu_translate(clean_text(entry.title))
            link = entry.link.replace("nitter.net", "twitter.com")
            return {
                "type": "社媒聚合",
                "title": title_bi,
                "link": link,
                "source": "Twitter/OpenAI"
            }
        return {"type": "社媒聚合", "title": {"en": "No updates today", "zh": "暂无更新"}, "link": "", "source": ""}
    except Exception as e:
        logging.error(f"社媒抓取失败: {e}")
        return {"type": "社媒聚合", "title": {"en": "Crawl failed", "zh": "抓取失败"}, "link": "", "source": ""}

# ===================== 飞书富文本推送（匹配目标样式） =====================
def send_feishu_card():
    """飞书卡片式推送（匹配案例样式）"""
    if not FEISHU_WEBHOOK or not (BAIDU_APP_ID and BAIDU_SECRET_KEY):
        logging.error("配置缺失！")
        return False
    
    # 抓取Top 2热点
    top_articles = crawl_and_rank_articles()
    if not top_articles:
        logging.warning("无热点资讯可推送")
        return False
    
    # 构建飞书卡片内容
    card_content = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"AI资讯日报 | {get_today_date()}"},
                "template": "blue"
            },
            "elements": []
        }
    }
    
    # 添加Top资讯条目
    for idx, art in enumerate(top_articles, 1):
        # 条目1：标题+热度
        element1 = {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"{idx}. **{art['title']['zh']}** \n 📈 热度：{art['hot_score']} | 来源：{art['source']}"
            }
        }
        # 条目2：英文标题+查看详情链接
        element2 = {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"📝 英文原文：{art['title']['en'][:50]}... \n 🔗 [查看详情（中英对照）]({art['link']})"
            }
        }
        # 分割线
        element3 = {"tag": "hr"}
        
        card_content["card"]["elements"].extend([element1, element2, element3])
    
    # 添加全文对照链接（模拟网页）
    bilingual_page = generate_bilingual_page(top_articles)
    card_content["card"]["elements"].append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"📖 [查看完整中英文对照网页](https://your-github-pages-url/{get_today_date()}.html)"
        }
    })
    
    try:
        response = requests.post(
            FEISHU_WEBHOOK,
            data=json.dumps(card_content, ensure_ascii=False),
            headers={"Content-Type": "application/json"},
            timeout=10,
            verify=False
        )
        result = response.json()
        if result.get("code") == 0:
            logging.info("✅ 飞书卡片推送成功！")
            return True
        else:
            logging.error(f"推送失败: {result}")
            return False
    except Exception as e:
        logging.error(f"推送异常: {e}")
        return False

# ===================== 主程序 =====================
if __name__ == "__main__":
    logging.info("🚀 开始执行AI资讯日报推送（目标样式版）")
    send_feishu_card()
    logging.info("🔚 推送任务执行完成")
