# PointPivot 自动交易系统

> ⚠️ 免责声明
本项目仅用于技术学习和研究，不构成任何投资建议。加密货币交易存在极高风险，可能导致全部本金损失。使用者应自行遵守所在国家/地区的法律法规，因使用本项目产生的任何后果由使用者自行承担。严禁用于非法金融活动。

基于 OKX + 可配置 AI 策略的加密货币自动交易 Web 应用。

## 功能特点

- **会话登录**：内置用户认证，所有页面和 API 受登录保护
- **AI 策略管理**：创建、编辑、启停多个 AI 交易策略，支持定时调度
- **多模型支持**：支持 DeepSeek、Qwen 及任何 OpenAI 兼容接口，可按策略单独配置
- **提示词模板库**：独立管理提示词模板，策略编辑时可一键载入
- **涨幅榜扫描**：策略可开启自动扫描 OKX 涨幅榜，按涨幅/成交额筛选交易对后让 AI 决策
- **自动交易**：定时执行策略，AI 分析 K 线和技术指标后自动下单（多/空/平仓/持仓）
- **AI 止盈止损**：AI 返回具体止盈止损价格，系统自动挂单，不再依赖固定百分比
- **固定 USDT 仓位**：每笔交易使用固定 USDT 名义金额（自动换算合约张数），控制风险敞口
- **持仓模式切换**：支持单向持仓 / 双向持仓切换
- **持仓管理**：实时同步 OKX 持仓及止盈止损状态，支持手动平仓
- **完整日志**：记录每次策略执行的 prompt、AI 响应和交易结果
- **净值曲线**：账户净值快照图表展示
- **双账户配置**：实盘与模拟盘分别存储独立 API 密钥，切换无需重填
- **代理支持**：可为 OKX 连接配置 HTTP/SOCKS 代理

## 技术栈

- **后端**: Python 3.10+, FastAPI, Jinja2, SQLite (WAL), APScheduler, ccxt
- **前端**: Jinja2 模板 + Bootstrap 5 + Chart.js
- **AI**: OpenAI 兼容接口 (DeepSeek / Qwen 等)

## 快速开始

### 1. 安装依赖

```bash
cd okx
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 文件，至少填写 ENCRYPTION_KEY
```

生成 `ENCRYPTION_KEY`（用于加密存储 API 密钥）：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> 如果 `.env` 中未设置 `ENCRYPTION_KEY`，系统会在首次启动时自动生成并写入，但建议手动配置以便备份。

`.env` 完整示例（参见 [.env.example](.env.example)）：

```dotenv
# OKX 实盘凭证（可在 Web 界面配置，此处可留空）
OKX_API_KEY=
OKX_SECRET_KEY=
OKX_PASSPHRASE=
OKX_SIMULATED=true

# 默认 AI 配置
AI_DEFAULT_PROVIDER=deepseek
AI_DEFAULT_API_KEY=your_ai_api_key_here
AI_DEFAULT_BASE_URL=https://api.deepseek.com/v1

# 数据库加密密钥（必填）
ENCRYPTION_KEY=

# Web 会话密钥（可选，不填则使用内置默认值）
APP_SESSION_SECRET=your_session_secret_here

# 服务监听地址
HOST=0.0.0.0
PORT=8000
```

### 3. 启动系统

```bash
python run.py
```

访问 http://localhost:8000，使用默认账号登录。

## 使用流程

1. **登录**：访问 `/login` 页面登录系统
   - 默认用户名密码：snowwolf/123qwe
   - 可在app\routers\pages.py文件中修改
2. **系统配置**：进入"系统配置"页面
   - 分别填写**模拟盘**和**实盘** OKX API 密钥
   - 填写默认 AI API 密钥和模型
   - 按需配置代理地址
   - 选择当前使用模拟盘/实盘
3. **提示词模板**（可选）：进入"提示词模板"页面预建常用模板
4. **创建策略**：进入"策略管理"，点击"新建策略"
   - 填写策略名称和执行间隔
   - 选择 AI 模型（不填则使用全局配置）
   - 手动添加交易对，或开启**涨幅榜扫描**自动筛选
   - 设置每笔仓位 USDT 金额和杠杆倍数
   - 编辑或载入提示词模板
5. **启动策略**：在策略卡片上点击"启动"
6. **监控**：
   - "运行日志"查看每次执行详情（prompt / AI 响应 / 下单结果）
   - "持仓管理"查看当前持仓、止盈止损价及净值曲线
   - "交易记录"查看历史成交

## AI 响应格式

策略提示词需引导 AI 返回以下 JSON 格式（系统会自动在提示词末尾附加强制格式说明）：

```json
{
  "action": "long | short | close_long | close_short | hold",
  "reason": "简短的决策理由",
  "tp_price": 68000.0,
  "sl_price": 64000.0
}
```

- `action` 为 `long` / `short` 时，`tp_price` 和 `sl_price` 必须为数字；
- `action` 为 `close_long` / `close_short` / `hold` 时，两者返回 `null`。

## 提示词模板变量

| 变量 | 说明 |
|------|------|
| `{symbol}` | 交易对名称 |
| `{klines}` | 格式化的 K 线数据表 |
| `{current_price}` | 当前价格 |
| `{bb_upper}` | 布林带上轨 |
| `{bb_middle}` | 布林带中轨 |
| `{bb_lower}` | 布林带下轨 |
| `{bb_position}` | 价格在布林带中的位置（0-100） |
| `{rsi}` | RSI 指标（14 期） |
| `{macd}` | MACD 值 |
| `{macd_signal}` | MACD 信号线 |
| `{macd_histogram}` | MACD 柱状图 |
| `{avg_volume}` | 平均成交量 |
| `{last_volume}` | 最新成交量 |
| `{equity}` | 账户总权益（USDT） |
| `{available_balance}` | 可用余额（USDT） |
| `{position_info}` | 当前持仓信息 |
| `{indicators}` | 所有技术指标 JSON |

## 安全说明

- 所有 API 密钥在数据库中使用 **Fernet 对称加密**存储，密钥不会在前端明文显示
- Web 界面受会话认证保护，未登录访问自动重定向到登录页
- 默认启用**模拟盘**模式，切换至实盘需二次确认
- 建议在生产环境中设置强随机 `APP_SESSION_SECRET`
- CORS 配置可在 `app/main.py` 中按需调整

## 注意事项

- 本系统仅供学习和研究使用
- 加密货币交易存在高风险，请谨慎使用实盘模式
- AI 模型的交易建议不构成投资建议
- OKX API 密钥需开启**交易权限**；建议同时绑定 IP 白名单
- 首次使用模拟盘需在 OKX 官网申请模拟盘账户并生成专用 API 密钥
