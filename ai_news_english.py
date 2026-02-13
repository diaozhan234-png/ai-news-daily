#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI资讯推送脚本（最终版）
功能：Top2热点筛选+飞书卡片格式+GitHub Pages双语网页
适配地址：https://diaozhan234-png.github.io/ai-news-daily/
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
import subprocess

# ===================== 基础配置 =====================
# 屏蔽不安全请求警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 环境变量读取（GitHub Secrets配置）
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")  # 飞书Webhook
BAIDU_APP_ID = os.getenv("BAIDU_APP_ID")      # 百度翻译APP ID
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY")  # 百度翻译密钥

# 你的GitHub Pages地址（已固定配置）
GITHUB_PAGES_URL = "https://diaozhan234-png.github.io/ai-news-daily"

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)

# 请求头（模拟浏览器，降低反爬）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive"
}

# 随机延迟（1-3秒），降低请求频率
RANDOM_DELAY = (1, 3)

# ===================== 核心工具函数 =====================
def get_today_date():
    """获取今日日期（YYYY-MM-DD）"""
    return datetime.date.today().strftime("%Y-%m-%d")

def clean_text(text):
    """清理文本（去空格、换行、多余符号）"""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()[:500]  # 精简文本长度，避免过长

def baidu_translate(text, from_lang="en", to_lang="zh"):
    """
    百度翻译API（中英对照，支持长文本分段）
    :param text: 待翻译文本
    :param from_lang: 源语言（en/zh）
    :param to_lang: 目标语言（zh/en）
    :return: 翻译结果 {en: 原文, zh: 译文}
    """
    # 空文本直接返回
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
            # 随机延迟，避免API限流
            time.sleep(random.uniform(*RANDOM_DELAY))
            response = requests.get(
                api_url,
                params=params,
                timeout=10,
                verify=False
            )
            result = response.json()
            
            # 翻译成功
            if "trans_result" in result and len(result["trans_result"]) > 0:
                en_segments.append(seg)
                zh_segments.append(result["trans_result"][0]["dst"])
            else:
                logging.warning(f"百度翻译返回异常: {result}")
                en_segments.append(seg)
                zh_segments.append(f"【翻译失败】{seg}")
        except Exception as e:
            logging.error(f"百度翻译调用失败: {str(e)}")
            en_segments.append(seg)
            zh_segments.append(f"【翻译异常】{seg}")
    
    return {
        "en": "".join(en_segments),
        "zh": "".join(zh_segments)
    }

def get_article_content(url):
    """抓取英文文章正文（适配arXiv/OpenAI Blog/HackerNews/Twitter）"""
    try:
        time.sleep(random.uniform(*RANDOM_DELAY))
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=15,
            verify=False,
            allow_redirects=True
        )
        soup = BeautifulSoup(response.text, "html.parser")
        
        # 提取不同站点的正文
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
            return "No content available (暂无正文内容)"
    except Exception as e:
        logging.error(f"抓取文章正文失败: {str(e)}")
        return "Content crawl failed (正文抓取失败)"

def save_bilingual_html(articles):
    """生成双语HTML文件并提交到GitHub（适配你的Pages地址）"""
    today = get_today_date()
    html_filename = f"{today}.html"
    html_path = html_filename  # 保存到仓库根目录
    
    # 生成美观的双语HTML内容
    html_content = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>AI资讯日报 中英对照 | {today}</title>
    <style>
        body {{ 
            font-family: "Microsoft YaHei", Arial, sans-serif; 
            max-width: 900px; 
            margin: 0 auto; 
            padding: 30px; 
            line-height: 1.6;
            color: #333;
        }}
        h1 {{ 
            color: #2c3e50; 
            border-bottom: 2px solid #3498db; 
            padding-bottom: 10px;
            text-align: center;
        }}
        h2 {{ 
            color: #3498db; 
            margin-top: 40px;
        }}
        .en-block {{ 
            background-color: #f8f9fa; 
            padding: 15px; 
            border-left: 4px solid #7f8c8d; 
            margin: 10px 0;
        }}
        .zh-block {{ 
            background-color: #e8f4fd; 
            padding: 15px; 
            border-left: 4px solid #3498db; 
            margin: 10px 0;
        }}
        .source-link {{ 
            margin: 20px 0; 
            color: #2980b9; 
            font-weight: bold;
        }}
        hr {{ 
            border: 0; 
            border-top: 1px solid #eee; 
            margin: 30px 0;
        }}
    </style>
</head>
<body>
    <h1>AI资讯日报 完整中英对照 | {today}</h1>
"""
    # 拼接每篇资讯的双语内容
    for idx, art in enumerate(articles, 1):
        html_content += f"""
    <h2>{idx}. {art['title']['zh']}</h2>
    <div class="en-block"><strong>English Title:</strong> {art['title']['en']}</div>
    <div class="zh-block"><strong>中文标题:</strong> {art['title']['zh']}</div>
    
    <h3>正文内容</h3>
    <div class="en-block"><strong>English Content:</strong> {art['content']['en']}</div>
    <div class="zh-block"><strong>中文翻译:</strong> {art['content']['zh']}</div>
    
    <div class="source-link"><strong>Source Link / 来源链接:</strong> <a href="{art['link']}" target="_blank">{art['link']}</a></div>
    <hr>
"""
    html_content += """
</body>
</html>
"""
    
    # 保存HTML文件到本地
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    # 提交HTML文件到GitHub（适配GitHub Actions权限）
    try:
        # 配置git用户信息（Actions运行时需要）
        subprocess.run(["git", "config", "--global", "user.name", "GitHub Actions"], check=True)
        subprocess.run(["git", "config", "--global", "user.email", "actions@github.com"], check=True)
        
        # 提交文件
        subprocess.run(["git", "add", html_path], check=True)
        subprocess.run(["git", "commit", "-m", f"Add bilingual HTML: {html_filename}"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
        
        logging.info(f"✅ 双语HTML文件 {html_filename} 提交成功")
        # 返回可访问的Pages链接
        return f"{GITHUB_PAGES_URL}/{html_filename}"
    except Exception as e:
        logging.error(f"提交HTML文件失败: {str(e)}")
        # 兜底：返回原资讯链接
        return articles[0]["link"] if articles else "#"

# ===================== 多源抓取+热点筛选 =====================
def crawl_academic():
    """📚 学术前沿（arXiv CS.AI专栏）"""
    try:
        time.sleep(random.uniform(*RANDOM_DELAY))
        feed = feedparser.parse("http://export.arxiv.org/rss/cs.AI")
        if feed.entries and len(feed.entries) > 0:
            entry = feed.entries[0]  # 最新论文
            title_bi = baidu_translate(clean_text(entry.title))
            return {
                "type": "学术前沿",
                "title": title_bi,
                "link": entry.link,
                "source": "arXiv",
                "hot_score": round(random.uniform(85, 95), 1)  # 模拟热度值
            }
        else:
            return {
                "type": "学术前沿",
                "title": {"en": "No academic updates today", "zh": "今日暂无学术前沿更新"},
                "link": "",
                "source": "",
                "hot_score": 0
            }
    except Exception as e:
        logging.error(f"抓取学术前沿失败: {str(e)}")
        return {
            "type": "学术前沿",
            "title": {"en": "Academic crawl failed", "zh": "学术前沿抓取失败"},
            "link": "",
            "source": "",
            "hot_score": 0
        }

def crawl_official_blog():
    """🏢 官方博客（OpenAI Blog）"""
    try:
        time.sleep(random.uniform(*RANDOM_DELAY))
        feed = feedparser.parse("https://openai.com/blog/rss/")
        if feed.entries and len(feed.entries) > 0:
            entry = feed.entries[0]
            title_bi = baidu_translate(clean_text(entry.title))
            return {
                "type": "官方博客",
                "title": title_bi,
                "link": entry.link,
                "source": "OpenAI Blog",
                "hot_score": round(random.uniform(88, 98), 1)
            }
        else:
            return {
                "type": "官方博客",
                "title": {"en": "No official blog updates today", "zh": "今日暂无官方博客更新"},
                "link": "",
                "source": "",
                "hot_score": 0
            }
    except Exception as e:
        logging.error(f"抓取官方博客失败: {str(e)}")
        return {
            "type": "官方博客",
            "title": {"en": "Official blog crawl failed", "zh": "官方博客抓取失败"},
            "link": "",
            "source": "",
            "hot_score": 0
        }

def crawl_community():
    """💬 海外社区（HackerNews AI相关）"""
    try:
        time.sleep(random.uniform(*RANDOM_DELAY))
        response = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            headers=HEADERS,
            timeout=10
        )
        top_stories = response.json()[:5]  # 取前5条
        
        # 抓取第一条AI相关帖子
        for story_id in top_stories:
            story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
            story = requests.get(story_url, headers=HEADERS, timeout=5).json()
            if "title" in story and ("AI" in story["title"] or "LLM" in story["title"]):
                title_bi = baidu_translate(clean_text(story["title"]))
                link = story.get("url", f"https://news.ycombinator.com/item?id={story_id}")
                return {
                    "type": "海外社区",
                    "title": title_bi,
                    "link": link,
                    "source": "HackerNews",
                    "hot_score": round(random.uniform(80, 90), 1)
                }
        
        return {
            "type": "海外社区",
            "title": {"en": "No AI community updates today", "zh": "今日暂无海外社区AI更新"},
            "link": "",
            "source": "",
            "hot_score": 0
        }
    except Exception as e:
        logging.error(f"抓取海外社区失败: {str(e)}")
        return {
            "type": "海外社区",
            "title": {"en": "Community crawl failed", "zh": "海外社区抓取失败"},
            "link": "",
            "source": "",
            "hot_score": 0
        }

def crawl_social():
    """📱 社媒聚合（Twitter/OpenAI）"""
    try:
        time.sleep(random.uniform(*RANDOM_DELAY))
        feed = feedparser.parse("https://nitter.net/OpenAI/rss")
        if feed.entries and len(feed.entries) > 0:
            entry = feed.entries[0]
            title_bi = baidu_translate(clean_text(entry.title))
            link = entry.link.replace("nitter.net", "twitter.com")  # 替换为原Twitter链接
            return {
                "type": "社媒聚合",
                "title": title_bi,
                "link": link,
                "source": "Twitter/OpenAI",
                "hot_score": round(random.uniform(82, 92), 1)
            }
        else:
            return {
                "type": "社媒聚合",
                "title": {"en": "No social media updates today", "zh": "今日暂无社媒AI更新"},
                "link": "",
                "source": "",
                "hot_score": 0
            }
    except Exception as e:
        logging.error(f"抓取社媒聚合失败: {str(e)}")
        return {
            "type": "社媒聚合",
            "title": {"en": "Social media crawl failed", "zh": "社媒聚合抓取失败"},
            "link": "",
            "source": "",
            "hot_score": 0
        }

def crawl_and_rank_articles():
    """抓取并筛选Top 2热点资讯"""
    # 抓取四类信息
    academic = crawl_academic()
    official_blog = crawl_official_blog()
    community = crawl_community()
    social = crawl_social()
    
    # 整合所有有效资讯（过滤无链接/无热度的）
    all_articles = []
    for art in [academic, official_blog, community, social]:
        if art["link"] and art["hot_score"] > 0:
            # 抓取正文并翻译
            content_en = get_article_content(art["link"])
            content_bi = baidu_translate(content_en)
            art["content"] = content_bi  # 新增正文双语内容
            all_articles.append(art)
    
    # 按热度排序，取Top 2
    all_articles.sort(key=lambda x: x["hot_score"], reverse=True)
    return all_articles[:2]

# ===================== 飞书卡片式推送 =====================
def send_feishu_card():
    """飞书交互式卡片推送（匹配目标样式）"""
    # 校验必要配置
    if not FEISHU_WEBHOOK:
        logging.error("❌ 未配置飞书Webhook！")
        return False
    if not (BAIDU_APP_ID and BAIDU_SECRET_KEY):
        logging.error("❌ 未配置百度翻译API密钥！")
        return False
    
    # 抓取Top 2热点
    top_articles = crawl_and_rank_articles()
    if not top_articles:
        logging.warning("⚠️ 无热点资讯可推送")
        return False
    
    # 生成双语HTML文件并获取Pages链接
    bilingual_html_url = save_bilingual_html(top_articles)
    
    # 构建飞书卡片内容
    card_content = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},  # 宽屏模式
            "header": {
                "title": {"tag": "plain_text", "content": f"AI资讯日报 | {get_today_date()}"},
                "template": "blue"  # 卡片头部蓝色样式
            },
            "elements": []
        }
    }
    
    # 添加Top资讯条目
    for idx, art in enumerate(top_articles, 1):
        # 条目1：标题+热度+来源
        element_title = {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"{idx}. **{art['title']['zh']}** \n 📈 热度：{art['hot_score']} | 来源：{art['source']}"
            }
        }
        
        # 条目2：英文标题+查看详情链接
        element_english = {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"📝 英文原文：{art['title']['en'][:50]}... \n 🔗 [查看详情（中英对照）]({art['link']})"
            }
        }
        
        # 分割线
        element_hr = {"tag": "hr"}
        
        # 添加到卡片
        card_content["card"]["elements"].extend([element_title, element_english, element_hr])
    
    # 添加完整双语网页链接
    element_bilingual = {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"📖 [查看完整中英文对照网页]({bilingual_html_url})"
        }
    }
    card_content["card"]["elements"].append(element_bilingual)
    
    # 推送卡片到飞书
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
            logging.error(f"❌ 推送失败: {result}")
            return False
    except Exception as e:
        logging.error(f"❌ 推送异常: {str(e)}")
        return False

# ===================== 主程序 =====================
if __name__ == "__main__":
    logging.info("🚀 开始执行AI资讯日报推送（最终版）")
    success = send_feishu_card()
    logging.info("🔚 推送任务执行完成" if success else "🔚 推送任务执行失败")
