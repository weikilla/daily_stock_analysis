# -*- coding: utf-8 -*-
"""
==================================
韭研公社 API 接口
==================================

提供韭研公社 APP API 的代理接口（带 token 管理），
同时支持直接在 DB 中查询已缓存的韭研公社情报。

抓取时间（定时任务自动更新）：
- 盘前：每个交易日 09:10
- 复盘：每个交易日 22:00
"""

import logging
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Body, Query

from api.v1.schemas.jiuyangongshe import (
    LoginRequest,
    LoginResponse,
    UserInfoResponse,
    BroadcastListRequest,
    BroadcastListResponse,
    ArticleDetailRequest,
    ArticleDetailResponse,
    FollowArticlesRequest,
    ArticleListResponse,
    IndustryListRequest,
    IndustryListResponse,
    DiagramUrlRequest,
    DiagramUrlResponse,
    ProductArticleListRequest,
    ProductArticleListResponse,
    TimelineRequest,
    TimelineResponse,
    ActionListRequest,
    ActionListResponse,
    JiuyangongsheFetchResult,
)
from api.v1.schemas.common import ErrorResponse
from src.services.jiuyangongshe_service import JiuyangongsheService

logger = logging.getLogger(__name__)
router = APIRouter()

# 内存缓存：每次登录后更新（仅用于本进程）
_cached_token: Optional[str] = None
_cached_cookie: Optional[str] = None


# === 内部辅助 ===

def _get_service() -> JiuyangongsheService:
    return JiuyangongsheService()


def _refresh_token(service: JiuyangongsheService):
    """刷新内存中的 token"""
    global _cached_token, _cached_cookie
    _cached_token, _cached_cookie = service.refresh_token()
    logger.info("韭研公社 token 已刷新")


# === 认证接口（无需 token） ===

@router.post(
    "/v1/user/login",
    response_model=LoginResponse,
    responses={200: {"description": "登录成功"}, 9: {"description": "版本过低"}},
    summary="用户登录",
    description="使用手机号密码登录，返回 sessionToken。登录后其他接口使用返回的 token。",
    tags=["韭研公社 - 认证"],
    include_in_schema=True,
)
def login(request: LoginRequest):
    """登录并缓存 token（进程内存）"""
    service = _get_service()
    token, cookie = service.login(request.phone, request.password)
    global _cached_token, _cached_cookie
    _cached_token = token
    _cached_cookie = cookie
    return service.fetch_user_info(token, cookie)


# === 代理接口（需要 token） ===

def _require_token():
    """确保有可用 token，没有则抛出异常"""
    if not _cached_token or not _cached_cookie:
        raise HTTPException(
            status_code=401,
            detail={"error": "not_authenticated", "message": "请先调用 /api/v1/jiuyangongshe/v1/user/login 登录"}
        )


@router.post(
    "/v1/user/info",
    response_model=UserInfoResponse,
    summary="获取用户信息",
    tags=["韭研公社 - 认证"],
)
def get_user_info():
    _require_token()
    service = _get_service()
    return service.fetch_user_info(_cached_token, _cached_cookie)


@router.post(
    "/v1/user/notice/broadcast",
    response_model=BroadcastListResponse,
    summary="获取广播消息列表",
    description="返回关注的广播消息列表（盘前纪要/涨停简图/晚间公告等）",
    tags=["韭研公社 - 内容"],
)
def get_broadcast_list(request: BroadcastListRequest = Body(default=BroadcastListRequest())):
    _require_token()
    service = _get_service()
    return service.fetch_broadcast_list(_cached_token, _cached_cookie, request.start, request.limit)


@router.post(
    "/v2/article/detail",
    response_model=ArticleDetailResponse,
    summary="获取文章详情",
    description="根据 article_id（broadcast 返回的 target_id）获取文章完整内容",
    tags=["韭研公社 - 内容"],
)
def get_article_detail(request: ArticleDetailRequest = Body(...)):
    _require_token()
    service = _get_service()
    return service.fetch_article_detail(_cached_token, _cached_cookie, request.article_id)


@router.post(
    "/v2/user/article/follow",
    response_model=ArticleListResponse,
    summary="获取关注者文章列表",
    description="按 type 分类获取关注文章。type: 1=综合 2=题材 3=公告 4=我的自选 5=其他",
    tags=["韭研公社 - 内容"],
)
def get_follow_articles(request: FollowArticlesRequest = Body(...)):
    _require_token()
    service = _get_service()
    return service.fetch_follow_articles(_cached_token, _cached_cookie, request.type, request.start, request.limit)


@router.post(
    "/v1/industry/list",
    response_model=IndustryListResponse,
    summary="获取题材库列表",
    tags=["韭研公社 - 题材"],
)
def get_industry_list(request: IndustryListRequest = Body(default=IndustryListRequest())):
    _require_token()
    service = _get_service()
    return service.fetch_industry_list(_cached_token, _cached_cookie, request.keyword, request.limit, request.start)


@router.post(
    "/v1/action/diagram-url",
    response_model=DiagramUrlResponse,
    summary="获取涨停简图 URL",
    description="根据日期获取涨停简图图片 URL",
    tags=["韭研公社 - 涨停"],
)
def get_diagram_url(request: DiagramUrlRequest = Body(...)):
    _require_token()
    service = _get_service()
    return service.fetch_diagram_url(_cached_token, _cached_cookie, request.date)


@router.post(
    "/v1/action/list",
    response_model=ActionListResponse,
    summary="获取涨停股列表",
    description="获取涨停股详情。action_field_id 格式: all,YYYY-MM-DD 或 recommend,YYYY-MM-DD",
    tags=["韭研公社 - 涨停"],
)
def get_action_list(request: ActionListRequest = Body(...)):
    _require_token()
    service = _get_service()
    return service.fetch_action_list(
        _cached_token, _cached_cookie,
        request.action_field_id, request.start, request.limit,
        request.sort_time, request.sort_price, request.sort_range, request.is_filter_st
    )


@router.post(
    "/v1/product/article/list",
    response_model=ProductArticleListResponse,
    summary="获取产品文章列表",
    description="获取逻辑红宝书等产品文章列表",
    tags=["韭研公社 - 内容"],
)
def get_product_articles(request: ProductArticleListRequest = Body(default=ProductArticleListRequest())):
    _require_token()
    service = _get_service()
    return service.fetch_product_articles(_cached_token, _cached_cookie, request.product_id, request.start, request.limit)


@router.post(
    "/v1/timeline/list",
    response_model=TimelineResponse,
    summary="获取大事件日历",
    description="按年月获取财经大事件时间线",
    tags=["韭研公社 - 日历"],
)
def get_timeline(request: TimelineRequest = Body(...)):
    _require_token()
    service = _get_service()
    return service.fetch_timeline(_cached_token, _cached_cookie, request.date, request.grade, request.keyword)


# === 内部缓存查询接口（无需认证） ===

@router.get(
    "",
    response_model=JiuyangongsheFetchResult,
    summary="获取缓存的韭研公社情报",
    description="查询 DB 中已缓存的韭研公社情报（盘前纪要、复盘内容等），无需认证",
    tags=["韭研公社 - 缓存"],
)
def get_cached_intel(
    fetch_type: str = Query("all", description="all | premarket | review"),
    stock_code: Optional[str] = Query(None, description="按关联股票代码筛选，如 000815"),
):
    """
    查询已缓存的韭研公社情报（来自定时抓取）。
    定时任务在 09:10（盘前）和 22:00（复盘）自动抓取并存入 DB。
    """
    service = _get_service()
    return service.get_cached_intel(fetch_type, stock_code)


@router.post(
    "/refresh-token",
    summary="刷新认证 Token",
    description="如果 token 过期，用缓存的凭据重新登录刷新",
    tags=["韭研公社 - 认证"],
)
def refresh_token():
    """强制刷新 token（使用缓存的手机号密码）"""
    service = _get_service()
    _refresh_token(service)
    return {"success": True, "message": "token 已刷新"}
