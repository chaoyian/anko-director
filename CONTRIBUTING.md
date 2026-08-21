# Contributing / 贡献指南

Thank you for helping improve Anko Director. 感谢你帮助改进 Anko Director。

## Before opening a change / 提交前

1. Search existing issues and describe the user-visible problem before proposing a large change.
2. Keep local adjudication, deterministic randomness, and adapter isolation intact.
3. Never include API keys, Discord tokens, personal campaign databases, logs, or exported saves.
4. For schema changes, include migration tests for both fresh and existing databases.

大型改动请先搜索或创建 Issue；不要提交密钥、Token、真实存档或日志。数据库变更必须同时提供新建与升级测试。

## Development / 开发

```bash
uv sync --extra dev --no-editable
uv run --no-editable ruff check src tests scripts
uv run --no-editable pytest
uv run --no-editable python scripts/check_version.py
```

Pull requests should be focused, include tests, and update the bilingual documentation when behavior changes. PR 请保持范围清晰、补充测试，并在行为变化时同步更新中英文文档。

## License / 许可

By contributing, you agree that your contribution is provided under the repository's [PolyForm Noncommercial License 1.0.0](LICENSE). This repository is source-available and is not OSI-approved open source.

提交贡献即表示你同意贡献内容按本仓库的 PolyForm Noncommercial 1.0.0 许可提供；该许可不是 OSI 开源许可。
