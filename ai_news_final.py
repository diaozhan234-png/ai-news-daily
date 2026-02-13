#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日AI精选资讯推送脚本（GitHub Actions适配版）
核心：无成本云端部署，每日9:30（北京时间）自动推送到飞书
"""
import requests
import json
import os
import datetime
import time
from bs4 import BeautifulSoup
import logging
import urllib3

# ===================== 基础配置 =====================
# 屏蔽InsecureRequestWarning警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 从环境变量读取敏感信息（GitHub Secrets配置）
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")  # 飞书Webhook
BAIDU_TRANS_APPID = os.getenv("BAIDU_TRANS_APPID")  # 百度翻译APPID（可选）
BAIDU_TRANS_KEY = os.getenv("BAIDU_TRANS_KEY")  # 百度翻译密钥（可选）

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)

# 请求头（防反爬）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ===================== 工具函数 =====================
def get_today_date():
    """获取今日日期（YYYY-MM-DD）"""
    return datetime.date.today().strftime("%Y-%m-%d")

def baidu_translate(text, from_lang="en", to_lang="zh"):
    """百度翻译API（可选，无配置则返回原文）"""
    if not BAIDU_TRANS_APPID or not BAIDU_TRANS_KEY:
        return text
    
    try:
        import hashlib
        salt = str(int(time.time()))
        sign_str = BAIDU_TRANS_APPID + text + salt + BAIDU_TRANS_KEY
        sign = hashlib.md5(sign_str.encode()).hexdigest()
        
        url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
        params = {
            "q": text,
            "from": from_lang,
            "to": to_lang,
            "appid": BAIDU_TRANS_APPID,
            "salt": salt,
            "sign": sign
        }
        response = requests.get(url, params=params, timeout=10, verify=False)
        result = response.json()
        if "trans_result" in result:
            return result["trans_result"][0]["dst"]
        return text
    except Exception as e:
        logging.error(f"翻译失败: {e}")
        return text

def clean_text(text):
    """清理文本（去空格、换行）"""
    return text.replace("\n", "").replace("\r", "").strip()

# ===================== 核心抓取函数（多源头+单条输出） =====================
def crawl_basic_llm():
    """🤖 基础大模型 / 多模态（主：新智元，备：机器之心）"""
    # 主源：新智元
    try:
        url = "https://www.xinzhiyuan.com/"
        response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(response.text, "html.parser")
        
        # 适配新智元页面结构
        articles = soup.find_all("div", class_="article-item", limit=1)
        if not articles:
            articles = soup.find_all("a", class_="title", limit=1)
        if articles:
            if hasattr(articles[0], "text"):
                title = clean_text(articles[0].text)
                link = articles[0]["href"] if "href" in articles[0].attrs else url
                if not link.startswith("http"):
                    link = "https://www.xinzhiyuan.com" + link
                return {
                    "type": "🤖 基础大模型 / 多模态",
                    "title_zh": title,
                    "summary_zh": f"最新动态：{title[:50]}...",
                    "link": link,
                    "time": get_today_date()
                }
    except Exception as e:
        logging.error(f"抓取新智元失败: {e}")
    
    # 备用源：机器之心
    try:
        url = "https://www.jiqizhixin.com/"
        response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(response.text, "html.parser")
        
        articles = soup.find_all("div", class_="article-item", limit=1)
        if not articles:
            articles = soup.find_all("a", class_="article-title", limit=1)
        if articles:
            title = clean_text(articles[0].text)
            link = articles[0]["href"] if "href" in articles[0].attrs else url
            if not link.startswith("http"):
                link = "https://www.jiqizhixin.com" + link
            return {
                "type": "🤖 基础大模型 / 多模态",
                "title_zh": title,
                "summary_zh": f"最新动态：{title[:50]}...",
                "link": link,
                "time": get_today_date()
            }
    except Exception as e:
        logging.error(f"抓取机器之心失败: {e}")
    
    # 无内容提示（仅文字）
    return {
        "type": "🤖 基础大模型 / 多模态",
        "title_zh": "今日暂无【基础大模型/多模态】相关信息",
        "summary_zh": "",
        "link": "",
        "time": ""
    }

def crawl_industry_dynamic():
    """🏢 AI 行业动态 / 应用创新（主：晚点，备：新智元）"""
    # 主源：晚点LatePost
    try:
        url = "https://www.latepost.com/"
        response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(response.text, "html.parser")
        
        articles = soup.find_all("article", class_="post-item", limit=1)
        if not articles:
            articles = soup.find_all("a", class_="post-title", limit=1)
        if articles:
            title = clean_text(articles[0].text)
            link = articles[0]["href"] if "href" in articles[0].attrs else url
            if not link.startswith("http"):
                link = "https://www.latepost.com" + link
            return {
                "type": "🏢 AI 行业动态 / 应用创新",
                "title_zh": title,
                "summary_zh": f"最新动态：{title[:50]}...",
                "link": link,
                "time": get_today_date()
            }
    except Exception as e:
        logging.error(f"抓取晚点失败: {e}")
    
    # 备用源：新智元
    try:
        url = "https://www.xinzhiyuan.com/"
        response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(response.text, "html.parser")
        
        articles = soup.find_all("div", class_="article-item", limit=1)
        if articles:
            title = clean_text(articles[0].text)
            link = articles[0]["href"] if "href" in articles[0].attrs else url
            if not link.startswith("http"):
                link = "https://www.xinzhiyuan.com" + link
            return {
                "type": "🏢 AI 行业动态 / 应用创新",
                "title_zh": title,
                "summary_zh": f"最新动态：{title[:50]}...",
                "link": link,
                "time": get_today_date()
            }
    except Exception as e:
        logging.error(f"抓取新智元（备用）失败: {e}")
    
    # 无内容提示
    return {
        "type": "🏢 AI 行业动态 / 应用创新",
        "title_zh": "今日暂无【AI行业动态/应用创新】相关信息",
        "summary_zh": "",
        "link": "",
        "time": ""
    }

def crawl_ai_tech():
    """🔧 AI 技术 / Agent（主：InfoQ，备：机器之心）"""
    # 主源：InfoQ
    try:
        url = "https://www.infoq.cn/topic/ai"
        response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(response.text, "html.parser")
        
        articles = soup.find_all("div", class_="article-item", limit=1)
        if not articles:
            articles = soup.find_all("a", class_="article-title", limit=1)
        if articles:
            title = clean_text(articles[0].text)
            link = articles[0]["href"] if "href" in articles[0].attrs else url
            if not link.startswith("http"):
                link = "https://www.infoq.cn" + link
            return {
                "type": "🔧 AI 技术 / Agent",
                "title_zh": title,
                "summary_zh": f"最新动态：{title[:50]}...",
                "link": link,
                "time": get_today_date()
            }
    except Exception as e:
        logging.error(f"抓取InfoQ失败: {e}")
    
    # 备用源：机器之心
    try:
        url = "https://www.jiqizhixin.com/"
        response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(response.text, "html.parser")
        
        articles = soup.find_all("a", class_="article-title", limit=1)
        if articles:
            title = clean_text(articles[0].text)
            link = articles[0]["href"] if "href" in articles[0].attrs else url
            if not link.startswith("http"):
                link = "https://www.jiqizhixin.com" + link
            return {
                "type": "🔧 AI 技术 / Agent",
                "title_zh": title,
                "summary_zh": f"最新动态：{title[:50]}...",
                "link": link,
                "time": get_today_date()
            }
    except Exception as e:
        logging.error(f"抓取机器之心（备用）失败: {e}")
    
    # 无内容提示
    return {
        "type": "🔧 AI 技术 / Agent",
        "title_zh": "今日暂无【AI技术/Agent】相关信息",
        "summary_zh": "",
        "link": "",
        "time": ""
    }

def crawl_llm_ranking():
    """📊 大模型排行榜 / 技术前沿（主：机器之心，备：InfoQ）"""
    # 主源：机器之心
    try:
        url = "https://www.jiqizhixin.com/"
        response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(response.text, "html.parser")
        
        articles = soup.find_all("div", class_="article-item", limit=1)
        if not articles:
            articles = soup.find_all("a", class_="article-title", limit=1)
        if articles:
            title = clean_text(articles[0].text)
            link = articles[0]["href"] if "href" in articles[0].attrs else url
            if not link.startswith("http"):
                link = "https://www.jiqizhixin.com" + link
            return {
                "type": "📊 大模型排行榜 / 技术前沿",
                "title_zh": title,
                "summary_zh": f"最新动态：{title[:50]}...",
                "link": link,
                "time": get_today_date()
            }
    except Exception as e:
        logging.error(f"抓取机器之心失败: {e}")
    
    # 备用源：InfoQ
    try:
        url = "https://www.infoq.cn/topic/ai"
        response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(response.text, "html.parser")
        
        articles = soup.find_all("a", class_="article-title", limit=1)
        if articles:
            title = clean_text(articles[0].text)
            link = articles[0]["href"] if "href" in articles[0].attrs else url
            if not link.startswith("http"):
                link = "https://www.infoq.cn" + link
            return {
                "type": "📊 大模型排行榜 / 技术前沿",
                "title_zh": title,
                "summary_zh": f"最新动态：{title[:50]}...",
                "link": link,
                "time": get_today_date()
            }
    except Exception as e:
        logging.error(f"抓取InfoQ（备用）失败: {e}")
    
    # 无内容提示
    return {
        "type": "📊 大模型排行榜 / 技术前沿",
        "title_zh": "今日暂无【大模型排行榜/技术前沿】相关信息",
        "summary_zh": "",
        "link": "",
        "time": ""
    }

def crawl_ai_innovation():
    """🚀 AI 应用创新 / 行业趋势（主：知潜，备：晚点）"""
    # 主源：知潜KnowFuture
    try:
        url = "https://www.knowfuture.cn/"
        response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(response.text, "html.parser")
        
        articles = soup.find_all("div", class_="article-item", limit=1)
        if not articles:
            articles = soup.find_all("a", class_="title", limit=1)
        if articles:
            title = clean_text(articles[0].text)
            link = articles[0]["href"] if "href" in articles[0].attrs else url
            if not link.startswith("http"):
                link = "https://www.knowfuture.cn" + link
            return {
                "type": "🚀 AI 应用创新 / 行业趋势",
                "title_zh": title,
                "summary_zh": f"最新动态：{title[:50]}...",
                "link": link,
                "time": get_today_date()
            }
    except Exception as e:
        logging.error(f"抓取知潜失败: {e}")
    
    # 备用源：晚点LatePost
    try:
        url = "https://www.latepost.com/"
        response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(response.text, "html.parser")
        
        articles = soup.find_all("a", class_="post-title", limit=1)
        if articles:
            title = clean_text(articles[0].text)
            link = articles[0]["href"] if "href" in articles[0].attrs else url
            if not link.startswith("http"):
                link = "https://www.latepost.com" + link
            return {
                "type": "🚀 AI 应用创新 / 行业趋势",
                "title_zh": title,
                "summary_zh": f"最新动态：{title[:50]}...",
                "link": link,
                "time": get_today_date()
            }
    except Exception as e:
        logging.error(f"抓取晚点（备用）失败: {e}")
    
    # 无内容提示
    return {
        "type": "🚀 AI 应用创新 / 行业趋势",
        "title_zh": "今日暂无【AI应用创新/行业趋势】相关信息",
        "summary_zh": "",
        "link": "",
        "time": ""
    }

# ===================== 构建推送内容 =====================
def build_feishu_content():
    """构建飞书推送内容"""
    # 抓取5类信息
    basic_llm = crawl_basic_llm()
    industry_dynamic = crawl_industry_dynamic()
    ai_tech = crawl_ai_tech()
    llm_ranking = crawl_llm_ranking()
    ai_innovation = crawl_ai_innovation()
    
    # 组装内容
    content = f"📮 每日AI精选（{get_today_date()}）\n\n"
    
    # 遍历5类信息
    for idx, item in enumerate([basic_llm, industry_dynamic, ai_tech, llm_ranking, ai_innovation], 1):
        content += f"{idx}. 【{item['type']}】\n"
        content += f"   标题：{item['title_zh']}\n"
        if item["summary_zh"]:
            content += f"   摘要：{item['summary_zh']}\n"
        if item["link"]:
            content += f"   来源链接：{item['link']}\n"
        content += "\n"
    
    return content.strip()

# ===================== 飞书推送 =====================
def send_to_feishu():
    """推送内容到飞书"""
    if not FEISHU_WEBHOOK:
        logging.error("未配置飞书Webhook！")
        return False
    
    try:
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": f"每日AI精选（{get_today_date()}）",
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
            logging.info("✅ 飞书推送成功！")
            return True
        else:
            logging.error(f"❌ 推送失败: {result}")
            return False
    except Exception as e:
        logging.error(f"❌ 推送异常: {e}")
        return False

# ===================== 主程序 =====================
if __name__ == "__main__":
    logging.info("🚀 开始执行每日AI资讯推送任务")
    send_to_feishu()
    logging.info("🔚 推送任务执行完成")
