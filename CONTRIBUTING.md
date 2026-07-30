# Contributing to IELTS Codex

感谢你愿意改进 IELTS Codex。欢迎修复缺陷、改善终端体验、扩充经过校对的词条，或完善测试与文档。

## 开发环境

项目仅依赖 Python 3.10 或更高版本：

```bash
git clone https://github.com/Miracle-0v0/ielts-codex-cli.git
cd ielts-codex-cli
python3 ielts.py
```

运行测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## 提交 Pull Request

1. 从 `main` 创建一个范围明确的分支。
2. 保持零运行时依赖，除非新依赖带来的价值经过充分说明。
3. 行为变化需要补充或更新测试。
4. 新词条必须包含准确的音标、词性、中英释义、双语例句、近义词、主题和分数段。
5. 确保全部测试通过，并在 Pull Request 中说明变更目的和验证方式。

请勿提交个人学习进度、虚拟环境、构建产物或凭据。

## 报告问题

提交 Issue 时请提供操作系统、Python 版本、复现命令、实际输出和预期行为。涉及安全或隐私的信息请不要放入公开 Issue。
