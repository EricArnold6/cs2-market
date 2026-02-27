"""
兼容层：src.data → src.acquisition

所有符号从 src.acquisition 全量 re-export，保持旧 import 路径有效。
"""
from src.acquisition import *  # noqa: F401, F403
