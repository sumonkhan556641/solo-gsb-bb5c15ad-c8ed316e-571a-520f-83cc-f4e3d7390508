# 激光切割路径排程 Web 工具（Laser Cutting Path Scheduler）

导入 SVG 闭合轮廓，自动解析嵌套关系、标记自交/未闭合轮廓，在
**先内后外**约束下求解**最小空移的确定性切割顺序**，提供路径预览与
**G-code** 导出。

- **后端**：Python 3.11 · FastAPI · SQLite（`svgelements` 解析、numpy/scipy 弧长计算）
- **前端**：TypeScript · React 18 · Vite
- **部署**：单镜像多阶段构建，Docker Compose 一键启动

---

## 一键启动

```bash
docker compose up -d --build
```

启动后访问：

| 项目 | 地址 |
| --- | --- |
| Web 界面 | <http://localhost:8000> |
| 健康检查 | <http://localhost:8000/api/health> |
| API 文档（Swagger） | <http://localhost:8000/docs> |

> 宿主机端口默认 **8000**，映射容器内 8000。
> 停止：`docker compose down`（加 `-v` 同时清除 SQLite 数据卷）。

快速体验：浏览器打开界面，把 `samples/nested-parts.svg` 拖入即可；
`samples/invalid-contours.svg` 演示自交/未闭合轮廓的标红与剔除。

---

## 配置

### 端口（Compose 变量）

```bash
LASER_PORT=9000 docker compose up -d --build
```

或新建 `.env`：`LASER_PORT=9000`。

### 运行时环境变量（容器内）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LASER_DB_PATH` | `/data/laser.db` | SQLite 数据库文件位置（已挂载命名卷持久化） |
| `LASER_STATIC_DIR` | `/app/static` | 前端静态文件目录（镜像内已包含构建产物） |

### 界面内排程/G-code 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| Home X / Y | `0 / 0` | 机床原点，空移起点（mm） |
| 完成后返回 Home | 开 | 末刀后追加一段回原点空移（计入空移总长） |
| Y 轴翻转 | 关 | 适配机床上位机坐标系（翻转后重算） |
| 曲线离散容差 | `0.5 mm` | 弧/贝塞尔曲线按弧长分段的最大弦长 |
| 空移进给 F | `3000 mm/min` | G0 进给速度 |
| 切割进给 F | `800 mm/min` | G1 切割速度 |
| 激光功率 S | `255` | GRBL 开光功率（0–1000，M3 S 值） |

坐标约定：**SVG viewBox 的 1 用户单位 = 1 mm**。根节点 `width/height`
上的物理单位（如 `width="200mm"`）会被忽略，以避免 96 PPI 隐式缩放。

---

## 功能说明

### 1. SVG 导入与解析

- 支持 `rect / circle / ellipse / polygon / polyline / line / path`
  以及分组 `<g transform>` 嵌套变换；曲线（弧、二次/三次贝塞尔）按弧长
  容差离散为多边形点列。
- 自动识别多子路径（一个 `<path>` 内的多个 `M` 段分别成轮廓）。

### 2. 嵌套解析与问题标记

- 采用**射线法点包含 + bbox 预筛**计算轮廓包含关系，直接父轮廓
  = 包含该轮廓的面积最小者，并给出嵌套深度。
- **未闭合**：子路径缺少 `Z` 闭合段（橙色虚线）。
- **自交**：非相邻边相交/重叠（红色虚线，空间网格分桶检测）。
- 退化轮廓（面积为 0、顶点不足）同样标记。
- 所有异常轮廓在画布与清单中标红，**不参与排程与 G-code 导出**。

### 3. 切割顺序求解（先内后外 · 最小空移）

- **约束**：轮廓必须等其内部所有轮廓（嵌套树子孙）切完后才能切，
  保证内孔/内腔先于外轮廓落刀，防止板材脱开后内件移位。
- **目标**：最小化空移总长度（Home→各入刀点→…→Home）。
  每步联合选择：下一条轮廓、距当前刀位点最近的顶点作为**入刀点**、
  走刀方向。
- 全局最优属于带先后约束的广义 TSP（NP-hard），本工具使用
  **确定性贪心最近邻**：相同输入与参数产生逐位一致、可复现的顺序；
  平局按嵌套深度（深者优先）、轮廓 index 裁决。

### 4. 预览与导出

- 画布：轮廓按切割顺序彩虹着色；绿点+数字表示入刀点与次序；
  灰色虚线为空移；蓝色菱形为 Home；悬停查看轮廓详情。
- 轮廓清单：来源元素、闭合/自交状态、父子嵌套、顺序、面积、
  切割长度、空移距离、问题描述。
- G-code：GRBL 兼容（`G17 G21 G90 G94`，`G0` 空移/M5 关光，
  `G1`/`M3 S…` 开光切割，闭合环末段封口回入刀点）。

G-code 片段示例：

```gcode
G17 G21 G90 G94
G0 F3000 X0.000 Y0.000
M5
; --- contour #1 <Circle> (order 0, depth 1) ---
M5
G0 X40.000 Y50.000
M3 S255
G1 F800 X40.087 Y49.500
...
G1 X40.000 Y50.000
M5
```

---

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/jobs` | multipart 上传 `file`（SVG）+ `settings`（JSON 字符串），解析并求解，返回轮廓/顺序/统计 |
| `GET` | `/api/jobs` | 历史作业列表（SQLite 持久化） |
| `GET` | `/api/jobs/{id}` | 作业详情（轮廓、cuts、stats、meta） |
| `GET` | `/api/jobs/{id}/gcode` | 下载 `.gcode` |
| `GET` | `/api/jobs/{id}/svg` | 取回原始 SVG |
| `GET` | `/api/health` | 健康检查 |

curl 示例：

```bash
curl -F "file=@samples/nested-parts.svg" \
     -F 'settings={"home_x":0,"home_y":0,"cut_feed":1000,"power":300}' \
     http://localhost:8000/api/jobs
```

---

## 本地开发（不用 Docker）

后端：

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
LASER_STATIC_DIR=../frontend/dist \
LASER_DB_PATH=./laser.db \
uvicorn app.main:app --reload --port 8000
```

前端（热更新，自动代理 `/api` 到 8000）：

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173
npm run build    # 类型检查 + 生产构建到 dist/
```

测试：

```bash
cd backend && python3 -m pytest -q
```

---

## 目录结构

```
.
├── Dockerfile                # 多阶段：node 构建前端 → python 运行后端（单镜像）
├── docker-compose.yml        # 一键编排，命名卷持久化 SQLite
├── samples/                  # 示例 SVG（正常嵌套 / 异常轮廓）
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py           # FastAPI 路由 / 静态托管
│       ├── geometry.py       # SVG 解析、离散、自交检测、嵌套关系
│       ├── scheduler.py      # 先内后外约束 + 最小空移贪心求解
│       ├── gcode.py          # G-code 生成
│       └── db.py             # SQLite 持久化
└── frontend/
    ├── src/
    │   ├── App.tsx           # 上传/参数/清单/历史
    │   ├── Canvas.tsx        # 路径预览（SVG 画布）
    │   ├── api.ts / types.ts
    └── vite.config.ts        # dev 代理 /api → :8000
```

## 已知边界

- 求解器为确定性贪心最近邻（工业常用、毫秒级），非全局最优；
  需要严格最优可在 `scheduler.py` 替换为约束规划/DP 求解，接口不变。
- 重叠（部分相交但无自交）的轮廓按“不包含”处理；请在 CAD 中避免
  非嵌套式的轮廓相交。
