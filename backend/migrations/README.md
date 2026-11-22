首次启动按 `src/models/entities.py` 的 SQLAlchemy 定义建表。保留数据卷升级时先记录并验证具体迁移。

新增/变更的表：

- `weekly_usage` 增列 `original_state`、`original_units`：首次登记值，回测训练只读这两列。
- `usage_amendments`：补报/更正留痕。
- `recommendations`：采购组，唯一键 `(sleeve_code, start_week)`。
- `forecast_sheets` 增列 `recommendation_id`、`revision`、`sleeve_code`、`captured_amendments`、`triggered_by`、`actuals`。
- `signatures`：签核冻结留档。

旧数据卷升级时需回填 `original_state/original_units = state/units`，并为既有备料单建立对应 `recommendations` 采购组。
