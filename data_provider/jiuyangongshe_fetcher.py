# -*- coding: utf-8 -*-
"""
韭研公社内容抓取器（APP API 方式）

核心接口：
1. /v1/user/notice/broadcast  — 广播消息列表（含盘前纪要/涨停简图/晚间公告等）
2. /v2/article/detail         — 文章详情
3. /v1/action/diagram-url    — 涨停简图 URL
4. /v1/industry/list         — 题材库列表
5. /v1/product/article/list   — 逻辑红宝书每日精选

抓取时间：
- 盘前：每个交易日 09:10
- 复盘：每个交易日 22:00
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, date
from typing import List, Optional, Dict, Any

import requests

logger = logging.getLogger(__name__)

# === API 配置 ===
_APP_BASE = "https://app.jiuyangongshe.com/jystock-app/api"
_REQUEST_TIMEOUT = 15
_DEVICE_TOKEN = "63C8320CE1786D66911ED9D79F96D9E7"


def _build_headers(token: str, timestamp: str) -> dict:
    return {
        "Content-Type": "application/json",
        "version_name": "1.3.4",
        "version": "2025070723",
        "platform": "1",
        "token": token,
        "timestamp": timestamp,
    }


def login(phone: str, password: str) -> tuple[str, str]:
    """登录并返回 (session_token, session_cookie)"""
    timestamp = str(int(time.time() * 1000))
    r = requests.post(
        f"{_APP_BASE}/v1/user/login",
        json={"phone": phone, "password": password},
        headers={
            "Content-Type": "application/json",
            "version_name": "1.3.4",
            "version": "2025070723",
            "platform": "1",
            "token": _DEVICE_TOKEN,
            "timestamp": timestamp,
        },
        timeout=_REQUEST_TIMEOUT,
    )
    data = r.json()
    if data.get("errCode") != "0":
        raise RuntimeError(f"登录失败: {data.get('msg')} (errCode={data.get('errCode')})")
    session_token = data["data"]["sessionToken"]
    session_cookie = r.cookies.get("SESSION")
    return session_token, session_cookie


def _api(path: str, body: dict, token: str, cookie: str) -> dict:
    """带 auth 的 POST 请求"""
    timestamp = str(int(time.time() * 1000))
    r = requests.post(
        f"{_APP_BASE}{path}",
        json=body,
        headers=_build_headers(token, timestamp),
        cookies={"SESSION": cookie},
        timeout=_REQUEST_TIMEOUT,
    )
    return r.json()


# === 核心接口 ===

def fetch_broadcast_list(token: str, cookie: str, start: int = 1, limit: int = 10) -> List[dict]:
    """获取广播消息列表（盘前纪要/涨停简图/晚间公告等）"""
    data = _api("/v1/user/notice/broadcast", {"start": str(start), "limit": str(limit)}, token, cookie)
    if data.get("errCode") != "0":
        raise RuntimeError(f"获取广播列表失败: {data.get('msg')}")
    return data.get("data", {}).get("result", [])


def fetch_article_detail(article_id: str, token: str, cookie: str) -> dict:
    """获取文章详情"""
    data = _api("/v2/article/detail", {"is_read": "1", "article_id": article_id}, token, cookie)
    if data.get("errCode") != "0":
        raise RuntimeError(f"获取文章详情失败: {data.get('msg')}")
    return data.get("data", {})


def fetch_industry_list(token: str, cookie: str, keyword: str = "", limit: int = 15) -> List[dict]:
    """获取题材库列表"""
    data = _api("/v1/industry/list", {"keyword": keyword, "limit": str(limit), "start": "1"}, token, cookie)
    if data.get("errCode") != "0":
        raise RuntimeError(f"获取题材列表失败: {data.get('msg')}")
    return data.get("data", {}).get("result", [])


def fetch_product_articles(token: str, cookie: str, product_id: str = "1", start: int = 1, limit: int = 15) -> List[dict]:
    """获取产品文章列表（如逻辑红宝书每日精选）"""
    data = _api("/v1/product/article/list", {"product_id": product_id, "start": str(start), "limit": str(limit)}, token, cookie)
    if data.get("errCode") != "0":
        raise RuntimeError(f"获取产品文章失败: {data.get('msg')}")
    return data.get("data", {}).get("result", [])


def fetch_action_diagram_url(token: str, cookie: str, action_date: str) -> str:
    """获取指定日期的涨停简图 URL"""
    data = _api("/v1/action/diagram-url", {"date": action_date}, token, cookie)
    if data.get("errCode") != "0":
        return ""
    return data.get("data", "")


def fetch_timeline_list(token: str, cookie: str, year: str = None) -> List[dict]:
    """获取大事件日历"""
    data = _api("/v1/timeline/list", {"grade": "0", "keyword": "", "date": year or str(datetime.now().year)}, token, cookie)
    if data.get("errCode") != "0":
        raise RuntimeError(f"获取时间线失败: {data.get('msg')}")
    return data.get("data", [])


# === 工具函数 ===

def strip_html(text: str) -> str:
    """去掉 HTML 标签"""
    return re.sub(r"<[^>]+>", "", text).strip()


def format_article_for_prompt(article: dict) -> str:
    """格式化文章为 prompt 友好文本"""
    title = article.get("title", "无标题")
    create_time = article.get("create_time", "")
    stocks = article.get("stock_list", [])
    stock_str = ", ".join([f"{s['name']}({s['code']})" for s in stocks]) if stocks else "无关联股票"
    content = strip_html(article.get("content", ""))
    return f"""【{title}】
时间: {create_time}
关联股票: {stock_str}
内容: {content[:800]}"""


# === 关键词匹配盘前/复盘内容 ===

def is_premarket(article: dict) -> bool:
    """判断是否是盘前纪要"""
    title = article.get("title", "")
    return "盘前" in title or "盘前纪要" in title


def is_review(article: dict) -> bool:
    """判断是否是复盘内容"""
    title = article.get("title", "")
    return any(kw in title for kw in ["复盘", "涨停简图", "晚间公告", "公社内容精选", "内容精选"])


def is_vote(article: dict) -> bool:
    """判断是否是投票"""
    title = article.get("title", "")
    return "投票" in title


# === 主流程 ===

def fetch_premarket_content(token: str, cookie: str) -> List[dict]:
    """抓取盘前相关的内容（盘前纪要 + 投票）"""
    articles = fetch_broadcast_list(token, cookie, start=1, limit=20)
    results = []
    for a in articles:
        title = a.get("title", "")
        target_id = a.get("target_id", "")
        create_time = a.get("create_time", "")
        # 匹配盘前纪要
        if "盘前" in title and target_id:
            try:
                detail = fetch_article_detail(target_id, token, cookie)
                results.append(detail)
            except Exception as e:
                logger.warning(f"抓取盘前纪要 {target_id} 失败: {e}")
    return results


def fetch_review_content(token: str, cookie: str) -> List[dict]:
    """抓取复盘相关的内容（复盘 + 涨停简图 + 晚间公告等）"""
    articles = fetch_broadcast_list(token, cookie, start=1, limit=20)
    results = []
    for a in articles:
        title = a.get("title", "")
        target_id = a.get("target_id", "")
        if any(kw in title for kw in ["复盘", "涨停简图", "晚间公告", "内容精选", "精选"]) and target_id:
            try:
                detail = fetch_article_detail(target_id, token, cookie)
                results.append(detail)
            except Exception as e:
                logger.warning(f"抓取复盘内容 {target_id} 失败: {e}")
    return results


def run_fetcher(phone: str, password: str, fetch_type: str = "all") -> dict:
    """
    完整抓取流程。fetch_type: 'premarket' | 'review' | 'all'
    返回 {'premarket': [...], 'review': [...], 'industry': [...], 'digest': [...]}
    """
    token, cookie = login(phone, password)

    result = {}

    if fetch_type in ("premarket", "all"):
        logger.info("抓取盘前内容...")
        result["premarket"] = fetch_premarket_content(token, cookie)

    if fetch_type in ("review", "all"):
        logger.info("抓取复盘内容...")
        result["review"] = fetch_review_content(token, cookie)
        logger.info("抓取涨停简图 URL...")
        today = datetime.now().strftime("%Y-%m-%d")
        result["diagram_url"] = fetch_action_diagram_url(token, cookie, today)

    if fetch_type in ("all",):
        logger.info("抓取题材库...")
        result["industry"] = fetch_industry_list(token, cookie)
        logger.info("抓取逻辑红宝书...")
        result["digest"] = fetch_product_articles(token, cookie)

    return result


if __name__ == "__main__":
    import pprint

    phone = "15160047541"
    password = "Am112211"

    print("=== 登录 ===")
    token, cookie = login(phone, password)
    print("登录成功")

    print("\n=== 广播列表（最近20条）===")
    articles = fetch_broadcast_list(token, cookie, start=1, limit=20)
    for a in articles:
        print(f"  [{a['target_id']}] {a['create_time'][:10]} | {a['title'][:50]}")

    print("\n=== 抓取今日涨停简图 ===")
    today = datetime.now().strftime("%Y-%m-%d")
    # 找最近的涨停简图
    for a in articles:
        if "涨停简图" in a.get("title", ""):
            url = fetch_action_diagram_url(token, cookie, a['create_time'][:10])
            print(f"  {a['title']} ({a['create_time'][:10]}): {url}")

    print("\n=== 盘前纪要详情 ===")
    for a in articles:
        if "盘前纪要" in a.get("title", ""):
            detail = fetch_article_detail(a["target_id"], token, cookie)
            print(f"标题: {detail.get('title')}")
            content = strip_html(detail.get("content", ""))[:300]
            print(f"内容: {content}...")
            break

    print("\n=== 题材库（最新3条）===")
    industries = fetch_industry_list(token, cookie, limit=3)
    for ind in industries:
        print(f"  {ind.get('title', '')[:60]}")

    print("\n=== 逻辑红宝书最新文章 ===")
    digest = fetch_product_articles(token, cookie, limit=3)
    for d in digest:
        print(f"  {d.get('create_time', '')[:10]} | {d.get('title', '')[:50]}")
        print(f"  摘要: {d.get('describe', '')[:100]}")
