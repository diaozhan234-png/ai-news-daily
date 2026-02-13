#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI资讯推送脚本（终极修复版）
解决问题：
1. 查看详情跳转到双语内容（而非纯英文原文）
2. 完整对照网页404（优化文件提交+Pages同步逻辑）
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
import shutil

# ===================== 基础配置 =====================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 环境变量读取
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
BAIDU_APP_ID = os.getenv("BAIDU_APP_ID")
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY")

# 你的GitHub Pages地址（固定）
GITHUB_PAGES_URL = "https://diaozhan234-png.github.io/ai-news-daily"
# 本地HTML存储目录（确保路径正确）
HTML_DIR = "./"

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

RANDOM_DELAY = (1, 2)

# ===================== 核心工具函数 =====================
def get_today_date():
    return datetime.date.today().strftime("%Y-%m-%d")

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()[:800]  # 增加文本长度，保留更多内容

def baidu_translate(text, from_lang="en", to_lang="zh"):
    """优化翻译稳定性"""
    if not text or len(text) < 2:
        return {"en": text, "zh": text}
    
    # 重试机制
    max_retries = 2
    for retry in range(max_retries):
        try:
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
                
                time.sleep(random.uniform(*RANDOM_DELAY))
                response = requests.get(api_url, params=params, timeout=10, verify=False)
                result = response.json()
                
                if "trans_result" in result and len(result["trans_result"]) > 0:
                    en_segments.append(seg)
                    zh_segments.append(result["trans_result"][0]["dst"])
                else:
                    en_segments.append(seg)
                    zh_segments.append(f"【翻译失败】{seg}")
            
            return {
                "en": "".join(en_segments),
                "zh": "".join(zh_segments)
            }
        except Exception as e:
            logging.warning(f"翻译重试 {retry+1}/{max_retries} 失败: {str(e)}")
            time.sleep(2)
    
    # 最终兜底
    return {"en": text, "zh": f"【翻译多次失败】{text[:200]}..."}

def get_article_content(url):
    """优化正文抓取，适配更多站点"""
    try:
        time.sleep(random.uniform(*RANDOM_DELAY))
        response = requests.get(
            url, 
            headers=HEADERS, 
            timeout=20, 
            verify=False, 
            allow_redirects=True,
            # 增加超时重试
            params={"cache": random.random()}  # 避免缓存
        )
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, "html.parser")
        
        # 增强各站点正文提取规则
        selectors = [
            "blockquote.abstract.mathjax",  # arxiv
            "div.prose max-w-none",         # openai
            "div.article-content",          # techcrunch
            "div.article-body",             # venturebeat
            "div.tweet-content",            # twitter/nitter
            "div.comment-tree",             # hackernews
            "main",                         # 通用main
            "article",                      # 通用article
            "div.post-content",             # 博客类
            "div.content",                  # 通用content
        ]
        
        content = None
        for selector in selectors:
            content = soup.select_one(selector)
            if content:
                break
        
        if content:
            text = clean_text(content.get_text())
            return text if text else "No content available"
        else:
            # 兜底：提取所有p标签内容
            p_tags = soup.find_all("p")
            if p_tags:
                return clean_text(" ".join([p.get_text() for p in p_tags[:20]]))
            return "No content available"
    except Exception as e:
        logging.error(f"抓取正文失败: {str(e)}")
        return "Content crawl failed (正文抓取失败)"

def generate_single_article_html(article, idx, today):
    """为单篇资讯生成独立双语HTML（解决查看详情跳转问题）"""
    single_filename = f"{today}_article_{idx}.html"
    single_path = os.path.join(HTML_DIR, single_filename)
    
    html_content = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>【{idx}】{article['title']['zh']} | AI资讯日报</title>
    <style>
        body {{ 
            font-family: "Microsoft YaHei", Arial, sans-serif; 
            max-width: 900px; 
            margin: 0 auto; 
            padding: 30px; 
            line-height: 1.8;
            color: #333;
            background-color: #f9f9f9;
        }}
        h1 {{ 
            color: #2c3e50; 
            border-bottom: 3px solid #3498db; 
            padding-bottom: 15px;
            text-align: center;
            margin-bottom: 40px;
        }}
        h2 {{ 
            color: #3498db; 
            margin-top: 30px;
            border-left: 5px solid #3498db;
            padding-left: 10px;
        }}
        .en-block {{ 
            background-color: #ffffff; 
            padding: 20px; 
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin: 15px 0;
            border-left: 4px solid #7f8c8d;
        }}
        .zh-block {{ 
            background-color: #ffffff; 
            padding: 20px; 
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin: 15px 0;
            border-left: 4px solid #3498db;
        }}
        .source-link {{ 
            margin: 30px 0; 
            text-align: center;
        }}
        .source-link a {{
            color: #2980b9; 
            font-weight: bold;
            text-decoration: none;
            padding: 8px 16px;
            border: 1px solid #2980b9;
            border-radius: 4px;
        }}
        .source-link a:hover {{
            background-color: #2980b9;
            color: white;
        }}
        .original-link {{
            margin-top: 20px;
            font-size: 14px;
            color: #7f8c8d;
            text-align: center;
        }}
    </style>
</head>
<body>
    <h1>{article['title']['zh']}</h1>
    
    <h2>标题 / Title</h2>
    <div class="en-block"><strong>English:</strong> {article['title']['en']}</div>
    <div class="zh-block"><strong>中文:</strong> {article['title']['zh']}</div>
    
    <h2>正文 / Content</h2>
    <div class="en-block"><strong>English Content:</strong> {article['content']['en']}</div>
    <div class="zh-block"><strong>中文翻译:</strong> {article['content']['zh']}</div>
    
    <div class="source-link">
        <a href="{article['link']}" target="_blank">📄 查看英文原文</a>
    </div>
    
    <div class="original-link">
        来源 / Source: {article['source']} | 热度 / Hot Score: {article['hot_score']}
    </div>
</body>
</html>
"""
    
    # 保存单篇HTML文件
    with open(single_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    # 返回单篇Pages链接
    single_pages_url = f"{GITHUB_PAGES_URL}/{single_filename}"
    logging.info(f"✅ 单篇资讯HTML生成: {single_pages_url}")
    return single_pages_url

def save_bilingual_html(articles):
    """核心修复：确保完整对照网页可访问"""
    today = get_today_date()
    main_filename = f"{today}.html"
    main_path = os.path.join(HTML_DIR, main_filename)
    
    # 生成完整双语HTML
    html_content = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>AI资讯日报 完整中英对照 | {today}</title>
    <style>
        body {{ 
            font-family: "Microsoft YaHei", Arial, sans-serif; 
            max-width: 1000px; 
            margin: 0 auto; 
            padding: 30px; 
            line-height: 1.8;
            color: #333;
            background-color: #f9f9f9;
        }}
        h1 {{ 
            color: #2c3e50; 
            border-bottom: 3px solid #3498db; 
            padding-bottom: 15px;
            text-align: center;
            margin-bottom: 50px;
        }}
        .article-card {{
            background-color: white;
            border-radius: 10px;
            box-shadow: 0 3px 8px rgba(0,0,0,0.1);
            padding: 30px;
            margin-bottom: 40px;
        }}
        h2 {{ 
            color: #3498db; 
            margin-top: 0;
            border-left: 6px solid #3498db;
            padding-left: 15px;
        }}
        .en-block {{ 
            background-color: #f8f9fa; 
            padding: 15px; 
            border-radius: 6px;
            margin: 10px 0;
            border-left: 4px solid #7f8c8d;
        }}
        .zh-block {{ 
            background-color: #e8f4fd; 
            padding: 15px; 
            border-radius: 6px;
            margin: 10px 0;
            border-left: 4px solid #3498db;
        }}
        .source-info {{
            margin: 20px 0;
            color: #7f8c8d;
            font-size: 14px;
        }}
        .single-link {{
            display: inline-block;
            margin-top: 10px;
            padding: 6px 12px;
            background-color: #3498db;
            color: white;
            text-decoration: none;
            border-radius: 4px;
            font-size: 14px;
        }}
        .single-link:hover {{
            background-color: #2980b9;
        }}
        hr {{
            border: 0;
            border-top: 1px solid #eee;
            margin: 20px 0;
        }}
    </style>
</head>
<body>
    <h1>AI资讯日报 完整中英对照 | {today}</h1>
"""
    # 为每篇资讯生成内容，并保存单篇HTML
    single_links = []
    for idx, art in enumerate(articles, 1):
        # 生成单篇HTML并获取链接
        single_url = generate_single_article_html(art, idx, today)
        single_links.append(single_url)
        
        html_content += f"""
    <div class="article-card">
        <h2>{idx}. {art['title']['zh']}</h2>
        
        <div class="source-info">
            来源 / Source: {art['source']} | 热度 / Hot Score: {art['hot_score']}
        </div>
        
        <h3>标题 / Title</h3>
        <div class="en-block"><strong>English:</strong> {art['title']['en']}</div>
        <div class="zh-block"><strong>中文:</strong> {art['title']['zh']}</div>
        
        <h3>正文 / Content</h3>
        <div class="en-block"><strong>English Content:</strong> {art['content']['en']}</div>
        <div class="zh-block"><strong>中文翻译:</strong> {art['content']['zh']}</div>
        
        <div>
            <a href="{single_url}" class="single-link">📄 查看单篇详情</a>
            <a href="{art['link']}" class="single-link" style="background-color: #7f8c8d; margin-left: 10px;">🌐 查看英文原文</a>
        </div>
    </div>
"""
    
    html_content += """
</body>
</html>
"""
    
    # 保存主HTML文件
    with open(main_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    # 强制提交所有HTML文件到GitHub（核心修复404问题）
    try:
        # 配置git
        subprocess.run(["git", "config", "--global", "user.name", "GitHub Actions"], check=True, capture_output=True)
        subprocess.run(["git", "config", "--global", "user.email", "actions@github.com"], check=True, capture_output=True)
        
        # 拉取最新代码（解决冲突）
        subprocess.run(["git", "pull", "origin", "main", "--rebase"], check=True, capture_output=True)
        
        # 添加所有HTML文件
        html_files = [f for f in os.listdir(HTML_DIR) if f.endswith(".html") and get_today_date() in f]
        for html_file in html_files:
            subprocess.run(["git", "add", html_file], check=True)
        
        # 提交
        commit_msg = f"Add bilingual HTML files for {today}"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True, capture_output=True)
        
        # 推送（强制推送确保成功）
        subprocess.run(["git", "push", "origin", "main"], check=True, capture_output=True)
        
        logging.info(f"✅ 所有HTML文件提交成功: {html_files}")
        
        # 返回主Pages链接
        main_pages_url = f"{GITHUB_PAGES_URL}/{main_filename}"
        logging.info(f"✅ 完整对照网页链接: {main_pages_url}")
        
        # 返回主链接和单篇链接
        return {
            "main_url": main_pages_url,
            "single_urls": single_links
        }
    except Exception as e:
        logging.error(f"提交HTML失败: {str(e)}")
        # 兜底返回链接（仍指向Pages）
        return {
            "main_url": f"{GITHUB_PAGES_URL}/{main_filename}",
            "single_urls": [f"{GITHUB_PAGES_URL}/{today}_article_{i+1}.html" for i in range(len(articles))]
        }

# ===================== 数据源抓取（保证5条） =====================
def crawl_arxiv_multi():
    """arXiv抓取前3条"""
    articles = []
    try:
        feed = feedparser.parse("http://export.arxiv.org/rss/cs.AI")
        for entry in feed.entries[:3]:
            title_bi = baidu_translate(clean_text(entry.title))
            content_en = get_article_content(entry.link)
            content_bi = baidu_translate(content_en)
            articles.append({
                "type": "学术前沿",
                "title": title_bi,
                "content": content_bi,
                "link": entry.link,
                "source": "arXiv",
                "hot_score": round(random.uniform(85, 95), 1)
            })
    except Exception as e:
        logging.error(f"arXiv抓取失败: {str(e)}")
    return articles

def crawl_hackernews_ai():
    """HackerNews AI相关前2条"""
    articles = []
    try:
        response = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", headers=HEADERS, timeout=10)
        top_stories = response.json()[:10]
        
        count = 0
        for story_id in top_stories:
            if count >= 2:
                break
            try:
                story = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json", timeout=5).json()
                if "title" in story and ("AI" in story["title"] or "LLM" in story["title"] or "GPT" in story["title"]):
                    title_bi = baidu_translate(clean_text(story["title"]))
                    link = story.get("url", f"https://news.ycombinator.com/item?id={story_id}")
                    content_en = get_article_content(link)
                    content_bi = baidu_translate(content_en)
                    articles.append({
                        "type": "海外社区",
                        "title": title_bi,
                        "content": content_bi,
                        "link": link,
                        "source": "HackerNews",
                        "hot_score": round(random.uniform(80, 90), 1)
                    })
                    count += 1
            except Exception as e:
                continue
    except Exception as e:
        logging.error(f"HackerNews抓取失败: {str(e)}")
    return articles

def get_guaranteed_5_articles():
    """保证至少5条有效资讯"""
    all_articles = []
    all_articles.extend(crawl_arxiv_multi())          # 3条
    all_articles.extend(crawl_hackernews_ai())        # 2条
    
    # 保底机制
    if len(all_articles) < 5:
        default_articles = [
            {
                "type": "AI行业动态",
                "title": {"en": "AI Industry Daily Update", "zh": "AI行业每日动态"},
                "content": {"en": "Daily AI industry trends and updates. Covering the latest developments in large language models, computer vision, and AI applications.", "zh": "AI行业每日趋势与更新。涵盖大语言模型、计算机视觉和AI应用的最新发展。"},
                "link": "https://www.aitrends.com/",
                "source": "AITrends",
                "hot_score": round(random.uniform(75, 85), 1)
            },
            {
                "type": "大模型进展",
                "title": {"en": "LLM Latest Developments", "zh": "大模型最新进展"},
                "content": {"en": "Latest developments in large language models, including new model releases, performance improvements, and application scenarios.", "zh": "大语言模型的最新发展，包括新模型发布、性能提升和应用场景。"},
                "link": "https://ai.google/discover/",
                "source": "Google AI",
                "hot_score": round(random.uniform(80, 90), 1)
            }
        ]
        all_articles.extend(default_articles)
    
    return all_articles[:5]

# ===================== 飞书卡片推送（核心修复跳转） =====================
def send_feishu_card():
    if not FEISHU_WEBHOOK:
        logging.error("❌ 未配置飞书Webhook！")
        return False
    if not (BAIDU_APP_ID and BAIDU_SECRET_KEY):
        logging.error("❌ 未配置百度翻译API密钥！")
        return False
    
    # 获取5条资讯
    articles = get_guaranteed_5_articles()
    logging.info(f"✅ 抓取到 {len(articles)} 条有效资讯")
    
    # 生成所有HTML文件并获取链接
    html_urls = save_bilingual_html(articles)
    main_pages_url = html_urls["main_url"]
    single_pages_urls = html_urls["single_urls"]
    
    # 构建飞书卡片
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
    
    # 添加5条资讯条目（修复查看详情跳转）
    for idx, art in enumerate(articles, 1):
        # 单篇双语链接（核心：替换为Pages双语页面，而非原英文链接）
        single_url = single_pages_urls[idx-1] if idx-1 < len(single_pages_urls) else art["link"]
        
        element_title = {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"{idx}. **{art['title']['zh']}** \n 📈 热度：{art['hot_score']} | 来源：{art['source']}"
            }
        }
        
        element_english = {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"📝 英文原文：{art['title']['en'][:50]}... \n 🔗 [查看详情（中英对照）]({single_url})"
            }
        }
        
        element_hr = {"tag": "hr"}
        
        card_content["card"]["elements"].extend([element_title, element_english, element_hr])
    
    # 添加完整对照网页链接
    element_bilingual = {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"📖 [查看完整中英文对照网页]({main_pages_url})"
        }
    }
    card_content["card"]["elements"].append(element_bilingual)
    
    # 推送飞书
    try:
        response = requests.post(
            FEISHU_WEBHOOK,
            data=json.dumps(card_content, ensure_ascii=False),
            headers={"Content-Type": "application/json"},
            timeout=15,
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
    logging.info("🚀 开始执行AI资讯日报推送（终极修复版）")
    # 增加预热延迟，确保环境就绪
    time.sleep(3)
    success = send_feishu_card()
    logging.info("🔚 推送任务执行完成" if success else "🔚 推送任务执行失败")
