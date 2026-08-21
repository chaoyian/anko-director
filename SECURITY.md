# Security Policy / 安全策略

## Supported versions / 支持版本

| Version | Supported |
|---|---|
| 0.7.x | Yes — development preview |
| < 0.7 | No |

## Reporting a vulnerability / 报告漏洞

Use GitHub's **Report a vulnerability** private-reporting flow for this repository. Do not open a public issue for an unpatched vulnerability, and never attach real API keys, Discord tokens, campaign databases, or exported saves.

请使用本仓库 GitHub Security 页面中的 **Report a vulnerability** 私密报告入口。未修复漏洞不要发布为公开 Issue，也不要上传真实 API Key、Discord Token、游戏数据库或导出存档。

Include affected versions, reproduction steps using synthetic data, expected impact, and any suggested mitigation. We will acknowledge a complete report, assess the affected versions, and coordinate disclosure after a fix is available.

报告应包含受影响版本、使用虚构数据的复现步骤、影响范围和可选修复建议。项目会在修复可用后协调公开披露。

## Deployment boundary / 部署边界

Anko Director is designed as a local loopback application. Binding it to a non-loopback interface without an authenticated reverse proxy is outside the supported security model.

Anko Director 按本地回环应用设计；在没有带认证的反向代理时绑定到非回环地址，不属于受支持的安全模型。
