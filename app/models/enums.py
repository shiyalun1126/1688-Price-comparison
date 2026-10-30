"""枚举类型定义。"""
from enum import Enum


class MatchTier(str, Enum):
    """匹配分层：完全同款 vs 等效替代。

    - exact：已通过属性/询价核验确认是同版货。
    - equivalent：等效替代，未核验到同版货级别，必须对用户声明。
    """

    EXACT = "exact"
    EQUIVALENT = "equivalent"


class MatchStatus(str, Enum):
    """单个候选商品与目标款的匹配核验状态。"""

    CANDIDATE = "candidate"      # 仅图相似召回，未核验
    VERIFIED_SAME = "verified_same"      # 已核验为同版货
    VERIFIED_EQUIVALENT = "verified_equivalent"  # 已核验为等效替代
    REJECTED = "rejected"        # 核验后排除（不同货）


class InquiryStatus(str, Enum):
    """询价任务状态。"""

    DRAFT = "draft"          # 话术已生成，待人工发送
    SENT = "sent"            # 已发送，待卖家回复
    REPLIED = "replied"      # 有回复，待解析/确认
    QUOTED = "quoted"        # 已解析出报价
    CLOSED = "closed"        # 结束（无回复/放弃）


class QuoteSource(str, Enum):
    """报价来源。"""

    INQUIRY = "inquiry"        # 询价聊天得来
    MANUAL = "manual"          # 手工录入/截图整理
    API = "api"                  # 1688 API 公开标价


class TaxStatus(str, Enum):
    """含税状态。"""

    INCLUDED = "included"
    EXCLUDED = "excluded"
    UNKNOWN = "unknown"