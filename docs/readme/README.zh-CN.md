<!-- l10n-source: README.md · written: 2026-10-22 · method: AI-assisted summary, reviewed for technical accuracy -->

# CUGA

> 本文是[英文 README](../../README.md) 的中文简介，由 AI 辅助编写，内容以英文版为准。如发现表述不准确或不通顺之处，欢迎在 [Issues](https://github.com/cuga-project/cuga-agent/issues) 中反馈。
>
> This page is an AI-assisted Chinese summary of the [English README](../../README.md); the English version is authoritative. Corrections are welcome.

**企业级智能体运行框架（agent harness）：在自主可控的环境中，实现受策略约束的自动化。**

CUGA 可根据业务请求，在应用、API 和文档之间执行操作。你可以将它部署在私有云或离线环境中，通过细粒度的执行策略（policy）约束其行为，并在自有基础设施上使用开源模型。你还可以接入自己的工具、配置工作流，并针对不同工作负载优化推理配置。

**[快速开始](#快速开始)** · **[在中国大陆使用](#在中国大陆使用)** · **[文档（英文）](https://docs.cuga.dev)** · **[Discord](https://discord.gg/PN3Zdq4dEd)**

[Apache 2.0](../../LICENSE) · Configurable Generalist Agent（可配置通用智能体）

---

## CUGA 能做什么

| 你的关注点 | CUGA 提供的能力 |
| --- | --- |
| **自主可控** | 在你自己的环境中运行；接入私有部署的模型和工具；可为离线部署预先下载所需的辅助模型（如嵌入模型）；用于推理的大模型需自行部署推理服务。 |
| **执行策略** | 阻止不符合策略的意图（Intent Guard），调用指定工具前须经人工审批（Tool Approval），用 Playbook（标准操作流程）引导工作流，并配置工具级防护（ToolGuard）。 |
| **推理效率** | 选用开源模型，调整思考模式（reasoning mode），自动筛选相关工具，设置工具调用次数上限，并可启用 Run Receipt 查看每次运行的 token 用量。 |
| **快速交付** | 接入 MCP 服务器、OpenAPI 服务和 Python 工具；在管理界面中配置并测试智能体，确认无误后再发布版本。 |
| **定时与事件驱动自动化** | 通过单独部署的事件服务（events service），按定时计划、Webhook 或应用事件触发智能体运行，并在 Events Studio 中查看运行历史。 |
| **技能与经验复用** | 加载任务专用技能（Skills），并通过配置好的 Evolve 集成检索相关指导和用户偏好。 |
| **多智能体** | CugaSupervisor 可以将任务分派给本地 CUGA 智能体以及支持 A2A 的远程智能体。CUGA 也可以作为 MCP 服务器提供能力，或通过 A2A 被其他智能体调用；此外，还可以通过 ACP（Agent Client Protocol）与 Zed 等编辑器集成。 |

## 快速开始

需要 **Python 3.12** 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

```bash
git clone https://github.com/cuga-project/cuga-agent.git
cd cuga-agent
uv sync --python 3.12
test -f .env || cp .env.example .env
```

在 `.env` 中设置模型提供商、模型名称和 API Key（参见英文文档：[模型配置](configuration-guide.md#supported-platforms)），然后启动：

```bash
uv run cuga start manager --crm
```

在浏览器中打开终端中显示的本地地址，进入 `/manage` 页面，即可试用 CRM 工具、配置策略，并在发布前测试草稿配置。

也可以作为 Python 库使用（运行前请先按上文配置模型）：

```python
# pip install cuga
import asyncio
from cuga import CugaAgent
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """返回指定城市的当前天气。"""
    return f"{city}：晴，24°C"

agent = CugaAgent(tools=[get_weather])

async def main():
    result = await agent.invoke("北京现在天气怎么样？")
    print(result.answer)

asyncio.run(main())
```

## 在中国大陆使用

CUGA 无需依赖 OpenAI 官方托管服务，可接入兼容 OpenAI API 的模型服务，例如阿里云百炼（千问 Qwen）。

**1. 使用国内 PyPI 镜像加速安装**（以[清华大学 TUNA 镜像](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)为例）：

```bash
# pip
pip install cuga -i https://pypi.tuna.tsinghua.edu.cn/simple

# uv：用环境变量指定默认索引
# （不要在项目根目录新建 uv.toml，否则 pyproject.toml 中的 [tool.uv] 配置会被忽略）
export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
uv sync --python 3.12
```

更换索引后 uv 会重新解析依赖并改写 `uv.lock`，请勿提交该改动。注意：torch 和 torchvision 仍从 pyproject.toml 中指定的 PyTorch CPU 索引（download.pytorch.org）下载，不经过该镜像。如果本机没有 Python 3.12，uv 会从 GitHub 下载，可能较慢；可先自行安装 Python 3.12，或用 `UV_PYTHON_INSTALL_MIRROR` 指定镜像。

**2. 配置 Hugging Face 镜像**：默认配置下，策略和知识库功能会从 Hugging Face 下载嵌入模型，并在本地运行。如果无法直接访问 Hugging Face，可以配置镜像地址：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

也可以先运行 `uv run python src/scripts/preload_models.py` 预先下载所需模型（同样需要设置 `HF_ENDPOINT`）。

**3. 使用千问（Qwen）**：在阿里云百炼控制台获取 API Key（参见 [OpenAI 兼容说明](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)），然后把 `.env` 中默认的 Groq 配置替换为：

```dotenv
AGENT_SETTING_CONFIG=settings.openai.toml
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# 百炼控制台中创建的 API Key
OPENAI_API_KEY=your-dashscope-api-key  # pragma: allowlist secret
MODEL_NAME=qwen-plus
```

API Key 按地域绑定，需与 `OPENAI_BASE_URL` 的地域一致（上例为北京）。阿里云目前推荐使用业务空间专属域名 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`（业务空间 ID 见控制台“业务空间详情”）；上例中的现有域名仍可正常使用。

DeepSeek 等其他兼容 OpenAI API 的服务配置方式相同：将 `OPENAI_BASE_URL`、`OPENAI_API_KEY` 和 `MODEL_NAME` 替换为对应服务的地址、API Key 和模型名称即可。

**4. 提问与社区**：Discord 在中国大陆可能无法访问，可以使用 [GitHub Discussions](https://github.com/cuga-project/cuga-agent/discussions)。

## 基准测试

AppWorld 基准测试结果（**IBM CUGA + GLM-5.3-Flash**）：Test-Normal 任务目标完成率（TGC）为 93.5%，Test-Challenge 为 92.8%。WebArena 总体成功率为 61.7%（[论文](https://arxiv.org/abs/2510.23856)）。完整数据见[英文 README](../../README.md#strong-benchmark-foundations)。

## 了解更多

- [英文 README](../../README.md)：完整功能介绍、Python SDK、Kubernetes 部署
- [文档站（英文）](https://docs.cuga.dev)
- [CUGA Apps](https://github.com/cuga-project/cuga-apps)：基于 CUGA 构建的示例应用

欢迎分享企业用例、报告问题或贡献集成：[提交 Issue](https://github.com/cuga-project/cuga-agent/issues/new/choose) · [贡献指南](../../CONTRIBUTING.md)
