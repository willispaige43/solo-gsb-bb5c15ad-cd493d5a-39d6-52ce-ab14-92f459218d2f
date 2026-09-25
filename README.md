# 装配尺寸链可行性校核 API

用 **Python 3.11 + FastAPI** 实现的一维装配尺寸链公差可行性校核服务。

客户端提交**基准点**、**测点**和若干条**有向尺寸约束**，每条约束给出
“终点坐标 − 起点坐标”的整数微米（µm）差的上下限。服务端把上下限转换为
**差分约束**（带权有向图），用 **Bellman–Ford** 判定是否存在负环：

- **无负环（可行）**：所有公差能同时满足，`/check` 返回以基准点为零的各点整数坐标；
  `/ranges` 进一步返回每个测点在全部约束同时成立时可分别达到的**最小/最大整数坐标紧界**；
  `/reconcile` 额外接收各测点的整数微米**读数**，求最小非负整数 `e`，使存在一组满足
  全部约束的校正坐标让所有测点 `|校正坐标 − 读数| ≤ e`，并返回 `e`、该组校正坐标
  与逐点校正量；
- **有负环（不可行）**：沿负环累加得到 `0 ≤ 负数` 的矛盾，返回构成该矛盾
  闭环的**原始约束编号**及逐边明细，便于定位尺寸链中的过约束环节
  （三个接口返回同一格式的矛盾闭环）。

## 单条启动命令

需要安装 Docker（含 Compose 插件）。在项目根目录执行：

```bash
docker compose up --build -d
```

启动后服务监听：

| 用途 | 地址 |
| --- | --- |
| 健康检查 | <http://localhost:8000/healthz> |
| 可行性校核接口 | `POST http://localhost:8000/api/v1/chains/check` |
| 测点坐标紧界接口 | `POST http://localhost:8000/api/v1/chains/ranges` |
| 读数最小校正接口 | `POST http://localhost:8000/api/v1/chains/reconcile` |
| Swagger UI（在线试调） | <http://localhost:8000/docs> |
| OpenAPI Schema | <http://localhost:8000/openapi.json> |

停止服务：`docker compose down`；查看日志：`docker compose logs -f`。

## 必要配置

所有配置均有默认值，开箱即用；需要调整时通过环境变量覆盖
（可复制 `.env.example` 为 `.env` 后修改）：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DIMCHAIN_PORT` | `8000` | 宿主机映射端口（容器内固定监听 `8000`）。例如端口被占用：`DIMCHAIN_PORT=9000 docker compose up --build -d` |
| `DIMCHAIN_LOG_LEVEL` | `info` | uvicorn 日志级别：`critical` / `error` / `warning` / `info` / `debug` |

容器内变量（一般无需改动）：`APP_HOST=0.0.0.0`、`APP_PORT=8000`。

不使用 Docker 时的本地运行方式（Python 3.11+）：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 快速试用

```bash
# 可行：返回坐标
curl -s -X POST http://localhost:8000/api/v1/chains/check \
  -H 'Content-Type: application/json' \
  -d @examples/feasible.json | python -m json.tool

# 可行：返回每个测点的最小/最大坐标紧界（跨测点约束 c3 把 B、C 的范围相互收紧）
curl -s -X POST http://localhost:8000/api/v1/chains/ranges \
  -H 'Content-Type: application/json' \
  -d @examples/ranges.json | python -m json.tool

# 读数校正：返回最小校正半径 e、同一组可行校正坐标与逐点校正量
curl -s -X POST http://localhost:8000/api/v1/chains/reconcile \
  -H 'Content-Type: application/json' \
  -d @examples/reconcile.json | python -m json.tool

# 不可行：返回矛盾闭环的约束编号
curl -s -X POST http://localhost:8000/api/v1/chains/check \
  -H 'Content-Type: application/json' \
  -d @examples/infeasible.json | python -m json.tool

# 校验错误（未知点 / 反向区间 / 未连通）
curl -s -X POST http://localhost:8000/api/v1/chains/check \
  -H 'Content-Type: application/json' \
  -d @examples/validation_error.json | python -m json.tool
```

## 请求与响应

`/api/v1/chains/check` 与 `/api/v1/chains/ranges` 的请求体、静态校验规则
（含下文 422 错误码）完全一致；`/api/v1/chains/reconcile` 在此之上增加
`readings` 字段（见下文专节）。

### 请求体 `POST /api/v1/chains/check`（`/ranges` 同）

```json
{
  "datum_points": ["A"],
  "measure_points": ["B", "C"],
  "constraints": [
    {"id": "c1", "start": "A", "end": "B", "lower": 10, "upper": 12},
    {"id": "c2", "start": "B", "end": "C", "lower": 5, "upper": 8}
  ]
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `datum_points` | string[]（≥1） | 基准点编号，坐标恒为 `0`；多个基准点视为同一零位 |
| `measure_points` | string[]（≥1） | 测点编号；必须经约束链（忽略方向）连到某个基准点 |
| `constraints[].id` | string | 约束原始编号，矛盾闭环按此编号溯源；不可重复 |
| `constraints[].start` / `end` | string | 起点 / 终点编号，必须已在基准点或测点中声明 |
| `constraints[].lower` / `upper` | **整数** µm | 语义：`lower ≤ coord(end) − coord(start) ≤ upper` |

所有编号会自动去除首尾空白；允许负整数（表示反向或负坐标）。

### 响应一：可行（HTTP 200）

```json
{
  "status": "feasible",
  "unit": "um",
  "coordinates": {"A": 0, "B": 12, "C": 20}
}
```

`coordinates` 是一组满足全部约束的整数坐标（逐点取紧上界得到的“最大可行解”），
所有基准点为 `0`。公差区间非零时解不唯一，客户端不应把返回值当作唯一解。

### `POST /api/v1/chains/ranges`：测点坐标紧界

请求体与 `/check` **完全相同**（同样的字段、同样的静态校验与 422 响应）；
可行时返回 200 + `RangesResponse`，不可行时返回与 `/check` **完全相同**的
`InfeasibleResponse`（矛盾闭环编号、逐边明细、环权）。

请求（`examples/ranges.json`，注意 c3 是连接两个测点的跨测点约束）：

```json
{
  "datum_points": ["A"],
  "measure_points": ["B", "C"],
  "constraints": [
    {"id": "c1", "start": "A", "end": "B", "lower": 0, "upper": 10},
    {"id": "c2", "start": "A", "end": "C", "lower": 0, "upper": 10},
    {"id": "c3", "start": "B", "end": "C", "lower": 5, "upper": 5}
  ]
}
```

响应（HTTP 200）：

```json
{
  "status": "feasible",
  "unit": "um",
  "ranges": [
    {"id": "B", "minimum": 0, "maximum": 5},
    {"id": "C", "minimum": 5, "maximum": 10}
  ]
}
```

`ranges` 严格按请求 `measure_points` 的顺序排列，每项给出该测点在**所有约束
同时成立、全部基准点固定为 0** 的前提下可取的整数微米坐标闭区间。上例若只看
c1/c2 则 B、C 各为 `[0,10]`，但 c3 要求 `C − B = 5`，跨测点约束把两测点的
范围相互收紧为 `B ∈ [0,5]`、`C ∈ [5,10]`。

紧界语义（重要）：

- 每个端点都是**全局可实现**的——存在一组满足全部约束（含基准点为 0）的
  整数坐标让该测点取到 `minimum` / `maximum`；
- 但**不要求同一组坐标同时达到所有端点**。上例中“B 取最大 5 且 C 取最小 5”
  不可能同时成立（违反 c3），两个端点分别由不同的可行赋值达到
  （B=0⇒C=5 达到最小端；B=5⇒C=10 达到最大端）；
- 多个基准点互钉为 0 的固定关系、跨测点约束、负权边（反向公差）均已纳入推导，
  界为包含负数的整数闭区间（如 `{"minimum": -12, "maximum": -10}`）。

### `POST /api/v1/chains/reconcile`：读数最小校正

请求体在 `/check` 的基础上**增加 `readings` 字段**，其余字段、静态校验规则
完全相同（`examples/reconcile.json`）：

```json
{
  "datum_points": ["A"],
  "measure_points": ["B", "C"],
  "constraints": [
    {"id": "c1", "start": "A", "end": "B", "lower": 10, "upper": 12},
    {"id": "c2", "start": "B", "end": "C", "lower": 5, "upper": 8}
  ],
  "readings": {"B": 10, "C": 19}
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `readings` | object | 测点编号 → **整数** µm 读数；必须**恰好覆盖全部测点**：缺漏报 `missing_reading`、多出非测点键（未知点或基准点）报 `extra_reading`（均 422）；非整数读数由 Pydantic 拦截（422）。键会自动去除首尾空白 |

语义：若原尺寸约束本身无解，返回与 `/check` **完全相同**的 `InfeasibleResponse`
（矛盾闭环，与读数无关）。有解时，求**最小非负整数 `e`**，使存在一组满足全部
约束（含基准点为 0）的整数校正坐标，让所有测点**同时**满足
`|校正坐标 − 读数| ≤ e`，返回 200：

```json
{
  "status": "feasible",
  "unit": "um",
  "e": 1,
  "coordinates": {"A": 0, "B": 11, "C": 19},
  "corrections": {"B": 1, "C": 0}
}
```

| 字段 | 说明 |
| --- | --- |
| `e` | 最小非负整数校正半径（µm）：`e` 可行而 `e−1` 不可行（全局最优，而非逐点各自最优） |
| `coordinates` | 一组达到 `e` 的可行校正坐标（全部约束、基准点为 0 均成立；基准点坐标恒为 0） |
| `corrections` | 逐测点校正量 = 校正坐标 − 读数（整数 µm）；与 `coordinates` 来自**同一组**坐标，绝对值均 `≤ e` 且最大值恰为 `e` |

上例中读数 `B=10、C=19` 要求 `C − B = 9`，但 c2 只允许 `[5,8]`，故 `e=0`
不可行；`e=1` 时 `B=11、C=19` 同时满足 c1、c2，即最小校正半径为 1。

### 响应二：不可行（HTTP 200）

```json
{
  "status": "infeasible",
  "unit": "um",
  "conflicting_constraint_ids": ["c2", "c1", "c3"],
  "cycle_edges": [
    {"constraint_id": "c2", "start": "B", "end": "C", "bound": "lower",
     "weight": -9, "edge_from": "C", "edge_to": "B"},
    {"constraint_id": "c1", "start": "A", "end": "B", "bound": "lower",
     "weight": -9, "edge_from": "B", "edge_to": "A"},
    {"constraint_id": "c3", "start": "C", "end": "A", "bound": "lower",
     "weight": 1, "edge_from": "A", "edge_to": "C"}
  ],
  "cycle_weight": -17
}
```

环的含义（以该例说明）：c1/c2 要求 `B ≥ A + 9`、`C ≥ B + 9`（取下限反向边，
权 −9），而 c3 的 `C − A ∈ [−1, 0]` 给出反向边 `A → C`，权 1，三条边
绕环合计 `−17 < 0`，即 `0 ≤ −17` 的矛盾。`bound` 表示该环边取自约束的
上限边（`upper`，权 `upper`）还是下限反向边（`lower`，权 `−lower`）；
当矛盾涉及“多个基准点必须同为零”时，环中还会出现 `bound = "datum_pin"`
的零权边（`constraint_id` 为 `null`），其余真实约束编号仍会列入
`conflicting_constraint_ids`。

### 响应三：请求校验错误（HTTP 422）

```json
{
  "detail": [
    {"code": "reversed_interval",
     "message": "约束 'c1' 的公差区间反向：lower=10 > upper=9",
     "location": ["constraints", 0, "lower"], "constraint_id": "c1"},
    {"code": "unknown_point",
     "message": "约束 'c2' 的终点 'GHOST' 未在基准点或测点中声明",
     "location": ["constraints", 1, "end"], "constraint_id": "c2"}
  ]
}
```

一次请求会尽量收集并返回**全部**校验错误。领域校验错误代码：

| `code` | 触发条件 |
| --- | --- |
| `unknown_point` | 约束引用了未在基准点/测点中声明的点（“未知点”） |
| `reversed_interval` | `lower > upper`（反向区间） |
| `point_not_connected_to_datum` | 测点在无向意义下未连到任何基准点，坐标无法确定 |
| `duplicate_point` / `duplicate_constraint` | 点编号或约束编号重复 |
| `point_role_conflict` | 同一点同时出现在基准点和测点中 |
| `missing_reading` | （仅 `/reconcile`）`readings` 缺少某个测点的读数 |
| `extra_reading` | （仅 `/reconcile`）`readings` 含有非测点键（未知点或基准点） |

此外，字段缺失、类型错误（如公差给了小数 `1.5`）、多余字段等由
Pydantic 拦截，同样返回 HTTP 422。

注意：`lower == upper` 的零宽区间表示精确尺寸，是合法约束；
`start == end` 且区间不含 0 的自环矛盾会作为 `infeasible` 返回。

## 算法说明

对约束 `c: coord(end) − coord(start) ∈ [lower, upper]` 建两条边：

- `coord(end) − coord(start) ≤ upper` → 边 `start → end`，权 `upper`
- `coord(start) − coord(end) ≤ −lower` → 边 `end → start`，权 `−lower`

基准点取其中之一作 Bellman–Ford 源点（距离 0），其余基准点与源点间
加双向 0 权边以钉在同一零位。松弛 `|V|` 轮：第 `|V|` 轮仍可松弛
⇔ 存在负环 ⇔ 约束系统无解；负环上的边经前驱链回溯还原为有向环，
再映射回原始约束编号。无负环时，源点到各点的最短距离即为一组可行坐标。

`/ranges` 的紧界同样由最短路给出（差分约束 `x_v − x_u ≤ w(u→v)`）：

- 沿任意 anchor→v 路径累加得 `x_v ≤ 路径权和`，故 **最大坐标** =
  原图 anchor→v 的**最短距离**；
- **最小坐标** = 转置图（每条边反向、权不变）anchor→v 最短距离的相反数
  （等价于原图 v→anchor 最短距离取负）。

两组极值距离各自给出一组满足全部不等式的全局可行坐标（全部基准点为 0），
因此上下界均可达到、是紧界；整数边权保证端点为整数微米。复杂度 O(|V|·|E|)。

`/reconcile` 在尺寸约束可行后，把每个测点 `p` 的读数 `r_p` 写成区间约束
`x_p ∈ [r_p − e, r_p + e]`，即两条差分边并入差分图：`anchor→p` 权 `r_p + e`、
`p→anchor` 权 `e − r_p`。给定 `e` 的可行性仍由 Bellman–Ford 负环判定；
`e` 增大只会放宽区间，可行性关于 `e` 单调，故以基准可行解（原图最短路坐标）
与读数的最大偏差为上界**二分最小可行 `e`**（该上界本身即一个可行 `e`）。
最后在最小 `e` 的图上取最短路坐标，作为返回的同一组可行校正坐标，
逐点校正量 = 校正坐标 − 读数。整数边权保证二分得到的 `e` 与坐标均为整数。

## 项目结构

```
app/
  main.py       # FastAPI 应用、路由、错误处理
  schemas.py    # 请求/响应 Pydantic 模型
  solver.py     # 静态校验 + 差分图建模 + Bellman-Ford
tests/          # pytest 测试（含暴力枚举随机交叉验证）
examples/       # curl 示例请求
Dockerfile
docker-compose.yml
requirements.txt
```

## 运行测试

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```
