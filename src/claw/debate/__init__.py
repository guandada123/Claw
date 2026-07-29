"""北辰多智能体辩论模块 (src/claw/debate)

三环辩论流程：
1. Stance  — 7专家并行分析，各自输出 BUY/HOLD/SELL + 置信度 + 理由
2. Peer Review — 互相对立观点提出质疑
3. Convergence — 加权投票 + 综合判决

用法：
    from claw.debate import run_debate
    result = run_debate("000333", "美的集团", data)
"""

try:
    from claw.debate.debate_engine import batch_debate, run_debate
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
    from claw.debate.debate_engine import batch_debate, run_debate

__all__ = ["run_debate", "batch_debate"]
