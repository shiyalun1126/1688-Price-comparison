"""核心实体模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import (
    InquiryStatus,
    MatchStatus,
    MatchTier,
    QuoteSource,
    TaxStatus,
)


# ---------------------------------------------------------------------------
# 目标款档案
# ---------------------------------------------------------------------------
class TargetStyle(BaseModel):
    """目标款档案：比价围绕它展开。"""

    id: str
    name: str                          # 人工可读名称
    description: Optional[str] = None  # 文字描述（面料/克重/版型/尺码等）
    image_paths: list[str] = Field(default_factory=list)  # 目标款图片路径
    # 结构化属性（可后续用 LLM 从描述/图里抽取）
    attributes: dict[str, str] = Field(default_factory=dict)
    reference_price: Optional[float] = None   # 参考/预期价
    category: Optional[str] = None            # 服装细分类目
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# 门店 / 店铺
# ---------------------------------------------------------------------------
class Shop(BaseModel):
    """1688 店铺。"""

    id: str
    name: str
    aliww: Optional[str] = None        # 旺旺号
    url: Optional[str] = None          # 店铺链接
    location: Optional[str] = None     # 发货地
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 候选商品
# ---------------------------------------------------------------------------
class Product(BaseModel):
    """目标款召回的候选商品（挂在某店铺下）。"""

    id: str
    target_style_id: str
    shop_id: str
    title: str
    image_paths: list[str] = Field(default_factory=list)
    url: Optional[str] = None          # 1688 商品链接
    attributes: dict[str, str] = Field(default_factory=dict)
    listed_price: Optional[float] = None   # 公开标价
    moq: Optional[int] = None              # 起订量
    match_status: MatchStatus = MatchStatus.CANDIDATE
    match_tier: Optional[MatchTier] = None
    match_score: Optional[float] = None    # 匹配置信度 0~1
    match_reason: str = ""                 # 判定理由（用于增量匹配复用）
    matched_at: Optional[datetime] = None  # 上次判定时间
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# 询价任务
# ---------------------------------------------------------------------------
class InquiryTask(BaseModel):
    """一次对某店的询价（半自动：智能体出话术，人发送）。"""

    id: str
    target_style_id: str
    product_id: str
    shop_id: str
    script: str = ""                   # 智能体生成的询价话术
    status: InquiryStatus = InquiryStatus.DRAFT
    buyer_quantity: Optional[int] = None  # 拟采购量（影响话术）
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# 报价
# ---------------------------------------------------------------------------
class Quote(BaseModel):
    """一条报价，可比性由 6 个维度保证。"""

    id: str
    target_style_id: str
    product_id: str
    shop_id: str
    inquiry_id: Optional[str] = None   # 若来自询价
    source: QuoteSource = QuoteSource.MANUAL
    price: float                       # 单价（元）
    moq: Optional[int] = None          # 起订量
    tax: TaxStatus = TaxStatus.UNKNOWN
    includes_shipping: Optional[bool] = None
    tiers: list[dict] = Field(default_factory=list)  # 阶梯价 [{qty, price}]
    condition_note: str = ""           # 条件备注（口语化原文）
    raw_text: str = ""                 # 卖家回复原文（溯源）
    quoted_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# 比价结论
# ---------------------------------------------------------------------------
class ComparisonResult(BaseModel):
    """对某目标款的一份比价结论。"""

    target_style_id: str
    generated_at: datetime = Field(default_factory=datetime.now)
    rows: list[dict] = Field(default_factory=list)   # 归一化后的比价行
    recommendation: str = ""                          # 采购建议
    negotiation_script: str = ""                      # 议价话术
    summary: str = ""                                 # 综述