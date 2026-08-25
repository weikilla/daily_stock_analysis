# -*- coding: utf-8 -*-
"""
Unit tests for src.services.jiuyangongshe_service module.

Covers: models, strip_html, format_for_push, date-filtering logic,
fetch_premarket_content (with mock), fetch_concept_content (with mock),
analyze_with_llm (with mock), and fetch_all error-handling.
Requires pytest. Run from project root:
    pytest tests/test_jiuyangongshe_service.py -v
"""

import json
import os
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.jiuyangongshe_service import (
    JiuyangongsheArticle,
    JiuyangongsheFetchResult,
    JiuyangongsheService,
    _build_headers,
    _api,
)


# =============================================================================
# Helpers
# =============================================================================

def make_article(
    article_id="aid1",
    title="4月30日盘前纪要",
    content="这是正文内容。",
    create_time="2026-04-30 07:32:20",
    article_type="premarket",
    stocks=None,
):
    return JiuyangongsheArticle(
        article_id=article_id,
        title=title,
        content=content,
        create_time=create_time,
        article_type=article_type,
        stocks=stocks or [],
    )


def mock_homepage_response(articles):
    """返回一个模拟的主页 API 响应结构."""
    return {
        "errCode": "0",
        "msg": "success",
        "data": {
            "result": articles,
        },
    }


def mock_detail_response(content="<p>正文HTML内容</p>"):
    return {
        "errCode": "0",
        "msg": "success",
        "data": {
            "content": content,
            "title": "测试文章",
            "create_time": "2026-04-30 07:32:20",
            "stock_list": [],
        },
    }


# =============================================================================
# Model tests
# =============================================================================

class TestJiuyangongsheArticle(unittest.TestCase):
    def test_default_fields(self):
        a = JiuyangongsheArticle(article_id="x", title="y")
        self.assertEqual(a.article_id, "x")
        self.assertEqual(a.title, "y")
        self.assertEqual(a.stocks, [])
        self.assertEqual(a.image_urls, [])
        self.assertEqual(a.content, "")
        self.assertEqual(a.create_time, "")

    def test_stocks_initialized_to_empty_list(self):
        a = JiuyangongsheArticle(article_id="x", title="y", stocks=None)
        self.assertEqual(a.stocks, [])

    def test_image_urls_initialized_to_empty_list(self):
        a = JiuyangongsheArticle(article_id="x", title="y", image_urls=None)
        self.assertEqual(a.image_urls, [])


class TestJiuyangongsheFetchResult(unittest.TestCase):
    def test_defaults(self):
        r = JiuyangongsheFetchResult()
        self.assertTrue(r.success)
        self.assertEqual(r.premarket, [])
        self.assertEqual(r.review, [])
        self.assertEqual(r.concept, [])
        self.assertEqual(r.industry, [])
        self.assertEqual(r.digest, [])

    def test_with_data(self):
        a = make_article()
        r = JiuyangongsheFetchResult(success=True, premarket=[a])
        self.assertEqual(len(r.premarket), 1)
        self.assertEqual(r.premarket[0].article_id, "aid1")


# =============================================================================
# Utility tests
# =============================================================================

class TestStripHtml(unittest.TestCase):
    def test_removes_simple_tags(self):
        self.assertEqual(
            JiuyangongsheService.strip_html("<p>hello</p>"),
            "hello",
        )

    def test_removes_nested_tags(self):
        html = '<div><p>text</p><img src="x"/></div>'
        self.assertEqual(JiuyangongsheService.strip_html(html), "text")

    def test_strips_whitespace(self):
        self.assertEqual(
            JiuyangongsheService.strip_html("  <p>  hello  </p>  "),
            "hello",
        )

    def test_empty_string(self):
        self.assertEqual(JiuyangongsheService.strip_html(""), "")


class TestBuildHeaders(unittest.TestCase):
    def test_includes_required_fields(self):
        h = _build_headers("token123", "1234567890")
        self.assertEqual(h["token"], "token123")
        self.assertEqual(h["timestamp"], "1234567890")
        self.assertEqual(h["Content-Type"], "application/json")
        self.assertEqual(h["platform"], "1")


# =============================================================================
# format_for_push tests
# =============================================================================

class TestFormatForPush(unittest.TestCase):
    def _make_result(self, articles, diagram_url=""):
        r = JiuyangongsheFetchResult()
        r.premarket = articles
        return r

    def test_premarket_no_articles(self):
        svc = JiuyangongsheService()
        result = self._make_result([])
        out = svc.format_for_push(result, push_type="premarket")
        self.assertIn("暂无盘前纪要", out)

    def test_premarket_with_today_article(self):
        svc = JiuyangongsheService()
        a = make_article(
            title="4月30日盘前纪要",
            content="今日看好风电板块",
        )
        result = self._make_result([a])
        out = svc.format_for_push(result, push_type="premarket")
        self.assertIn("4月30日盘前纪要", out)
        self.assertIn("今日看好风电板块", out)

    def test_premarket_skips_old_articles(self):
        svc = JiuyangongsheService()
        # articles list contains an old article (29日) — should be skipped
        old = make_article(title="4月29日盘前纪要", content="旧文")
        today = make_article(title="4月30日盘前纪要", content="今日内容")
        result = self._make_result([old, today])
        out = svc.format_for_push(result, push_type="premarket")
        # 29日文章的内容不应出现
        self.assertNotIn("旧文", out)
        self.assertIn("今日内容", out)

    def test_premarket_empty_content_no_crash(self):
        svc = JiuyangongsheService()
        a = make_article(title="4月30日盘前纪要", content="")
        result = self._make_result([a])
        out = svc.format_for_push(result, push_type="premarket")
        self.assertIn("4月30日盘前纪要", out)  # still shows title

    def test_review_no_articles(self):
        svc = JiuyangongsheService()
        r = JiuyangongsheFetchResult()
        r.review = []
        out = svc.format_for_push(r, push_type="review")
        self.assertIn("暂无复盘内容", out)

    def test_review_with_today_article(self):
        svc = JiuyangongsheService()
        a = make_article(
            article_type="review",
            title="复盘",
            content="今日光伏强势",
            create_time=datetime.now().strftime("%Y-%m-%d") + " 18:00:00",
        )
        r = JiuyangongsheFetchResult()
        r.review = [a]
        out = svc.format_for_push(r, push_type="review")
        self.assertIn("今日光伏强势", out)

    def test_review_diagram_url(self):
        svc = JiuyangongsheService()
        r = JiuyangongsheFetchResult()
        r.review = []
        r.diagram_url = "http://example.com/diagram.png"
        out = svc.format_for_push(r, push_type="review")
        self.assertIn("diagram.png", out)


# =============================================================================
# fetch_premarket_content tests
# =============================================================================

class TestFetchPremarketContent(unittest.TestCase):
    """Test the retry + date-filtering logic with mocked API responses."""

    def _mock_svc(self, homepage_data):
        """Return a JiuyangongsheService with mocked fetch_user_homepage_articles."""
        svc = JiuyangongsheService.__new__(JiuyangongsheService)
        svc.config = mock.MagicMock()
        svc.db = mock.MagicMock()
        svc.fetch_user_homepage_articles = mock.MagicMock(return_value=homepage_data)
        svc.fetch_article_detail = mock.MagicMock(return_value=mock_detail_response())
        return svc

    def test_returns_empty_when_api_returns_nothing(self):
        svc = self._mock_svc({"data": {"result": []}})
        result = svc.fetch_premarket_content("token", "cookie")
        self.assertEqual(result, [])

    def test_filters_out_old_articles_by_title(self):
        """Articles whose title doesn't match today's date should be skipped."""
        svc = self._mock_svc(
            mock_homepage_response([
                {
                    "article_id": "old1",
                    "title": "4月28日盘前纪要",
                    "create_time": "2026-04-28 07:30:00",
                    "content": "旧文内容",
                    "stock_list": [],
                },
            ])
        )
        result = svc.fetch_premarket_content("token", "cookie")
        self.assertEqual(result, [])

    def test_returns_today_article_and_stops(self):
        """Should return the first today's article and stop after one."""
        svc = self._mock_svc(
            mock_homepage_response([
                {
                    "article_id": "today1",
                    "title": "4月30日盘前纪要",
                    "create_time": "2026-04-30 07:32:20",
                    "content": "今日内容",
                    "stock_list": [],
                },
            ])
        )
        result = svc.fetch_premarket_content("token", "cookie")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].article_id, "today1")
        self.assertEqual(result[0].title, "4月30日盘前纪要")
        # fetch_article_detail should be called
        svc.fetch_article_detail.assert_called_once()

    def test_retries_with_time_filter_on_empty_first_attempt(self):
        """
        First API call (time_filter='') returns no match.
        Second call (time_filter='YYYY-MM-DD') returns today's article.
        Should return the article from the second attempt.
        """
        svc = JiuyangongsheService.__new__(JiuyangongsheService)
        svc.config = mock.MagicMock()
        svc.db = mock.MagicMock()

        calls = []
        detail_calls = []

        def mock_homepage(token, cookie, **kwargs):
            calls.append(kwargs.get("time_filter"))
            # attempt 0: empty result
            if len(calls) == 1:
                return {"data": {"result": []}}
            # attempt 1: returns today's article
            return mock_homepage_response([
                {
                    "article_id": "today_retry",
                    "title": "4月30日盘前纪要",
                    "create_time": "2026-04-30 07:32:20",
                    "content": "重试成功",
                    "stock_list": [],
                },
            ])

        def mock_detail(token, cookie, article_id):
            detail_calls.append(article_id)
            return mock_detail_response(content="<p>重试成功的正文</p>")

        svc.fetch_user_homepage_articles = mock_homepage
        svc.fetch_article_detail = mock_detail

        result = svc.fetch_premarket_content("token", "cookie")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].article_id, "today_retry")
        # Should have called homepage twice (empty then success)
        self.assertEqual(calls, ["", "2026-04-30"])
        # Second attempt's article should have detail fetched
        self.assertIn("today_retry", detail_calls)

    def test_limit_is_20(self):
        """Verify the code uses limit=20 (not 10) for the homepage API."""
        svc = JiuyangongsheService.__new__(JiuyangongsheService)
        svc.config = mock.MagicMock()
        svc.db = mock.MagicMock()

        captured_limits = []

        def mock_homepage(token, cookie, **kwargs):
            captured_limits.append(kwargs.get("limit"))
            return mock_homepage_response([])

        svc.fetch_user_homepage_articles = mock_homepage
        svc.fetch_premarket_content("token", "cookie")

        # Both attempts should use limit="20"
        self.assertEqual(captured_limits, ["20", "20"])


# =============================================================================
# fetch_concept_content tests
# =============================================================================

class TestFetchConceptContent(unittest.TestCase):
    def _mock_svc(self, homepage_data):
        svc = JiuyangongsheService.__new__(JiuyangongsheService)
        svc.config = mock.MagicMock()
        svc.db = mock.MagicMock()
        svc.fetch_user_homepage_articles = mock.MagicMock(return_value=homepage_data)
        svc.fetch_article_detail = mock.MagicMock(return_value=mock_detail_response())
        return svc

    def test_skips_articles_not_from_today(self):
        yesterday = "2026-04-29 08:00:00"
        svc = self._mock_svc(
            mock_homepage_response([
                {
                    "article_id": "old_concept",
                    "title": "储能板块前瞻",
                    "create_time": yesterday,
                    "content": "旧内容",
                    "stock_list": [],
                },
            ])
        )
        result = svc.fetch_concept_content("token", "cookie")
        self.assertEqual(result, [])

    def test_returns_today_concept_articles(self):
        today = datetime.now().strftime("%Y-%m-%d") + " 09:00:00"
        svc = self._mock_svc(
            mock_homepage_response([
                {
                    "article_id": "concept1",
                    "title": "储能板块前瞻",
                    "create_time": today,
                    "content": "今日储能逻辑",
                    "stock_list": [{"name": "宁德时代", "code": "300750"}],
                },
            ])
        )
        result = svc.fetch_concept_content("token", "cookie")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].article_id, "concept1")
        self.assertEqual(result[0].article_type, "concept")
        self.assertEqual(result[0].stocks[0]["name"], "宁德时代")

    def test_max_5_articles(self):
        today = datetime.now().strftime("%Y-%m-%d") + " 09:00:00"
        articles = [
            {
                "article_id": f"c{i}",
                "title": f"题材{i}",
                "create_time": today,
                "content": f"内容{i}",
                "stock_list": [],
            }
            for i in range(8)
        ]
        svc = self._mock_svc(mock_homepage_response(articles))
        result = svc.fetch_concept_content("token", "cookie")
        self.assertEqual(len(result), 5)

    def test_extracts_image_urls_from_html(self):
        today = datetime.now().strftime("%Y-%m-%d") + " 09:00:00"
        html_with_img = '<p>文字</p><img src="http://example.com/pic.jpg"/><img src="http://example.com/pic2.png"/>'
        svc = self._mock_svc(
            mock_homepage_response([
                {
                    "article_id": "with_img",
                    "title": "配图题材",
                    "create_time": today,
                    "content": "内容",
                    "stock_list": [],
                },
            ])
        )
        svc.fetch_article_detail = mock.MagicMock(
            return_value=mock_detail_response(content=html_with_img)
        )
        result = svc.fetch_concept_content("token", "cookie")
        self.assertIn("http://example.com/pic.jpg", result[0].image_urls)
        self.assertIn("http://example.com/pic2.png", result[0].image_urls)


# =============================================================================
# fetch_all error-handling tests
# =============================================================================

class TestFetchAll(unittest.TestCase):
    @mock.patch.object(JiuyangongsheService, "refresh_token")
    def test_login_failure_returns_error_result(self, mock_refresh):
        mock_refresh.side_effect = RuntimeError("登录失败: invalid credentials")

        svc = JiuyangongsheService()
        result = svc.fetch_all()

        self.assertFalse(result.success)
        self.assertIn("登录失败", result.error)

    @mock.patch.object(JiuyangongsheService, "fetch_premarket_content")
    @mock.patch.object(JiuyangongsheService, "refresh_token")
    def test_premarket_exception_returns_error_result(self, mock_refresh, mock_pm):
        mock_refresh.return_value = ("token", "cookie")
        mock_pm.side_effect = Exception("网络超时")

        svc = JiuyangongsheService()
        result = svc.fetch_all()

        self.assertFalse(result.success)
        self.assertIn("网络超时", result.error)


# =============================================================================
# analyze_with_llm tests
# =============================================================================

class TestAnalyzeWithLLm(unittest.TestCase):
    def _result_with_article(self, article_type="premarket"):
        r = JiuyangongsheFetchResult()
        today = datetime.now().strftime("%Y-%m-%d")
        article = make_article(
            article_type=article_type,
            title=("4月30日盘前纪要" if article_type == "premarket" else "复盘"),
            content="这是一段测试正文内容。",
            create_time=today + " 07:32:20",
        )
        if article_type == "premarket":
            r.premarket = [article]
        else:
            r.review = [article]
        return r

    def test_returns_none_when_no_articles(self):
        svc = JiuyangongsheService()
        result = JiuyangongsheFetchResult()
        out = svc.analyze_with_llm(result, push_type="premarket")
        self.assertIsNone(out)

    def test_returns_none_when_today_article_not_found(self):
        svc = JiuyangongsheService()
        # article is not today's
        a = make_article(title="4月28日盘前纪要", article_type="premarket", content="旧文")
        r = JiuyangongsheFetchResult()
        r.premarket = [a]
        out = svc.analyze_with_llm(r, push_type="premarket")
        self.assertIsNone(out)

    @mock.patch("src.analyzer.GeminiAnalyzer")
    @mock.patch("src.config.get_config")
    def test_calls_llm_with_correct_prompt_structure(self, mock_config, mock_analyzer_cls):
        mock_config.return_value.stock_list = []
        mock_analyzer = mock.MagicMock()
        mock_analyzer.generate_text.return_value = "分析完成"
        mock_analyzer_cls.return_value = mock_analyzer

        svc = JiuyangongsheService()
        result = self._result_with_article("premarket")
        out = svc.analyze_with_llm(result, push_type="premarket")

        # Check prompt contains expected sections
        call_args = mock_analyzer.generate_text.call_args
        full_prompt = call_args[1]["prompt"]  # keyword args

        self.assertIn("今日盘前要点", full_prompt)
        self.assertIn("核心题材", full_prompt)
        self.assertIn("自选股影响分析", full_prompt)
        self.assertIn("交易视角", full_prompt)
        self.assertIn("4月30日盘前纪要", full_prompt)
        self.assertEqual(out, "分析完成")

    @mock.patch("src.analyzer.GeminiAnalyzer")
    @mock.patch("src.config.get_config")
    def test_review_prompt_has_review_sections(self, mock_config, mock_analyzer_cls):
        mock_config.return_value.stock_list = []
        mock_analyzer = mock.MagicMock()
        mock_analyzer.generate_text.return_value = "复盘分析"
        mock_analyzer_cls.return_value = mock_analyzer

        svc = JiuyangongsheService()
        result = self._result_with_article("review")
        out = svc.analyze_with_llm(result, push_type="review")

        call_args = mock_analyzer.generate_text.call_args
        full_prompt = call_args[1]["prompt"]

        self.assertIn("今日复盘摘要", full_prompt)
        self.assertIn("今日龙头题材", full_prompt)
        self.assertIn("强势股点评", full_prompt)
        self.assertIn("后市展望", full_prompt)
        self.assertEqual(out, "复盘分析")

    @mock.patch("src.analyzer.GeminiAnalyzer")
    @mock.patch("src.config.get_config")
    def test_llm_failure_returns_none(self, mock_config, mock_analyzer_cls):
        mock_config.return_value.stock_list = []
        mock_analyzer = mock.MagicMock()
        mock_analyzer.generate_text.side_effect = Exception("模型调用失败")
        mock_analyzer_cls.return_value = mock_analyzer

        svc = JiuyangongsheService()
        result = self._result_with_article()
        out = svc.analyze_with_llm(result, push_type="premarket")
        self.assertIsNone(out)

    @mock.patch("src.analyzer.GeminiAnalyzer")
    @mock.patch("src.config.get_config")
    def test_watchlist_stocks_injected_into_prompt(self, mock_config, mock_analyzer_cls):
        mock_cfg = mock.MagicMock()
        mock_cfg.stock_list = ["000815", "600673"]
        mock_config.return_value = mock_cfg

        mock_analyzer = mock.MagicMock()
        mock_analyzer.generate_text.return_value = "ok"
        mock_analyzer_cls.return_value = mock_analyzer

        svc = JiuyangongsheService()
        result = self._result_with_article()
        svc.analyze_with_llm(result, push_type="premarket")

        prompt = mock_analyzer.generate_text.call_args[1]["prompt"]
        # 自选股代码应出现在 prompt 中
        self.assertIn("000815", prompt)
        self.assertIn("600673", prompt)


# =============================================================================
# main
# =============================================================================

if __name__ == "__main__":
    unittest.main()
