#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日AI精选资讯推送脚本（GitHub Actions适配版）
核心：通用抓取逻辑，适配各网站最新页面结构，稳定获取有效内容+链接
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

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)

# 请求头（防反爬，模拟浏览器）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive"
}

# ===================== 工具函数 =====================
def get_today_date():
    """获取今日日期（YYYY-MM-DD）"""
    return datetime.date.today().strftime("%Y-%m-%d")

def clean_text(text):
    """清理文本（去空格、换行、多余符号）"""
    if not text:
        return ""
    return text.replace("\n", "").replace("\r", "").replace("  ", "").strip()

def get_valid_article(url, domain, href_keywords, title_min_len=5):
    """
    通用文章抓取函数（适配所有网站）
    :param url: 目标网址
    :param domain: 网站域名（如https://www.xinzhiyuan.com）
    :param href_keywords: 文章链接包含的关键词（如["/articles/", "/post/"]）
    :param title_min_len: 标题最小长度（过滤无效链接）
    :return: 有效文章{title, link}或None
    """
    try:
        # 发送请求（添加超时和重试）
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=15,
            verify=False,
            allow_redirects=True
        )
        response.raise_for_status()  # 抛出HTTP错误
        soup = BeautifulSoup(response.text, "html.parser")
        
        # 遍历所有a标签，找符合条件的文章链接
        all_links = soup.find_all("a", href=True)
        for a in all_links:
            href = a["href"]
            title = clean_text(a.text)
            
            # 过滤条件：链接含关键词 + 标题长度达标 + 标题非空
            if any(keyword in href for keyword in href_keywords) and len(title) >= title_min_len and title:
                # 补全相对链接为绝对链接
                if not href.startswith("http"):
                    href = domain + href if href.startswith("/") else domain + "/" + href
                return {"title": title, "link": href}
        
        logging.warning(f"{domain} 未找到符合条件的文章链接")
        return None
    except Exception as e:
        logging.error(f"抓取 {domain} 失败: {str(e)}")
        return None

# ===================== 核心抓取函数（适配最新页面） =====================
def crawl_basic_llm():
    """🤖 基础大模型 / 多模态（主：新智元，备：机器之心）"""
    # 主源：新智元
    xinzhiyuan = get_valid_article(
        url="https://www.xinzhiyuan.com/",
        domain="https://www.xinzhiyuan.com",
        href_keywords=["/articles/", "/detail/", "/news/"]
    )
    if xinzhiyuan:
        return {
            "type": "🤖 基础大模型 / 多模态",
            "title_zh": xinzhiyuan["title"],
            "summary_zh": f"最新动态：{xinzhiyuan['title'][:50]}...",
            "link": xinzhiyuan["link"],
            "time": get_today_date()
        }
    
    # 备用源：机器之心
    jiqizhixin = get_valid_article(
        url="https://www.jiqizhixin.com/",
        domain="https://www.jiqizhixin.com",
        href_keywords=["/articles/", "/detail/", "/news/"]
    )
    if jiqizhixin:
        return {
            "type": "🤖 基础大模型 / 多模态",
            "title_zh": jiqizhixin["title"],
            "summary_zh": f"最新动态：{jiqizhixin['title'][:50]}...",
            "link": jiqizhixin["link"],
            "time": get_today_date()
        }
    
    # 无内容提示（仅文字）
    return {
        "type": "🤖 基础大模型 / 多模态",
        "title_zh": "今日暂无【基础大模型/多模态】相关信息",
        "summary_zh": "",
        "link": "",
        "time": ""
    }

def crawl_industry_dynamic():
    """🏢 AI 行业动态 / 应用创新（主：晚点LatePost，备：新智元）"""
    # 主源：晚点LatePost
    latepost = get_valid_article(
        url="https://www.latepost.com/",
        domain="https://www.latepost.com",
        href_keywords=["/post/", "/article/", "/detail/"]
    )
    if latepost:
        return {
            "type": "🏢 AI 行业动态 / 应用创新",
            "title_zh": latepost["title"],
            "summary_zh": f"最新动态：{latepost['title'][:50]}...",
            "link": latepost["link"],
            "time": get_today_date()
        }
    
    # 备用源：新智元
    xinzhiyuan = get_valid_article(
        url="https://www.xinzhiyuan.com/",
        domain="https://www.xinzhiyuan.com",
        href_keywords=["/articles/", "/detail/", "/news/"]
    )
    if xinzhiyuan:
        return {
            "type": "🏢 AI 行业动态 / 应用创新",
            "title_zh": xinzhiyuan["title"],
            "summary_zh": f"最新动态：{xinzhiyuan['title'][:50]}...",
            "link": xinzhiyuan["link"],
            "time": get_today_date()
        }
    
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
    # 主源：InfoQ AI专栏
    infoq = get_valid_article(
        url="https://www.infoq.cn/topic/ai",
        domain="https://www.infoq.cn",
        href_keywords=["/article/", "/detail/", "/news/"]
    )
    if infoq:
        return {
            "type": "🔧 AI 技术 / Agent",
            "title_zh": infoq["title"],
            "summary_zh": f"最新动态：{infoq['title'][:50]}...",
            "link": infoq["link"],
            "time": get_today_date()
        }
    
    # 备用源：机器之心
    jiqizhixin = get_valid_article(
        url="https://www.jiqizhixin.com/",
        domain="https://www.jiqizhixin.com",
        href_keywords=["/articles/", "/detail/", "/tech/"]
    )
    if jiqizhixin:
        return {
            "type": "🔧 AI 技术 / Agent",
            "title_zh": jiqizhixin["title"],
            "summary_zh": f"最新动态：{jiqizhixin['title'][:50]}...",
            "link": jiqizhixin["link"],
            "time": get_today_date()
        }
    
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
    jiqizhixin = get_valid_article(
        url="https://www.jiqizhixin.com/",
        domain="https://www.jiqizhixin.com",
        href_keywords=["/articles/", "/rank/", "/paper/", "/tech/"]
    )
    if jiqizhixin:
        return {
            "type": "📊 大模型排行榜 / 技术前沿",
            "title_zh": jiqizhixin["title"],
            "summary_zh": f"最新动态：{jiqizhixin['title'][:50]}...",
            "link": jiqizhixin["link"],
            "time": get_today_date()
        }
    
    # 备用源：InfoQ
    infoq = get_valid_article(
        url="https://www.infoq.cn/topic/ai",
        domain="https://www.infoq.cn",
        href_keywords=["/article/", "/detail/", "/research/"]
    )
    if infoq:
        return {
            "type": "📊 大模型排行榜 / 技术前沿",
            "title_zh": infoq["title"],
            "summary_zh": f"最新动态：{infoq['title'][:50]}...",
            "link": infoq["link"],
            "time": get_today_date()
        }
    
    # 无内容提示
    return {
        "type": "📊 大模型排行榜 / 技术前沿",
        "title_zh": "今日暂无【大模型排行榜/技术前沿】相关信息",
        "summary_zh": "",
        "link": "",
        "time": ""
    }

def crawl_ai_innovation():
    """🚀 AI 应用创新 / 行业趋势（主：知潜KnowFuture，备：晚点LatePost）"""
    # 主源：知潜KnowFuture
    zhiqian = get_valid_article(
        url="https://www.knowfuture.cn/",
        domain="https://www.knowfuture.cn",
        href_keywords=["/articles/", "/post/", "/detail/", "/trend/"]
    )
    if zhiqian:
        return {
            "type": "🚀 AI 应用创新 / 行业趋势",
            "title_zh": zhiqian["title"],
            "summary_zh": f"最新动态：{zhiqian['title'][:50]}...",
            "link": zhiqian["link"],
            "time": get_today_date()
        }
    
    # 备用源：晚点LatePost
    latepost = get_valid_article(
        url="https://www.latepost.com/",
        domain="https://www.latepost.com",
        href_keywords=["/post/", "/article/", "/case/"]
    )
    if latepost:
        return {
            "type": "🚀 AI 应用创新 / 行业趋势",
            "title_zh": latepost["title"],
            "summary_zh": f"最新动态：{latepost['title'][:50]}...",
            "link": latepost["link"],
            "time": get_today_date()
        }
    
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
        logging.error("❌ 未配置飞书Webhook！")
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
        logging.error(f"❌ 推送异常: {str(e)}")
        return False

# ===================== 主程序 =====================
if __name__ == "__main__":
    logging.info("🚀 开始执行每日AI资讯推送任务")
    send_to_feishu()
    logging.info("🔚 推送任务执行完成")
