# -*- coding: utf-8 -*-
"""
LLM 配置模块 - 多 Agent 分析用
支持 DeepSeek（默认，国内直连，性价比高）
在项目根目录 .env 或环境变量中配置 DEEPSEEK_API_KEY
"""
import os

# 加载 .env 文件（如果存在）
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_REASONER_MODEL = os.environ.get("DEEPSEEK_REASONER_MODEL", "deepseek-reasoner")

# 多 Agent 分析开关
AGENT_ANALYSIS_ENABLED = bool(DEEPSEEK_API_KEY)

# 分析缓存目录
AGENT_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "agent_cache")

# 单次分析最大 token（防止报告截断）
AGENT_MAX_TOKENS = 4096


def is_enabled():
    """检查多 Agent 分析是否可用（有 API Key 才启用）"""
    return bool(DEEPSEEK_API_KEY)


def ensure_cache_dir():
    """确保缓存目录存在"""
    os.makedirs(AGENT_CACHE_DIR, exist_ok=True)
    return AGENT_CACHE_DIR
