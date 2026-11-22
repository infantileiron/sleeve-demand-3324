# 槐页修复 · 无酸封套领用预测

槐页修复的 5 人材料组给照片、地图和手稿的修复工位供料。原流程从 `封套收据-2026.xlsx` 拷入月度表，再按上一月均量备料。工作组需要区分真实零领用与漏传，避免预测看到回测答案，并把预计领用换算成到货周期内可采购的包装数量。备料单给材料负责人核对后交采购员。

| 角色 | 常用页面 | 权限与动作 |
|---|---|---|
| `analyst` 档案备料员 | 规格账本、领用周序列、滚动回测 | 建规格、登记及补报、执行计算 |
| `manager` 材料负责人 | 领用备料单 | 分析员权限，另可签单和查询审计 |
| `viewer` 修复组观察员 | 四个模块 | 查阅与下载证据 |

## 周一工作剧本

1. 08:50 核对 `封套收据-2026.xlsx` 的 `SL-A4` 规格包规为 25 张，到货周期 3 周。
2. 09:05 比对 `SL-8X10`、`SL-MAP` 的包规、到货周数。
3. 09:30 在领用周序列查看各规格的独立台账。2026 年 10 月 19 日的 `SL-MAP` 为缺传。
4. 09:50 先提交地图封套，看回测拒绝把最近的缺传周当作零领取。
5. 10:00 材料员确认原始收据无领用，补报 0 张；该规格的现行建议会触发重算。
6. 10:20 提交 A4 备料测算，SBA 系数 0.20，移动平均窗口 8 周，记下备料起始周。结果展示各起点发生量及误差。
7. 10:45 材料负责人复核包数和用途后签核，回看此前缺传单保留的失败证据。
8. 11:00 下载 CSV 提供给采购，采购另行扣除库存与在途。

## 采购口径

- **采购按「规格 + 备料起始周」领单**：同一组只保留一份现行建议。再次提交同一组只重算并替换现行建议，不新增采购单。重试也复用原采购单。
- **补报触发重算**：补报/更正领用周后，该规格每个起始周晚于该周的采购组各排一次重算。
- **回测只用当时已知的记录**：每个回测起点只用该起点之前首次登记的领用周；缺传或未登记不当作零，也不使用未来记录。
- **误差按补报后的实领算**：实领评测窗口取补报后的当前值，历史训练不重写。
- **乱序完成**：两次补报触发的批次可能后完成先排的旧批次，旧批次不得覆盖已换上的新建议。
- **算不出时沿用**：最新一批算不出则沿用上次可用建议，组状态标为「沿用上次可用」，并列出哪些补报没纳入。
- **页面与下载同源**：两者都读采购组上锁定的现行建议。**已签单**按签核当时的数据和包数冻结留档，后续重算不回写。

## 启动

```bash
cp .env.example .env
docker compose up --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```

页面 `http://localhost:18277`，API 文档 `http://localhost:19277/docs`，MySQL 端口 `17777`。使用 `analyst@huaiye.demo`、`manager@huaiye.demo` 或 `viewer@huaiye.demo`，统一密码 `Archive2026!`。

| 服务 | 作用 |
|---|---|
| frontend | Nginx 1.27 托管原生 HTML/CSS/JS 和 `/api` 反代 |
| backend | Python 3.11、FastAPI、SQLAlchemy 校验写入、签核与导出 |
| worker | 数据库持久存储任务，Redis 唤醒，JSON 写入共享卷 |
| db | MySQL 8.0 保存规格、周记录、补报、采购组、批次与签核留档、审计 |
| redis | Redis 7，健康探针与队列通知 |

`.env` 可覆盖 `FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT`、`JWT_SECRET`、`DB_PASSWORD`、`MYSQL_ROOT_PASSWORD` 与 `SEED_DEMO`。本地开发可用 `docker compose exec -T backend python -m compileall src`。结构定义和输入模型位于 `backend/src/models` 和 `schemas`，计算位于 `backend/src/services/forecast.py`，采购组与重算调度位于 `backend/src/services/business.py`，后台批次合并位于 `backend/src/worker.py`，`frontend/src/pages` 按业务分页面。角色和任务状态出现在 `backend/src/constants/enums.py` / `frontend/src/constants/enums.js`、登录授权、任务切换和备料报告处；周状态见 `UsageInput` 及领用序列页。

### 数据表与关键字段

- `weekly_usage`：`state/units` 为补报后的当前值，`original_state/original_units` 为首次登记值，回测训练只读首次登记值。
- `usage_amendments`：每次补报/更正留痕，作为判断「哪些补报已纳入现行建议」的依据。
- `recommendations`：唯一键 `(sleeve_code, start_week)`；`revision` 为最近请求批次，`applied_revision` 为现行建议来自哪一批次，`covered_amendments` 为已纳入的补报。
- `forecast_sheets`：一次计算批次，保留锁定快照、实领、结果与失败说明。
- `signatures`：签核当时冻结的快照、实领、结果与包数。

## 口径与使用边界

每个规格从最近周向历史回溯至缺周或缺传。最近周缺传直接阻断。SBA 分别对正领用量和正领用之间的间隔做指数平滑，得到每周 `(1-α/2)×平滑领用量÷平滑间隔`。每个回测点向前训练，向后按完整到货周期汇总，移动均值用同一评估窗口；周期 MAE 较低的方法取得备料建议，得分完全相同时使用 SBA。包数使用周期估计数量除包规后向上取整。重叠的历史评估窗口用于方法比选，误差只代表本台账历史片段；点预测及备料包数还需结合库存和正在执行的修复任务。预置序列按固定日期与算式构造，可重复验证。

MIT License，见 `LICENSE`。
