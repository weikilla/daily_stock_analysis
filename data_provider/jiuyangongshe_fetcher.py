"""
韭研公社内容抓取器（APP API 方式 + 纯 Python TLS JA3 模拟）

2026-08-25: 服务端加了 TLS JA3 + token 算法 + 强制升级墙。
本 fetcher 用以下组合绕过:
1. tls-client 模拟 OkHttp4_Android_13 JA3 指纹(代替 Python requests)
2. 服务端实时 Date header 校准 ISO timestamp(代替本地 UTC 时钟)
3. AES-CBC-128 + IV=key + PKCS7 算法算 token(从 Frida hook d.t() 拿到的 strategy)
4. 1.3.8 / 2026080619 / platform=0(从 Frida hook RealInterceptorChain 拿到的真实 build)

strategy 缓存到 end=2026-08-31。sessionToken 15 天有效,过期后用户重跑 fetcher.login() 拿新。

依赖: pip install tls-client cryptography
"""

import os
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, date, timezone
from typing import List, Optional, Dict, Any

import requests
import tls_client
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

# === API 配置 ===
_APP_BASE = "https://app-api.jiuyangongshe.com/jystock-app/api"


# === Session 持久化(15 天有效期) ===
def _env_path():
    """找 .env 路径。优先 fetcher 父目录的 .env,fallback cwd。"""
    import sys
    import os
    from pathlib import Path
    # 优先用 __file__
    if "__file__" in globals() and __file__:
        return str(Path(__file__).parent.parent / ".env")
    # fallback: 找 import 路径的 module
    for mod_name in ["data_provider.jiuyangongshe_fetcher"]:
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "__file__") and mod.__file__:
            return str(Path(mod.__file__).parent.parent / ".env")
    # fallback: cwd/.env
    return ".env"


def _read_cached_session():
    """从 .env 读 JIUYANGONGSHE_SESSION + JIUYANGONGSHE_SESSION_EXPIRES。
    未过期返回 (session, expires_iso);否则返回 (None, None)"""
    import re, os
    from datetime import datetime
    path = _env_path()
    if not os.path.exists(path):
        return None, None
    with open(path) as f:
        text = f.read()
    m_token = re.search(r"^JIUYANGONGSHE_SESSION=(\S+)", text, re.MULTILINE)
    if not m_token:
        return None, None
    m_exp = re.search(r"^JIUYANGONGSHE_SESSION_EXPIRES=(\S+)", text, re.MULTILINE)
    expires_iso = m_exp.group(1) if m_exp else None
    if expires_iso:
        try:
            exp_dt = datetime.strptime(expires_iso.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
            now = datetime.utcnow()
            if exp_dt < now:
                return None, None  # 已过期
        except Exception:
            return None, None
    return m_token.group(1), expires_iso


def _write_session_to_env(session_token, expires_iso):
    """写 session + expires 到 .env"""
    import re, os
    path = _env_path()
    if not os.path.exists(path):
        return
    with open(path) as f:
        text = f.read()
    text = re.sub(r"^JIUYANGONGSHE_SESSION=.*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"^JIUYANGONGSHE_SESSION_EXPIRES=.*\n", "", text, flags=re.MULTILINE)
    text += f"\nJIUYANGONGSHE_SESSION={session_token}\n"
    text += f"JIUYANGONGSHE_SESSION_EXPIRES={expires_iso}\n"
    with open(path, "w") as f:
        f.write(text)
_REQUEST_TIMEOUT = 15

# 真实 SDK build headers(2026-08-25 Frida hook 拿到)
_BUILD_VERSION_NAME = "1.3.8"
_BUILD_VERSION = "2026080619"
_BUILD_PLATFORM = "0"
_BUILD_USER_AGENT = f"Jiuyangongshe/{_BUILD_VERSION}(Android;OPPO;PGFM10;12:2.25)"

# Strategy 缓存(2026-08-25 Frida hook d.t() 拿到)
_STRATEGY_PROJECT = "jiuyangongshe"
_STRATEGY_AES_KEY = b"MS5V7FCRznPt0CJs"
_STRATEGY_EXPIRES = "2026-08-31T16:00:00Z"  # 过期前 fetcher 都可用


# === Token 算法 + ts 校准 ===
#
# ╔══════════════════════════════════════════════════════════════════════╗
# ║ 重要: AES 与 MD5 算法使用不同的 timestamp 格式                    ║
# ╠══════════════════════════════════════════════════════════════════════╣
# ║ AES  (Frida SDK 实际行为)   - ISO 字符串   "2026-08-25T01:30:00Z" ║
# ║ MD5  (Mingdi 实际行为)    - 毫秒数字   "1787674931900"             ║
# ╚══════════════════════════════════════════════════════════════════════╝
#
# 踩坑记录: fetcher 之前 MD5 fallback 用 ISO ts → 服务端返 110(格式不对)
# 当时以为服务端"用不同 SECRET 拒绝",实际 ts 格式错了就 fail
# 修 _get_display_ts() 根据 algorithm 自动选 ISO 还是毫秒,不再踩
# 详细: 2026-08-25 调试 Mingdi 仓库 (Mingdi-hub/jiuyan-stock-sentiment-skill)

# 当前算法 (main): Frida hook 拿到的 AES strategy
def _compute_jys_token_aes(timestamp_iso: str) -> str:
    """主算法 - 本地 AES-CBC-128 算 token(服务端校验通过,与 Frida SDK 算法一致)"""
    pt = (_STRATEGY_PROJECT + _STRATEGY_AES_KEY.decode() + timestamp_iso).encode("utf-8")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(pt) + padder.finalize()
    cipher = Cipher(algorithms.AES(_STRATEGY_AES_KEY), modes.CBC(_STRATEGY_AES_KEY), backend=default_backend())
    encryptor = cipher.encryptor()
    ct = encryptor.update(padded) + encryptor.finalize()
    return ct.hex().upper()


# 备用算法 - web API SECRET (来自 Mingdi 仓库 jiuyan_fetch.py)
# 算法: MD5(SECRET + ":" + 毫秒时间戳),header 用毫秒时间戳不是 ISO 字符串
_WEB_API_SECRET = "Uu0KfOB8iUP69d3c"  # 公开 web API SECRET (stable,几年不变)
_wb_strategy = "md5_web"  # 算法协商标签


def _compute_jys_token_md5(timestamp_iso: str = None) -> str:
    """备用算法 - APP API 的 MD5 token 算法

    来源: Mingdi-hub/jiuyan-stock-sentiment-skill 仓库,公开 SECRET:
        SECRET = "Uu0KfOB8iUP69d3c"
        算法    = MD5(SECRET + ":" + 毫秒时间戳)

    ⚠️ 关键: timestamp header 必须是毫秒数字字符串 (不是 ISO 字符串!)
       例如: timestamp="1787674931900", 不是 "2026-08-25T01:30:00Z"
       _get_display_ts("md5", ts_iso) 帮你自动转换

    用途:
    - 适用于 app-api.jiuyangongshe.com (APP API)
    - 当 AES 算法失效时(如服务端 strategy 轮换),自动降级到 MD5
    """
    if timestamp_iso:
        from datetime import datetime as _dt
        dt = _dt.strptime(timestamp_iso, "%Y-%m-%dT%H:%M:%SZ")
        ts_ms = str(int(dt.timestamp() * 1000))
    else:
        ts_ms = str(int(time.time() * 1000))
    return hashlib.md5(f"{_WEB_API_SECRET}:{ts_ms}".encode()).hexdigest()


# Fetcher 状态 - 记录当前 token 算法(AES/MD5),login 时协商
_fetcher_state_cache = None


def _get_display_ts(algorithm: str, timestamp_iso: str) -> str:
    """根据算法返回 header 用的 timestamp 字符串

    重要! 不同算法用不同 ts 格式:
      - AES 算法: ISO 字符串,如 "2026-08-25T01:30:00Z" (Frida SDK 实际行为)
      - MD5 算法: 毫秒数字字符串,如 "1787674931900" (Mingdi 实际行为)
    不要在 MD5 算法路径用 ISO ts,服务端会返 110 token 无效。
    """
    if algorithm == "md5":
        from datetime import datetime as _dt
        dt = _dt.strptime(timestamp_iso, "%Y-%m-%dT%H:%M:%SZ")
        return str(int(dt.timestamp() * 1000))
    return timestamp_iso
    if algorithm == "md5":
        from datetime import datetime as _dt
        dt = _dt.strptime(timestamp_iso, "%Y-%m-%dT%H:%M:%SZ")
        return str(int(dt.timestamp() * 1000))
    return timestamp_iso


def _get_state():
    """从 .env 读 fetcher 状态(algorithm/strategy 等),默认 AES"""
    global _fetcher_state_cache
    if _fetcher_state_cache is None:
        from pathlib import Path
        env_path = str(Path(__file__).parent.parent / ".env")
        state = {"algorithm": "aes"}
        if os.path.exists(env_path):
            with open(env_path) as f:
                text = f.read()
            import re as _re
            m = _re.search(r"^JIUYANGONGSHE_ALGORITHM=(\S+)", text, _re.MULTILINE)
            if m:
                state["algorithm"] = m.group(1)
        _fetcher_state_cache = state
    return _fetcher_state_cache


def _save_state(state):
    """保存 fetcher 状态(algorithm)到 .env"""
    import re as _re
    from pathlib import Path
    env_path = str(Path(__file__).parent.parent / ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        text = f.read()
    text = _re.sub(r"^JIUYANGONGSHE_ALGORITHM=.*\n", "", text, flags=_re.MULTILINE)
    text += f"JIUYANGONGSHE_ALGORITHM={state['algorithm']}\n"
    with open(env_path, "w") as f:
        f.write(text)


def _compute_jys_token(timestamp_iso: str = None):
    """智能选算法 - 返回 (token, ts_to_send) 元组

    AES 算法用 ISO timestamp string(Frida SDK 行为)
    MD5 算法用毫秒数字 string(Mingdi 行为)

    Returns:
        (token: str, ts_iso_or_ms: str) — 传给 header 时按算法用对应格式
    """
    state = _get_state()
    algo = state.get("algorithm", "aes")
    ts_iso = timestamp_iso or _now_iso()
    if algo == "md5":
        return _compute_jys_token_md5(ts_iso)
    return _compute_jys_token_aes(ts_iso)


# Server-time 校准 ts(SDK 内部也用 serverTime - offsetSeconds 算 ts)
_server_time_cache = ["", 0.0]
def _now_iso() -> str:
    """从服务端 Date header 拿精确时间,5 秒缓存复用避免每次都打一次。"""
    now = time.time()
    if _server_time_cache[1] and now - _server_time_cache[1] < 5:
        return _server_time_cache[0]
    try:
        r = requests.head(f"{_APP_BASE}/v1/version_check", timeout=5)
        ds = r.headers.get("Date")
        if ds:
            dt = datetime.strptime(ds, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc)
            iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            _server_time_cache[0] = iso
            _server_time_cache[1] = now
            return iso
    except Exception:
        pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_session() -> 'tls_client.Session':
    """新 tls_client.Session(用 okhttp4_android_13 JA3 模拟 OkHttp 客户端指纹)"""
    return tls_client.Session(
        client_identifier="okhttp4_android_13",
        random_tls_extension_order=True,
    )


def _build_headers(ts_iso: str = None, token: str = None) -> dict:
    """构造完整 SDK headers(含 Python 算的 token + server-time 校准 ts)"""
    if ts_iso is None:
        ts_iso = _now_iso()
    if token is None:
        token = _compute_jys_token(ts_iso)
    return {
        "Content-Type": "application/json",
        "version_name": _BUILD_VERSION_NAME,
        "version": _BUILD_VERSION,
        "platform": _BUILD_PLATFORM,
        "User-Agent": _BUILD_USER_AGENT,
        "token": token,
        "timestamp": ts_iso,
    }


def login(phone: str, password: str, force: bool = False) -> tuple[str, str, str]:
    """登录并返回 (token, session_token, session_cookie)。

    优先用 .env 缓存的 session (15 天有效),未过期就跳过 login。
    login 时同时尝试 AES 和 MD5 算法(work 的用哪个,把 algorithm 写到 .env)。
    force=True 强制重新登录。
    """
    import hashlib

    # 1. 缓存有效 + 算法一致 → 直接用缓存
    cached = None if force else _read_cached_session()
    if cached and cached[0]:
        print(f"[login] 用 .env 缓存 session (expires={cached[1]})")
        sess = _new_session()
        ts_iso = _now_iso()
        token = _compute_jys_token(ts_iso)
        return token, cached[0], cached[0]

    # 2. 没缓存/force - 试 AES 主算法 + MD5 fallback
    state = _get_state()
    primary = state.get("algorithm", "aes")

    sess = _new_session()
    ts_iso = _now_iso()

    for algo_name, compute_fn, header_builder in [
        ("aes", lambda: _compute_jys_token_aes(ts_iso), lambda tk, ts: {
            "Content-Type":"application/json","version_name":_BUILD_VERSION_NAME,
            "version":_BUILD_VERSION,"platform":_BUILD_PLATFORM,"User-Agent":_BUILD_USER_AGENT,
            "token":tk,"timestamp":ts,
        }),
        ("md5", lambda: _compute_jys_token_md5(), lambda tk, ts: {
            "Content-Type":"application/json","version_name":_BUILD_VERSION_NAME,
            "version":_BUILD_VERSION,"platform":_BUILD_PLATFORM,"User-Agent":_BUILD_USER_AGENT,
            "token":tk,"timestamp":ts,  # MD5 用 ISO 但服务端可能容忍
        }),
    ]:
        if algo_name == primary:
            # 主算法先试
            pass
        elif primary == "aes" and algo_name == "md5":
            # 主算法 fail 后再试 fallback
            pass
        else:
            continue

        try:
            token = compute_fn()
            r = sess.post(
                f"{_APP_BASE}/v1/user/login",
                json={"phone": phone, "password": password},
                headers=header_builder(token, ts_iso),
            )
            data = r.json()
            if data.get("errCode") == "0":
                if algo_name != state.get("algorithm"):
                    print(f"[login] 主算法 {state.get('algorithm')} 失效,切换到 {algo_name}")
                    state["algorithm"] = algo_name
                    _save_state(state)
                session_token = data["data"]["sessionToken"]
                session_cookie = r.cookies.get("SESSION")
                from datetime import datetime, timedelta
                expires_iso = (datetime.utcnow() + timedelta(days=14, hours=23)).strftime("%Y-%m-%dT%H:%M:%SZ")
                _write_session_to_env(session_token, expires_iso)
                print(f"[login] 用 {algo_name} 算法登录,新 session 已缓存到 .env (expires={expires_iso})")
                return token, session_token, session_cookie
        except Exception as e:
            print(f"[login] {algo_name} 算法失败: {e}")
            continue

    raise RuntimeError(f"所有算法都失败,可能是 password 错或服务端大改")
    data = r.json()
    if data.get("errCode") != "0":
        raise RuntimeError(f"登录失败: {data.get('msg')} (errCode={data.get('errCode')})")
    session_token = data["data"]["sessionToken"]
    session_cookie = r.cookies.get("SESSION")

    # 3. 写 .env 缓存(session 15 天有效)
    from datetime import datetime, timedelta
    expires_iso = (datetime.utcnow() + timedelta(days=14, hours=23)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session_to_env(session_token, expires_iso)
    print(f"[login] 新 session 已缓存到 .env (expires={expires_iso})")

    return token, session_token, session_cookie


def _api(path: str, body: dict, session: str, cookie: str = None, phone: str = None, password: str = None) -> dict:
    """带 auth 的 POST 请求。

    - token 每次用 server-time 校准 + 自算
    - 失败重试 1 次(优先 token 失效时重算; 登录失效时强制重新登录)
    """
    sess = _new_session()
    ts_iso = _now_iso()
    token = _compute_jys_token(ts_iso)
    state = _get_state()
    hdr_ts = _get_display_ts(state.get("algorithm", "aes"), ts_iso)
    cookie_hdr = f"SESSION={cookie}; Max-Age=1296000; Path=/jystock-app; HttpOnly; SameSite=Lax" if cookie else None
    hdrs = {
        "Content-Type": "application/json",
        "version_name": _BUILD_VERSION_NAME,
        "version": _BUILD_VERSION,
        "platform": _BUILD_PLATFORM,
        "User-Agent": _BUILD_USER_AGENT,
        "token": token,
        "timestamp": hdr_ts,
    }
    if cookie_hdr:
        hdrs["Cookie"] = cookie_hdr
    r = sess.post(f"{_APP_BASE}{path}", json=body, headers=hdrs)
    data = r.json()

    # 错误重试逻辑
    err = data.get("errCode")
    if err in ("1", "110") and phone and password:
        if err == "1":
            # 登录失效: 删缓存重新登录
            print(f"[{path}] 登录失效,重新登录")
            _, new_session, new_cookie = login(phone, password, force=True)
            hdrs["Cookie"] = f"SESSION={new_cookie}; Max-Age=1296000; Path=/jystock-app; HttpOnly; SameSite=Lax"
            hdrs["token"] = _compute_jys_token(_now_iso())
            hdrs["timestamp"] = _get_display_ts(state.get("algorithm", "aes"), _now_iso())
            r = sess.post(f"{_APP_BASE}{path}", json=body, headers=hdrs)
            data = r.json()
        elif err == "110":
            # token 失效: 重新算
            print(f"[{path}] token 失效,重算")
            hdrs["token"] = _compute_jys_token(_now_iso())
            hdrs["timestamp"] = _get_display_ts(state.get("algorithm", "aes"), _now_iso())
            r = sess.post(f"{_APP_BASE}{path}", json=body, headers=hdrs)
            data = r.json()
    return data


# === 核心接口 ===
def fetch_broadcast_list(session: str, cookie: str, start: int = 1, limit: int = 10) -> List[dict]:
    """获取广播消息列表(盘前纪要/涨停简图/晚间公告等)"""
    data = _api("/v1/user/notice/broadcast", {"start": str(start), "limit": str(limit)}, session, cookie)
    if data.get("errCode") != "0":
        raise RuntimeError(f"获取广播列表失败: {data.get('msg')}")
    return data.get("data", {}).get("result", [])


def fetch_article_detail(article_id: str, session: str, cookie: str) -> dict:
    """获取文章详情"""
    data = _api("/v2/article/detail", {"is_read": "1", "article_id": article_id}, session, cookie)
    if data.get("errCode") != "0":
        raise RuntimeError(f"获取文章详情失败: {data.get('msg')}")
    return data.get("data", {})


def fetch_industry_list(session: str, cookie: str, keyword: str = "", limit: int = 15) -> List[dict]:
    """获取题材库列表"""
    data = _api("/v1/industry/list", {"keyword": keyword, "limit": str(limit), "start": "1"}, session, cookie)
    if data.get("errCode") != "0":
        raise RuntimeError(f"获取题材列表失败: {data.get('msg')}")
    return data.get("data", {}).get("result", [])


def fetch_product_articles(session: str, cookie: str, product_id: str = "1", start: int = 1, limit: int = 15) -> List[dict]:
    """获取产品文章列表(如逻辑红宝书每日精选)"""
    data = _api("/v1/product/article/list", {"product_id": product_id, "start": str(start), "limit": str(limit)}, session, cookie)
    if data.get("errCode") != "0":
        raise RuntimeError(f"获取产品文章失败: {data.get('msg')}")
    return data.get("data", {}).get("result", [])


def fetch_action_diagram_url(session: str, cookie: str, action_date: str) -> str:
    """获取指定日期的涨停简图 URL"""
    data = _api("/v1/action/diagram-url", {"date": action_date}, session, cookie)
    if data.get("errCode") != "0":
        return ""
    return data.get("data", "")


def fetch_timeline_list(session: str, cookie: str, year: str = None) -> List[dict]:
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
def fetch_premarket_content(session: str, cookie: str) -> List[dict]:
    """抓取盘前相关的内容(盘前纪要 + 投票)"""
    articles = fetch_broadcast_list(session, cookie, start=1, limit=20)
    results = []
    for a in articles:
        title = a.get("title", "")
        target_id = a.get("target_id", "")
        create_time = a.get("create_time", "")
        if "盘前" in title and target_id:
            try:
                detail = fetch_article_detail(target_id, session, cookie)
                results.append(detail)
            except Exception as e:
                logger.warning(f"抓取盘前纪要 {target_id} 失败: {e}")
    return results


def fetch_review_content(session: str, cookie: str) -> List[dict]:
    """抓取复盘相关的内容(复盘 + 涨停简图 + 晚间公告等)"""
    articles = fetch_broadcast_list(session, cookie, start=1, limit=20)
    results = []
    for a in articles:
        title = a.get("title", "")
        target_id = a.get("target_id", "")
        if any(kw in title for kw in ["复盘", "涨停简图", "晚间公告", "内容精选", "精选"]) and target_id:
            try:
                detail = fetch_article_detail(target_id, session, cookie)
                results.append(detail)
            except Exception as e:
                logger.warning(f"抓取复盘内容 {target_id} 失败: {e}")
    return results


def run_fetcher(phone: str, password: str, fetch_type: str = "all") -> dict:
    """
    完整抓取流程。fetch_type: 'premarket' | 'review' | 'all'
    返回 {'premarket': [...], 'review': [...], 'industry': [...], 'digest': [...]}
    """
    token, session, cookie = login(phone, password)

    result = {}

    if fetch_type in ("premarket", "all"):
        logger.info("抓取盘前内容...")
        result["premarket"] = fetch_premarket_content(session, cookie)

    if fetch_type in ("review", "all"):
        logger.info("抓取复盘内容...")
        result["review"] = fetch_review_content(session, cookie)
        logger.info("抓取涨停简图 URL...")
        today = datetime.now().strftime("%Y-%m-%d")
        result["diagram_url"] = fetch_action_diagram_url(session, cookie, today)

    if fetch_type in ("all",):
        logger.info("抓取题材库...")
        result["industry"] = fetch_industry_list(session, cookie)
        logger.info("抓取逻辑红宝书...")
        result["digest"] = fetch_product_articles(session, cookie)

    return result


if __name__ == "__main__":
    import pprint

    phone = "18020745991"
    password = "Am112211"

    print("=== 登录 ===")
    token, session, cookie = login(phone, password)
    print("登录成功")

    print("\n=== 广播列表(最近10条) ===")
    articles = fetch_broadcast_list(session, cookie, start=1, limit=10)
    for a in articles:
        print(f"  [{a['target_id']}] {a['create_time'][:10]} | {a['title'][:50]}")

    print("\n=== 抓取今日涨停简图 ===")
    today = datetime.now().strftime("%Y-%m-%d")
    for a in articles:
        if "涨停简图" in a.get("title", ""):
            url = fetch_action_diagram_url(session, cookie, a['create_time'][:10])
            print(f"  {a['title']} ({a['create_time'][:10]}): {url}")

    print("\n=== 盘前纪要详情 ===")
    for a in articles:
        if "盘前纪要" in a.get("title", ""):
            detail = fetch_article_detail(a["target_id"], token, cookie)
            print(f"标题: {detail.get('title')}")
            content = strip_html(detail.get("content", ""))[:300]
            print(f"内容: {content}...")
            break

    print("\n=== 题材库(最新3条) ===")
    industries = fetch_industry_list(session, cookie, limit=3)
    for ind in industries:
        print(f"  {ind.get('title', '')[:60]}")

    print("\n=== 逻辑红宝书最新文章 ===")
    digest = fetch_product_articles(session, cookie, limit=3)
    for d in digest:
        print(f"  {d.get('create_time', '')[:10]} | {d.get('title', '')[:50]}")
        print(f"  摘要: {d.get('describe', '')[:100]}")
