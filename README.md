# Home Assistant Integration 开发完整流程

## 一、环境准备

1. Fork 仓库：Fork [home-assistant/core](https://github.com/home-assistant/core) 到你的 GitHub
2. Clone 到本地：`git clone https://github.com/<your-username>/core.git`
3. 安装 Python：HA 要求 Python 3.14+（用 pyenv 管理）
4. 安装依赖：

```bash
pip install -e .              # HA 核心依赖
pip install -r requirements_test.txt  # 测试依赖
```

5. 安装 pre-commit：`prek install`（HA 用的 prek，不是标准 pre-commit）

## 二、开发集成

### 2.1 创建集成目录

```
homeassistant/components/<integration_name>/
├── __init__.py          # 入口，设置和卸载逻辑
├── manifest.json        # 集成元数据
├── const.py             # 常量定义
├── config_flow.py       # 配置流程（必须）
├── coordinator.py       # 数据协调器
├── sensor.py            # 传感器实体
├── switch.py            # 开关实体
├── button.py            # 按钮实体
├── number.py            # 数字实体
├── select.py            # 选择实体
├── strings.json         # 翻译字符串
├── quality_scale.yaml   # 质量等级声明
└── translations/
    └── en.json          # 英文翻译（自动生成）
```

### 2.2 manifest.json

```json
{
  "domain": "qingpingiot",
  "name": "Qingping",
  "codeowners": ["@your-github-username"],
  "config_flow": true,
  "dependencies": ["mqtt"],
  "documentation": "https://www.home-assistant.io/integrations/qingpingiot",
  "iot_class": "local_push",
  "quality_scale": "bronze",
  "requirements": []
}
```

> **注意：**
> - `codeowners` 必须是有效的 GitHub 用户名（带 `@`）
> - `documentation` URL 即使文档还没写也要填
> - `quality_scale` 新集成必须至少声明 bronze

### 2.3 quality_scale.yaml

必须存在且格式正确，bronze 级别的所有规则都标记为 `done`：

```yaml
rules:
  # Bronze
  action-setup: done
  appropriate-polling: done
  brands: done
  # ... 所有 bronze 规则
```

### 2.4 Python 代码规范

#### 必须遵守的规则

| 规则 | 说明 |
|------|------|
| 禁止 `from __future__ import annotations` | HA 要求 Python 3.14+，不需要这个 |
| 禁止裸 `except Exception` | 用具体异常类型，如 `except (ValueError, KeyError)` |
| 函数返回类型 | `config_flow` 方法必须返回 `ConfigFlowResult` |
| 实体回调类型 | `async_setup_entry` 第三参数用 `AddConfigEntryEntitiesCallback` |
| docstring | 所有 public class 的 `__init__`、`property`、public method 都必须有 docstring |
| import 排序 | 用 ruff 自动排序，按标准库 → 第三方 → HA 内部 → 本地 |
| CoordinatorEntity 泛型 | 继承时必须指定类型，如 `CoordinatorEntity[MyCoordinator]` |
| `device_info` 类型 | 用 `DeviceInfo` 而不是 `dict` |
| EntityCategory | 从 `homeassistant.const` 导入，不从 `homeassistant.helpers.entity` 导入 |
| PLATFORMS | 按字母排序 |
| 常量去重 | HA 已有的常量（`PERCENTAGE`、`CONF_MAC` 等）从 `homeassistant.const` 导入，不要在 `const.py` 重复定义 |

#### 禁止的编码习惯

- 不要用 `except Exception as e:`，改用具体异常
- 不要用 `_LOGGER.error(..., exc_info=True)`，改用 `_LOGGER.exception(...)`
- 不要用 `range(len(x))`，改用 `enumerate(x)`
- 不要用 `x == A or x == B`，改用 `x in {A, B}`
- 不要用 `int` / `float` 赋值给同一变量（mypy 会报错）
- 不要用未使用的 import

### 2.5 strings.json 注意事项

- 不要引用不存在的公共 key，如 `[%key:common::config_flow::data::model%]` 不存在，直接写 `"Model"`
- 可以安全引用的 key：`[%key:common::config_flow::data::device%]`、`[%key:common::config_flow::data::name%]` 等
- JSON key 按字母排序
- 文件末尾必须有换行符

### 2.6 翻译文件

修改 `strings.json` 后必须重新生成翻译：

```bash
python3 -m script.translations develop --integration <integration_name>
```

## 三、本地代码检查（提交前必须全部通过）

按顺序执行以下检查：

### 3.1 Ruff 格式化和检查

```bash
ruff format homeassistant/components/<name>/ tests/components/<name>/
ruff check --fix homeassistant/components/<name>/ tests/components/<name>/
```

### 3.2 Pylint 检查

```bash
pylint homeassistant/components/<name>/
pylint tests/components/<name>/
```

### 3.3 Mypy 类型检查

```bash
mypy homeassistant/components/<name>/
```

### 3.4 Hassfest 验证

```bash
python3 -m script.hassfest
```

### 3.5 Pre-commit 检查

```bash
prek run --files <changed files>
```

### 3.6 Pytest 测试

```bash
pytest tests/components/<name>/ -x -v
```

## 四、Git 提交规范

### 4.1 分支管理

- 在特性分支开发，不要直接在 dev 上改
- 保持干净的 commit 历史，不要有 merge commit
- 用 `git pull --rebase` 代替 `git pull`

### 4.2 Commit 信息

- 用简洁明确的英文描述
- 格式：`Add <integration_name> integration` 或 `fix: ...`
- 不要 amend 已 push 的 commit（reviewers 需要看历史）

### 4.3 Author 邮箱

commit 的 author 邮箱必须是 GitHub 账号关联的邮箱。

```bash
# 检查邮箱
git log --format="%ae" -1

# 修改邮箱
git filter-branch --env-filter '...' 或 git commit --amend --author="Name <email>"
```

### 4.4 Push

```bash
# 如果改写了历史，需要 force push
git push --force origin dev

# 记得带代理（如果需要）
export https_proxy=http://127.0.0.1:7897
```

## 五、提交 PR

### 5.1 PR 标题

简短描述，如：`Add Qingping IoT integration`

### 5.2 PR 描述模板

必须使用 HA 的模板（`.github/PULL_REQUEST_TEMPLATE.md`），不要删除模板中的任何内容：

- **Proposed change**：详细描述集成功能、支持的设备、实体类型
- **Type of change**：勾选 New integration
- **Checklist**：完成的项打勾 `[x]`，未完成的不勾

### 5.3 CLA（Contributor License Agreement）

- GitHub 账号必须关联 commit 使用的邮箱
- 否则 cla-bot 会报错，PR 无法合并

## 六、文档 PR（必须）

HA 要求新集成必须有文档。需要在 [home-assistant/home-assistant.io](https://github.com/home-assistant/home-assistant.io) 仓库单独提 PR：

1. Fork `home-assistant/home-assistant.io`
2. 创建文件 `source/_integrations/<integration_name>.md`
3. 包含：配置说明、支持设备列表、实体说明等
4. 提交 PR 并关联到集成 PR

## 七、常见踩坑总结

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| hassfest 失败 | `quality_scale.yaml` 格式不对或缺失 | 确保格式正确且 manifest 声明了 `quality_scale` |
| prek 失败 | JSON 未排序、文件缺少末尾换行 | 用 `python3 -c "import json; ..."` 重新格式化 |
| mypy 报错 | 类型推断不匹配 | 添加显式类型注解，用 `dict[str, Any]` 而不是 `{}` |
| pylint 报错 | import 路径不对、缺少 docstring | 用 `AddConfigEntryEntitiesCallback`、添加 docstring |
| CLA 检查失败 | commit 邮箱未关联 GitHub | 在 GitHub Settings → Emails 添加邮箱 |
| translations 报错 | 引用了不存在的公共翻译 key | 不确定时直接硬编码字符串 |
| `.gitignore` 误忽略 | 把需要的文件加到了 gitignore | 检查 `.gitignore` 不要忽略 `quality_scale.yaml` 等 |

## 八、本地环境问题

- Python 3.14.0b1 可能缺少 `typing.ByteString`，导致 mashumaro 库崩溃
- 建议用 `pyenv install 3.14-dev` 或更新版本的 Python
- 本地跑不了 hassfest 可能是缺少某些 HA 依赖（如 hassil），CI 上不会有这个问题