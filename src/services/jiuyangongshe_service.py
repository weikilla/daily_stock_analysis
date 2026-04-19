# -*- coding: utf-8 -*-
"""
韭研公社服务层

提供：
1. APP API 请求（带 token 管理）
2. 定时抓取逻辑（盘前 09:10 / 复盘 22:00）
3. DB 存储（jiuyangongshe_intel 表）
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Any, Dict

import requests

from src.config import get_config
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

# === API 配置 ===
_APP_BASE = "https://app.jiuyangongshe.com/jystock-app/api"
_REQUEST_TIMEOUT = 15
_DEVICE_TOKEN = "63C8320CE1786D66911ED9D79F96D9E7"


# === 数据模型 ===

@dataclass
class JiuyangongsheArticle:
    """单篇文章数据"""
    article_id: str
    title: str
    content: str = ""
    create_time: str = ""
    article_type: str = ""  # premarket / review / industry / digest / other
    stocks: List[Dict[str, str]] = None  # [{name, code}]
    fetched_at: str = ""

    def __post_init__(self):
        if self.stocks is None:
            self.stocks = []


@dataclass
class JiuyangongsheFetchResult:
    """抓取结果汇总"""
    success: bool = True
    premarket: List[JiuyangongsheArticle] = None
    review: List[JiuyangongsheArticle] = None
    diagram_url: str = ""
    industry: List[Any] = None
    digest: List[Any] = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.premarket is None:
            self.premarket = []
        if self.review is None:
            self.review = []
        if self.industry is None:
            self.industry = []
        if self.digest is None:
            self.digest = []


# === API 层 ===

def _build_headers(token: str, timestamp: str) -> dict:
    return {
        "Content-Type": "application/json",
        "version_name": "1.3.4",
        "version": "2025070723",
        "platform": "1",
        "token": token,
        "timestamp": timestamp,
    }


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


# === 服务类 ===

class JiuyangongsheService:
    """韭研公社服务"""

    def __init__(self):
        self.config = get_config()
        self.db = DatabaseManager.get_instance()

    def refresh_token(self) -> tuple[str, str]:
        """用配置中的凭据重新登录"""
        phone = self.config.jiuyangongshe_phone
        password = self.config.jiuyangongshe_password
        if not phone or not password:
            raise RuntimeError("未配置 JIUYANGONGSHE_PHONE / JIUYANGONGSHE_PASSWORD")
        return self.login(phone, password)

    def login(self, phone: str, password: str) -> tuple[str, str]:
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

    # --- API 请求 ---

    def fetch_user_info(self, token: str, cookie: str) -> dict:
        return _api("/v1/user/info", {}, token, cookie)

    def fetch_broadcast_list(self, token: str, cookie: str, start: str = "1", limit: str = "10") -> dict:
        return _api("/v1/user/notice/broadcast", {"start": start, "limit": limit}, token, cookie)

    def fetch_article_detail(self, token: str, cookie: str, article_id: str) -> dict:
        return _api("/v2/article/detail", {"is_read": "1", "article_id": article_id}, token, cookie)

    def fetch_follow_articles(self, token: str, cookie: str, article_type: str, start: str, limit: str) -> dict:
        return _api("/v2/user/article/follow", {"type": article_type, "start": start, "limit": limit}, token, cookie)

    def fetch_industry_list(self, token: str, cookie: str, keyword: str, limit: str, start: str) -> dict:
        return _api("/v1/industry/list", {"keyword": keyword, "limit": limit, "start": start}, token, cookie)

    def fetch_diagram_url(self, token: str, cookie: str, action_date: str) -> dict:
        return _api("/v1/action/diagram-url", {"date": action_date}, token, cookie)

    def fetch_action_list(self, token: str, cookie: str, action_field_id: str, start: str, limit: str,
                          sort_time: str, sort_price: str, sort_range: str, is_filter_st: str) -> dict:
        return _api("/v1/action/list", {
            "action_field_id": action_field_id, "start": start, "limit": limit,
            "sort_time": sort_time, "sort_price": sort_price, "sort_range": sort_range, "is_filter_st": is_filter_st
        }, token, cookie)

    def fetch_product_articles(self, token: str, cookie: str, product_id: str, start: str, limit: str) -> dict:
        return _api("/v1/product/article/list", {"product_id": product_id, "start": start, "limit": limit}, token, cookie)

    def fetch_timeline(self, token: str, cookie: str, date: str, grade: str, keyword: str) -> dict:
        return _api("/v1/timeline/list", {"date": date, "grade": grade, "keyword": keyword}, token, cookie)

    # --- 工具 ---

    @staticmethod
    def strip_html(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text).strip()

    # --- 内容抓取 ---

    def _fetch_articles_by_keywords(
        self, token: str, cookie: str, keywords: list, article_type: str, max_articles: int = 10
    ) -> List[JiuyangongsheArticle]:
        """根据关键词从广播列表抓取文章详情"""
        results = []
        broadcast_data = self.fetch_broadcast_list(token, cookie, start="1", limit="20")
        articles = broadcast_data.get("data", {}).get("result", [])

        count = 0
        for a in articles:
            title = a.get("title", "")
            if not any(kw in title for kw in keywords):
                continue
            target_id = a.get("target_id", "")
            if not target_id:
                continue
            try:
                detail_data = self.fetch_article_detail(token, cookie, target_id)
                detail = detail_data.get("data", {})
                if not detail:
                    continue
                stocks = detail.get("stock_list", [])
                results.append(JiuyangongsheArticle(
                    article_id=target_id,
                    title=detail.get("title", title),
                    content=self.strip_html(detail.get("content", "")),
                    create_time=detail.get("create_time", a.get("create_time", "")),
                    article_type=article_type,
                    stocks=[{"name": s.get("name", ""), "code": s.get("code", "")} for s in stocks],
                    fetched_at=datetime.now().isoformat(),
                ))
                count += 1
                if count >= max_articles:
                    break
            except Exception as e:
                logger.warning(f"抓取文章 {target_id} 失败: {e}")
        return results

    def fetch_premarket_content(self, token: str, cookie: str) -> List[JiuyangongsheArticle]:
        """抓取盘前纪要和投票"""
        return self._fetch_articles_by_keywords(token, cookie, ["盘前", "投票"], "premarket", max_articles=10)

    def fetch_review_content(self, token: str, cookie: str) -> List[JiuyangongsheArticle]:
        """抓取复盘内容"""
        return self._fetch_articles_by_keywords(
            token, cookie, ["复盘", "涨停简图", "晚间公告", "内容精选", "精选"], "review", max_articles=10
        )

    def fetch_all(self) -> JiuyangongsheFetchResult:
        """完整抓取流程：登录 → 抓取 → 存 DB"""
        try:
            token, cookie = self.refresh_token()
        except Exception as e:
            return JiuyangongsheFetchResult(success=False, error=f"登录失败: {e}")

        try:
            today = datetime.now().strftime("%Y-%m-%d")
            premarket = self.fetch_premarket_content(token, cookie)
            review = self.fetch_review_content(token, cookie)

            diagram_data = self.fetch_diagram_url(token, cookie, today)
            diagram_url = diagram_data.get("data", "") or ""

            self._save_articles(premarket)
            self._save_articles(review)

            # 存涨停简图 URL
            if diagram_url:
                self.db.save_jiuyangongshe_intel(
                    article_id=f"diagram_{today}",
                    title=f"涨停简图 {today}",
                    article_type="diagram",
                    diagram_url=diagram_url,
                )

            return JiuyangongsheFetchResult(
                success=True,
                premarket=premarket,
                review=review,
                diagram_url=diagram_url,
            )
        except Exception as e:
            logger.error(f"抓取失败: {e}", exc_info=True)
            return JiuyangongsheFetchResult(success=False, error=str(e))

    def _save_articles(self, articles: List[JiuyangongsheArticle]):
        for a in articles:
            self.db.save_jiuyangongshe_intel(
                article_id=a.article_id,
                title=a.title,
                content=a.content,
                create_time=a.create_time,
                article_type=a.article_type,
                stocks=json.dumps(a.stocks, ensure_ascii=False),
            )

    def get_cached_intel(self, fetch_type: str = "all", stock_code: Optional[str] = None) -> dict:
        """从 DB 查询缓存的情报"""
        if fetch_type == "premarket":
            articles = self.db.get_jiuyangongshe_intel(article_type="premarket", stock_code=stock_code, limit=20)
        elif fetch_type == "review":
            articles = self.db.get_jiuyangongshe_intel(article_type="review", stock_code=stock_code, limit=20)
        else:
            articles = self.db.get_jiuyangongshe_intel(stock_code=stock_code, limit=50)

        items = []
        for a in articles:
            stocks = []
            if a.get("stocks"):
                try:
                    stocks = json.loads(a["stocks"])
                except Exception:
                    pass
            items.append({
                "article_id": a.get("article_id"),
                "title": a.get("title"),
                "content": a.get("content"),
                "create_time": a.get("create_time"),
                "article_type": a.get("article_type"),
                "stocks": stocks,
                "fetched_at": a.get("fetched_at"),
            })

        return {"success": True, "items": items, "count": len(items)}
