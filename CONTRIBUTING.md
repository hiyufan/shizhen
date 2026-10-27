# 贡献指南 · Contributing

[简体中文](#简体中文) · [English](#english)

## 简体中文

欢迎提交问题、改进解析器、补充测试、翻译与文档。

1. 先[搜索已有 Issue](https://github.com/hiyufan/shizhen/issues)。报告问题时附可复现的公开链接、预期结果、错误提示与部署方式；日志中移除 Cookie、令牌和其他凭证。
2. 按[开发说明](docs/development.md#贡献)准备环境，阅读 [AGENTS.md](AGENTS.md)，沿用目标模块与相邻测试的约定。账号助手另见[子项目开发约定](apps/douyin-inbox/AGENTS.md)。
3. 按[验证检查](docs/development.md#验证检查)运行受影响的测试与对应检查。解析、下载、转换行为还需真实链接和三种格式的烟测，离线结果与平台实测分别报告。
4. 一次 PR 围绕一个目的，说明问题、改动后的行为与验证结果；提交信息遵循 [Conventional Commits 约定](AGENTS.md#提交信息)。

修改介绍时同步 [README.md](README.md) 与 [README.en.md](README.en.md)。首页保留用途、演示、入门与帮助入口，完整配置和 API 放在对应文档；功能与限制应与实现一致。

## English

Contributions to parsers, tests, translations and documentation are welcome.

1. [Search existing issues](https://github.com/hiyufan/shizhen/issues) first. For bugs, include a reproducible public link, expected behavior, the error message and your deployment method. Remove cookies, tokens and other credentials from logs.
2. Follow the [development guide](docs/development.en.md#contributing) and read [AGENTS.md](AGENTS.md) (Chinese). Follow conventions in the affected module and nearby tests. The account assistant has [additional guidance](apps/douyin-inbox/AGENTS.md) (Chinese).
3. Run the relevant [checks](docs/development.en.md#checks). Changes to parsing, downloads or conversion also need real-link smoke tests and all three conversion formats; report offline and real-platform results separately.
4. Keep each PR focused on one purpose. Describe the problem, resulting behavior and validation. Follow the repository's [Conventional Commits format](AGENTS.md#提交信息) (Chinese).

Keep [README.md](README.md) and [README.en.md](README.en.md) in sync. The README introduces the product, demonstrates it, and links to setup and support. Keep full configuration and API references in their dedicated guides, and ensure claims and limitations match the implementation.
