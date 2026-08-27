# tests_meta — 按需运行的元测试

本目录存放**不验证 phone_agent 生产行为**的元测试（meta tests），因此不参与默认套件
`pytest tests/` 的收集。它们断言的是仓库文档、技能规则、打包元数据等工程层面内容，
以及 phase0 遗留的 characterization 契约，按需运行时才执行。

包含：

| 路径 | 内容 |
|---|---|
| `test_packaging_docs.py` | setup.py 依赖、README 拓扑、future-roadmap 断言 |
| `test_live_diagnosis_rules.py` | live-diagnosis 技能脚本的规则回归 |
| `characterization/` | phase0 遗留的严格契约测试 |

## 按需运行方式

```bash
# 在仓库根目录，使用项目虚拟环境
.venv/bin/python -m pytest tests_meta/ -q

# 仅运行某一部分
.venv/bin/python -m pytest tests_meta/test_live_diagnosis_rules.py -q
```

> 注意：`tests_meta` 中文件对仓库根的相对路径（`Path(__file__).resolve().parents[2]`
> 等）与迁移前保持一致，因为 `tests_meta/` 与 `tests/` 位于仓库根同一层级。
