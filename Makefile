# Makefile — 标准化操作命令
# 来源:Learn Harness Engineering 第 2、6 讲
# AGENTS.md 中引用的所有命令都应该在这里有对应 target

.PHONY: setup dev test lint check clean

# 安装依赖、配置环境
setup:
	uv sync --all-packages
	@echo "✓ 依赖已安装"

# 启动开发服务器（本项目是 CLI，没有 dev server，这里做 smoke test）
dev:
	uv run agent-memory-exporter export --source workbuddy --limit 1 --output ./agent_export_smoke
	@echo "✓ Smoke test 通过（导出 1 个会话）"

# 运行测试
test:
	uv run pytest tests/ -v

# 代码风格检查
lint:
	uv run python -c "import py_compile, glob; [py_compile.compile(f, doraise=True) for f in glob.glob('packages/**/*.py', recursive=True)]; print('✓ 语法检查通过')"

# 完整验证 = 测试 + lint
# 这是 AGENTS.md 里引用的"标准验证路径"
check: test lint
	@echo "✓ 完整验证通过"

# 清理临时文件
clean:
	rm -rf agent_export agent_export_test agent_export_test2 agent_export_smoke .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "✓ 临时文件已清理"
