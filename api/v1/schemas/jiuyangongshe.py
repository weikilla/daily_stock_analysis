# -*- coding: utf-8 -*-
"""
韭研公社 API 请求/响应模型
"""

from typing import List, Optional, Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    phone: str = Field(..., description="手机号")
    password: str = Field(..., description="密码")


class LoginResponseData(BaseModel):
    user_id: str
    phone: str
    nickname: str
    sessionToken: str
    avatar: Optional[str] = None
    follow_count: int = 0
    fans_count: int = 0


class LoginResponse(BaseModel):
    msg: str = ""
    errCode: str = "0"
    data: LoginResponseData
    serverTime: int


class UserInfoResponse(BaseModel):
    msg: str = ""
    errCode: str = "0"
    data: Optional[dict] = None


class BroadcastItem(BaseModel):
    title: str
    create_time: str
    target_id: str
    type: int


class BroadcastListRequest(BaseModel):
    start: str = "1"
    limit: str = "10"


class BroadcastListData(BaseModel):
    pageNo: int
    pageSize: int
    result: List[BroadcastItem]


class BroadcastListResponse(BaseModel):
    msg: str = ""
    errCode: str = "0"
    data: Optional[BroadcastListData] = None


class ArticleDetailRequest(BaseModel):
    is_read: str = "1"
    article_id: str


class StockInfo(BaseModel):
    stock_id: Optional[str] = None
    name: str
    code: str


class ArticleDetailData(BaseModel):
    article_id: str
    title: str
    content: str = ""
    create_time: str = ""
    comment_count: int = 0
    like_count: int = 0
    user: Optional[dict] = None
    stock_list: List[StockInfo] = []


class ArticleDetailResponse(BaseModel):
    msg: str = ""
    errCode: str = "0"
    data: Optional[ArticleDetailData] = None


class FollowArticlesRequest(BaseModel):
    type: str = "1"
    start: str = "1"
    limit: str = "10"


class FollowArticleItem(BaseModel):
    article_id: str
    title: str
    create_time: str = ""
    type: int = 1


class ArticleListData(BaseModel):
    pageNo: int
    pageSize: int
    result: List[FollowArticleItem]


class ArticleListResponse(BaseModel):
    msg: str = ""
    errCode: str = "0"
    data: Optional[ArticleListData] = None


class IndustryItem(BaseModel):
    industry_id: str
    title: str
    keyword: str = ""


class IndustryListRequest(BaseModel):
    keyword: str = ""
    limit: str = "15"
    start: str = "1"


class IndustryListData(BaseModel):
    result: List[IndustryItem]


class IndustryListResponse(BaseModel):
    msg: str = ""
    errCode: str = "0"
    data: Optional[IndustryListData] = None


class DiagramUrlRequest(BaseModel):
    date: str = Field(..., description="日期 YYYY-MM-DD")


class DiagramUrlResponse(BaseModel):
    msg: str = ""
    errCode: str = "0"
    data: str = ""


class ProductArticleItem(BaseModel):
    product_article_id: str
    title: str
    describe: str = ""
    create_time: str = ""
    author: Optional[dict] = None


class ProductArticleListRequest(BaseModel):
    product_id: str = "1"
    start: str = "1"
    limit: str = "15"


class ProductArticleListData(BaseModel):
    result: List[ProductArticleItem]


class ProductArticleListResponse(BaseModel):
    msg: str = ""
    errCode: str = "0"
    data: Optional[ProductArticleListData] = None


class TimelineItem(BaseModel):
    article_id: str
    title: str
    content: str = ""
    create_time: str = ""
    user: Optional[dict] = None
    timeline: Optional[dict] = None


class TimelineDateItem(BaseModel):
    date: str
    list: List[TimelineItem]


class TimelineRequest(BaseModel):
    date: str = Field(..., description="年份或年月，如 2026 或 2026-04")
    grade: str = "0"
    keyword: str = ""


class TimelineResponse(BaseModel):
    msg: str = ""
    errCode: str = "0"
    data: List[TimelineDateItem] = []


class ActionListRequest(BaseModel):
    action_field_id: str = Field(..., description="如 all,2026-04-17 或 recommend,2026-04-17")
    start: str = "1"
    limit: str = "10"
    sort_time: str = "0"
    sort_price: str = "0"
    sort_range: str = "0"
    is_filter_st: str = "0"


class ActionListResponse(BaseModel):
    msg: str = ""
    errCode: str = "0"
    data: Optional[dict] = None


class UserNoticeItem(BaseModel):
    type: int
    title: str
    content: str
    create_time: str


class UserNoticeResponse(BaseModel):
    msg: str = ""
    errCode: str = "0"
    data: List[UserNoticeItem] = []


# === 内部存储模型 ===

class JiuyangongsheArticleRecord(BaseModel):
    """存储到 DB 的单条文章记录"""
    article_id: str
    title: str
    content: str = ""
    create_time: str = ""
    article_type: str = ""
    stocks: List[StockInfo] = []
    fetched_at: str = ""


class JiuyangongsheFetchResult(BaseModel):
    """抓取结果汇总"""
    success: bool = True
    premarket: List[JiuyangongsheArticleRecord] = []
    review: List[JiuyangongsheArticleRecord] = []
    diagram_url: str = ""
    industry: List[Any] = []
    digest: List[Any] = []
    error: Optional[str] = None
