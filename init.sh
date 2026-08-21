#!/usr/bin/env bash
# init.sh — 标准启动与验证入口
# 来源:Learn Harness Engineering 第 6 讲
# 用法:每轮会话开始时运行 ./init.sh
# 幂等:无论跑多少次结果一样,失败时重跑也安全

set -euo pipefail

echo "========================================"
echo "  init.sh — agent-memory-exporter"
echo "========================================"
echo ""

# ---------- 1. 确认目录 ----------
echo "[1/6] 确认工作目录..."
pwd
if [ ! -f "AGENTS.md" ]; then
  echo "  ❌ 未找到 AGENTS.md,可能不在仓库根目录"
  exit 1
fi
echo "  ✓ 在仓库根目录"
echo ""

# ---------- 2. 安装依赖 ----------
echo "[2/6] 安装依赖..."
uv sync --all-packages
echo "  ✓ 依赖已安装"
echo ""

# ---------- 3. 基础验证 ----------
echo "[3/6] 运行测试..."
uv run pytest tests/ -q
echo "  ✓ 测试通过"
echo ""

# ---------- 4. Lint 检查 ----------
echo "[4/6] Python 导入检查..."
uv run python -c "from agent_memory_exporter.cli import main; from agent_memory_shared.models import RawSession; print('imports OK')"
echo ""

# ---------- 5. 状态快照 ----------
echo "[5/6] 状态快照..."
echo "  Git HEAD:    $(git rev-parse --short HEAD 2>/dev/null || echo 'not a git repo')"
echo "  Branch:      $(git branch --show-current 2>/dev/null || echo 'N/A')"
echo "  Uncommitted: $(git status --porcelain 2>/dev/null | wc -l) files"
echo ""

# ---------- 6. 完成 ----------
echo "[6/6] init.sh 完成"
echo ""
echo "下一步:"
echo "  1. 读 claude-progress.md 了解当前状态"
echo "  2. 读 feature_list.json 选择下一个未完成功能"
echo "  3. 只围绕一个功能工作,直到它通过验证"
