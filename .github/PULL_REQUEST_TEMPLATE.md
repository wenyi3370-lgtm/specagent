## 改动说明

<!-- 做了什么,为什么 -->

## 测试

- [ ] `python -m pytest -q` 全量通过(现有测试只追加,不删改;`tests/test_generator.py` 不动)
- [ ] 新增行为有对应测试
- [ ] 文档/CHANGELOG 已同步(中文)

## 自查

- [ ] 没有提交密钥、`.env`、本地数据库或 `docs/handoff-*`
- [ ] 判定路径保持确定性(LLM 不参与 PASS/FAIL)
