#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
英文AI资讯聚合推送脚本（GitHub部署版）
功能：多源抓取+中英对照+飞书推送
适配：GitHub Actions定时运行/手动触发
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
import feedparser  # RSS解析库

# ===================== 基础配置 =====================
# 屏蔽不安全请求警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 环境变量读取（GitHub Secrets配置）
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")  # 飞书Webhook
BAIDU_APP_ID = os.getenv("BAIDU_APP_ID")      # 百度翻译APP ID
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY")  # 百度翻译密钥

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
    return text.replace("\n", "").replace("\r", "").replace("  ", "").strip()

def baidu_translate(text, from_lang="en", to_lang="zh"):
    """
    百度翻译API（中英对照）
    :param text: 待翻译文本
    :param from_lang: 源语言（en/zh）
    :param to_lang: 目标语言（zh/en）
    :return: 翻译结果 {en: 原文, zh: 译文}
    """
    # 空文本直接返回
    if not text or len(text) < 2:
        return {"en": text, "zh": text}
    
    # 百度翻译API参数组装
    api_url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
    salt = str(random.randint(32768, 65536))
    sign_str = BAIDU_APP_ID + text + salt + BAIDU_SECRET_KEY
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    
    params = {
        "q": text,
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
            return {
                "en": text if from_lang == "en" else result["trans_result"][0]["dst"],
                "zh": result["trans_result"][0]["dst"] if from_lang == "en" else text
            }
        else:
            logging.warning(f"百度翻译返回异常: {result}")
            return {"en": text, "zh": f"【翻译失败】{text}"}
    except Exception as e:
        logging.error(f"百度翻译调用失败: {str(e)}")
        return {"en": text, "zh": f"【翻译异常】{text}"}

# ===================== 多源抓取函数（英文优先） =====================
def crawl_academic():
    """📚 学术前沿（arXiv CS.AI专栏 - RSS）"""
    try:
        # 随机延迟
        time.sleep(random.uniform(*RANDOM_DELAY))
        # arXiv CS.AI最新论文RSS
        feed = feedparser.parse("http://export.arxiv.org/rss/cs.AI")
        if feed.entries and len(feed.entries) > 0:
            entry = feed.entries[0]  # 最新论文
            title_bi = baidu_translate(clean_text(entry.title))
            summary_bi = baidu_translate(clean_text(entry.summary[:150]))  # 摘要仅取前150字符
            
            return {
                "type": "📚 学术前沿 / Academic Frontier",
                "title": title_bi,
                "summary": summary_bi,
                "link": entry.link,
                "time": get_today_date()
            }
        else:
            return {
                "type": "📚 学术前沿 / Academic Frontier",
                "title": {"en": "No academic updates today", "zh": "今日暂无学术前沿更新"},
                "summary": {"en": "", "zh": ""},
                "link": "",
                "time": ""
            }
    except Exception as e:
        logging.error(f"抓取学术前沿失败: {str(e)}")
        return {
            "type": "📚 学术前沿 / Academic Frontier",
            "title": {"en": "Academic crawl failed", "zh": "学术前沿抓取失败"},
            "summary": {"en": "", "zh": ""},
            "link": "",
            "time": ""
        }

def crawl_official_blog():
    """🏢 官方博客（OpenAI Blog - RSS）"""
    try:
        time.sleep(random.uniform(*RANDOM_DELAY))
        # OpenAI官方博客RSS
        feed = feedparser.parse("https://openai.com/blog/rss/")
        if feed.entries and len(feed.entries) > 0:
            entry = feed.entries[0]
            title_bi = baidu_translate(clean_text(entry.title))
            summary_bi = baidu_translate(clean_text(entry.summary[:150]))
            
            return {
                "type": "🏢 官方博客 / Official Blog",
                "title": title_bi,
                "summary": summary_bi,
                "link": entry.link,
                "time": get_today_date()
            }
        else:
            return {
                "type": "🏢 官方博客 / Official Blog",
                "title": {"en": "No official blog updates today", "zh": "今日暂无官方博客更新"},
                "summary": {"en": "", "zh": ""},
                "link": "",
                "time": ""
            }
    except Exception as e:
        logging.error(f"抓取官方博客失败: {str(e)}")
        return {
            "type": "🏢 官方博客 / Official Blog",
            "title": {"en": "Official blog crawl failed", "zh": "官方博客抓取失败"},
            "summary": {"en": "", "zh": ""},
            "link": "",
            "time": ""
        }

def crawl_community():
    """💬 海外社区（HackerNews AI相关 - API）"""
    try:
        time.sleep(random.uniform(*RANDOM_DELAY))
        # HackerNews Top AI相关帖子
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
                    "type": "💬 海外社区 / Overseas Community",
                    "title": title_bi,
                    "summary": {"en": "Top AI discussion on HackerNews", "zh": "HackerNews热门AI讨论"},
                    "link": link,
                    "time": get_today_date()
                }
        
        return {
            "type": "💬 海外社区 / Overseas Community",
            "title": {"en": "No AI community updates today", "zh": "今日暂无海外社区AI更新"},
            "summary": {"en": "", "zh": ""},
            "link": "",
            "time": ""
        }
    except Exception as e:
        logging.error(f"抓取海外社区失败: {str(e)}")
        return {
            "type": "💬 海外社区 / Overseas Community",
            "title": {"en": "Community crawl failed", "zh": "海外社区抓取失败"},
            "summary": {"en": "", "zh": ""},
            "link": "",
            "time": ""
        }

def crawl_social():
    """📱 社媒聚合（Twitter AI趋势 - Nitter RSS）"""
    try:
        time.sleep(random.uniform(*RANDOM_DELAY))
        # Nitter（Twitter镜像）OpenAI RSS
        feed = feedparser.parse("https://nitter.net/OpenAI/rss")
        if feed.entries and len(feed.entries) > 0:
            entry = feed.entries[0]
            title_bi = baidu_translate(clean_text(entry.title))
            link = entry.link.replace("nitter.net", "twitter.com")  # 替换为原Twitter链接
            
            return {
                "type": "📱 社媒聚合 / Social Media",
                "title": title_bi,
                "summary": {"en": "Latest AI trend on Twitter", "zh": "Twitter最新AI趋势"},
                "link": link,
                "time": get_today_date()
            }
        else:
            return {
                "type": "📱 社媒聚合 / Social Media",
                "title": {"en": "No social media updates today", "zh": "今日暂无社媒AI更新"},
                "summary": {"en": "", "zh": ""},
                "link": "",
                "time": ""
            }
    except Exception as e:
        logging.error(f"抓取社媒聚合失败: {str(e)}")
        return {
            "type": "📱 社媒聚合 / Social Media",
            "title": {"en": "Social media crawl failed", "zh": "社媒聚合抓取失败"},
            "summary": {"en": "", "zh": ""},
            "link": "",
            "time": ""
        }

# ===================== 构建双语推送内容 =====================
def build_feishu_content():
    """构建飞书双语推送内容"""
    # 抓取四类信息
    academic = crawl_academic()
    official_blog = crawl_official_blog()
    community = crawl_community()
    social = crawl_social()
    
    # 组装双语内容
    content = f"📮 Daily AI Digest / 每日AI英文精选（{get_today_date()}）\n\n"
    
    for idx, item in enumerate([academic, official_blog, community, social], 1):
        content += f"{idx}. 【{item['type']}】\n"
        content += f"   English Title: {item['title']['en']}\n"
        content += f"   中文标题：{item['title']['zh']}\n"
        if item['summary']['en']:
            content += f"   English Summary: {item['summary']['en'][:100]}...\n"
            content += f"   中文摘要：{item['summary']['zh'][:100]}...\n"
        if item['link']:
            content += f"   Source Link / 来源链接：{item['link']}\n"
        content += "\n"
    
    return content.strip()

# ===================== 飞书推送 =====================
def send_to_feishu():
    """推送双语内容到飞书"""
    # 校验必要配置
    if not FEISHU_WEBHOOK:
        logging.error("❌ 未配置飞书Webhook！")
        return False
    if not (BAIDU_APP_ID and BAIDU_SECRET_KEY):
        logging.error("❌ 未配置百度翻译API密钥！")
        return False
    
    try:
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": f"Daily AI Digest / 每日AI英文精选（{get_today_date()}）",
                        "content": [[{"tag": "text", "text": build_feishu_content()}]]
                    }
                }
            }
        }
        response = requests.post(
            FEISHU_WEBHOOK,
            data=json.dumps(payload, ensure_ascii=False),
            headers={"Content-Type": "application/json"},
            timeout=10,
            verify=False
        )
        result = response.json()
        if result.get("code") == 0:
            logging.info("✅ 飞书双语推送成功！")
            return True
        else:
            logging.error(f"❌ 推送失败: {result}")
            return False
    except Exception as e:
        logging.error(f"❌ 推送异常: {str(e)}")
        return False

# ===================== 主程序 =====================
if __name__ == "__main__":
    logging.info("🚀 开始执行英文AI资讯聚合推送任务")
    success = send_to_feishu()
    logging.info("🔚 推送任务执行完成" if success else "🔚 推送任务执行失败")
