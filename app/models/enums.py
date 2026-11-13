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
    """询价任务状态（draft→sent 为人工路径，其余为自动谈判路径）。"""

    DRAFT = "draft"              # 话术已生成，待发送
    SENDING = "sending"          # 智能体正在打开会话并发送
    SENT = "sent"                # 已发送，待卖家回复
    FOLLOWING_UP = "following_up"  # 多轮追问中（信息没问全）
    REPLIED = "replied"          # 有回复，待解析/确认
    QUOTED = "quoted"            # 已解析出完整报价
    NEGOTIATING = "negotiating"  # 议价中（已有报价，正在压价）
    ESCALATED = "escalated"      # 转人工（风控/异常/卖家拒答）
    CLOSED = "closed"            # 结束（无回复/放弃）
    EXPIRED = "expired"          # 超时未回，自动关闭


class InquiryStage(str, Enum):
    """会话所处阶段：先问全信息，再谈价格。"""

    INQUIRY = "inquiry"    # 询价：把所有必问项问清楚
    BARGAIN = "bargain"    # 议价：拿着市场最低价回头压价


class MsgRole(str, Enum):
    """会话消息的发出方（system 记录智能体自己的动作与判定）。"""

    BUYER = "buyer"      # 我方（智能体替买家发）
    SELLER = "seller"    # 卖家
    SYSTEM = "system"    # 系统事件（发送成功/解析结果/转人工原因）


class ChatChannel(str, Enum):
    """会话执行通道。"""

    CDP = "cdp"      # 接管真实浏览器里的旺旺
    MOCK = "mock"    # 离线模拟（演示与回归测试）


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


class StockStatus(str, Enum):
    """现货状态（卖家履约能力）。"""

    IN_STOCK = "in_stock"      # 现货
    PRE_SALE = "pre_sale"      # 预售/期货
    CUSTOM = "custom"          # 定制（按单生产）
    UNKNOWN = "unknown"