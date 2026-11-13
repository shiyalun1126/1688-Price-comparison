"""视觉核验失败的降级语义（无网络、秒级）。

背景：视觉调用失败时旧逻辑一律退回属性核验，而目标款 attributes 常为空，
于是拿到 0.0/0.3 的默认分被当成「已排除/等效替代」，把「没核验」伪装成结论，
污染比价榜单。这里锁住修正后的语义：
- 属性也无证据 → candidate（未核验）
- 属性有证据 → 仍按属性核验结论降级（保留原设计）

注意：本模块不用 pytest 的 tmp_path/tmp_path_factory——它走 tempfile.mkdtemp，
在本项目的 Windows 沙箱里创建出的目录无法被再次列出，会连累全部用例报
WinError 5。这里改用 pathlib.mkdir 自建目录并显式清理。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.matcher import matcher as matcher_module
from app.matcher.matcher import Matcher
from app.models.entities import Product, TargetStyle
from app.models.enums import MatchStatus


@pytest.fixture
def img_pair():
    """造两张「存在但内容不是图片」的文件：绕过文件缺失分支，逼视觉调用抛错。"""
    d = Path(__file__).resolve().parent / "_tmp_vision_fallback"
    d.mkdir(exist_ok=True)
    a, b = d / "a.jpg", d / "b.jpg"
    a.write_bytes(b"not-a-real-image")
    b.write_bytes(b"not-a-real-image")
    try:
        yield a, b
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _target(attrs: dict, img: Path) -> TargetStyle:
    return TargetStyle(id="t1", name="测试款", category="POLO衫",
                       attributes=attrs, image_paths=[str(img)])


def _product(attrs: dict, img: Path) -> Product:
    return Product(id="p1", target_style_id="t1", shop_id="s1", title="测试商品",
                   attributes=attrs, image_paths=[str(img)])


def _boom(*args, **kwargs):
    raise RuntimeError("视觉比对失败（模拟）: 503 pool_busy")


def test_vision_failure_without_attribute_evidence_stays_candidate(img_pair, monkeypatch):
    """视觉失败 + 无属性维度 → 未核验（candidate），不得判 rejected/equivalent。"""
    a, b = img_pair
    monkeypatch.setattr(matcher_module, "compare_images", _boom)
    out = Matcher().verify(_target({}, a), _product({}, b))
    assert out.status == MatchStatus.CANDIDATE
    assert out.confidence == 0.0
    assert "未完成核验" in out.reason


def test_vision_failure_without_common_dimension_stays_candidate(img_pair, monkeypatch):
    """双方各有属性但无交集 → 同样判未核验，而不是拿 0.0 分判已排除。"""
    a, b = img_pair
    monkeypatch.setattr(matcher_module, "compare_images", _boom)
    out = Matcher().verify(
        _target({"面料": "纯棉"}, a),
        _product({"型号": "A123"}, b),
    )
    assert out.status == MatchStatus.CANDIDATE
    assert "未完成核验" in out.reason


def test_vision_failure_with_attribute_evidence_keeps_fallback(img_pair, monkeypatch):
    """属性有共同维度时，失败降级仍走属性核验（不改变既有行为）。"""
    a, b = img_pair
    monkeypatch.setattr(matcher_module, "compare_images", _boom)
    out = Matcher().verify(
        _target({"面料": "纯棉", "克重": "280g"}, a),
        _product({"面料": "纯棉", "克重": "280g"}, b),
    )
    assert out.status == MatchStatus.VERIFIED_SAME
    assert "改用属性核验" in out.reason


def test_vision_concurrency_env_override(monkeypatch):
    """并发数可由 LLM_VISION_CONCURRENCY 覆盖，非法/空值退回默认。"""
    monkeypatch.delenv("LLM_VISION_CONCURRENCY", raising=False)
    assert matcher_module.vision_concurrency() == matcher_module.DEFAULT_MAX_WORKERS
    monkeypatch.setenv("LLM_VISION_CONCURRENCY", "6")
    assert matcher_module.vision_concurrency() == 6
    monkeypatch.setenv("LLM_VISION_CONCURRENCY", "abc")
    assert matcher_module.vision_concurrency() == matcher_module.DEFAULT_MAX_WORKERS
    monkeypatch.setenv("LLM_VISION_CONCURRENCY", "0")
    assert matcher_module.vision_concurrency() == matcher_module.DEFAULT_MAX_WORKERS


def test_retry_delay_honours_retry_after():
    """429/503 按 Retry-After 退避（有 2s 保底），其它错误线性退避。"""
    from app.matcher.vision import _retry_delay

    class _Resp:
        def __init__(self, code, headers):
            self.status_code = code
            self.headers = headers

    class _Err(Exception):
        def __init__(self, resp):
            self.response = resp

    assert _retry_delay(_Err(_Resp(503, {"retry-after": "2"})), 0) == 2.0
    assert _retry_delay(_Err(_Resp(503, {"retry-after": "2"})), 2) == 6.0
    # 无 Retry-After 也保底 2s，避免池满时立刻重试again 503
    assert _retry_delay(_Err(_Resp(429, {})), 0) == 2.0
    # 非限流错误：沿用线性退避
    assert _retry_delay(RuntimeError("boom"), 0) == 1.5
    assert _retry_delay(RuntimeError("boom"), 1) == 3.0
