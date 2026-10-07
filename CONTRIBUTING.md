# 参与贡献

SpecAgent 是作品集项目:Issue 和 PR 都欢迎,但响应不定期;大改动请先开 Issue 对齐方向。

## 本地开发

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -U pip
pip install -e ".[dev]"
python -m pytest -q          # 全量测试;浏览器测试在没有 playwright 的机器上会自动跳过
```

## 约定

1. **功能分支 + PR**,不要直接推 `main`。
2. **现有测试只能追加**,不要删改既有断言;`tests/test_generator.py` 不改。
3. 文档与 CHANGELOG 用中文(README.en.md 是唯一的英文文档);日志命名 `specagent.<area>`。
4. 判定逻辑(`app/judge.py`、`app/constraints.py`)保持**确定性**:LLM 只做起草/分诊/建议,永远不做裁判;Agent 层不允许触碰判定与门禁(有 AST 测试守护)。
5. 提交信息风格参考 `git log`;不把密钥、`.env`、本地数据库提交进仓库。
