# 激光切割路径排程 Web 工具

一个 **Docker 一键启动** 的激光切割路径排程工具：导入 SVG 闭合轮廓 → 解析嵌套（孔洞）关系并标记自交 / 未闭合轮廓 → 在 **先内后外（inner-before-outer）** 约束下，按最小空移目标求解**确定性**切割顺序 → 预览路径并导出 **G-code**。

- 后端：Python 3.11 + FastAPI + SQLite（`aiosqlite`）
- 前端：TypeScript + Vite（原生 SVG 渲染，无重型框架）
- 部署：多阶段 Dockerfile + docker-compose，单镜像同时托管 API 与前端静态资源

---

## 一条命令启动

```bash
docker compose up -d --build
```

启动后访问：

| 入口 | 地址 |
| --- | --- |
| Web 界面 | <http://localhost:8000> |
| 健康检查 | <http://localhost:8000/api/health> |
| API 文档（Swagger） | <http://localhost:8000/docs> |

**默认端口 `8000`**，数据持久化在命名卷 `laser-data`（容器内 `/data/lasercut.db`）。

停止 / 删除：

```bash
docker compose down          # 停止并删除容器（保留数据卷）
docker compose down -v       # 同时删除排程数据库
```

> 需要使用自定义端口或不依赖 compose：
>
> ```bash
> APP_PORT=9000 docker compose up -d --build
> # 或纯 docker
> docker build -t lasercut-scheduler .
> docker run -d -p 9000:8000 -v "$PWD/data:/data" lasercut-scheduler
> ```

---

## 快速体验

1. 打开 <http://localhost:8000>
2. 将 `examples/sample.svg` 拖入左上角上传区（或点击选择）
3. 在预览中查看：
   - 彩色实线：有效轮廓，颜色按切割序列渐变；绿色编号徽章为入刀点与切割次序
   - 橙色虚线：轮廓间的空移路径；黄色圆点为 `HOME` 起点
   - 红色图形 / 红点：**未闭合**轮廓与**自交**交点
4. 右侧面板查看切割顺序表、嵌套树和统计（切割总长 / 空移总长）
5. 修改起点、功率、进给等参数后自动重排，点击 **⬇ 导出 G-code** 下载

预览交互：**滚轮缩放 · 拖拽平移 · 双击复位适配**。

---

## 功能说明

### SVG 导入与解析

- 支持元素：`path`（含 `M/L/H/V/C/S/Q/T/A/Z` 全部指令与相对/绝对、隐式重复）、`rect`（含圆角）、`circle`、`ellipse`、`line`、`polyline`、`polygon`
- 支持 `transform`：`matrix / translate / scale / rotate / skewX / skewY`（含嵌套 `<g>` 链式变换）
- 曲线（三次 / 二次贝塞尔、椭圆弧）按固定步数离散为折线；椭圆弧按 SVG F.6 规范做半径修正
- 自动识别 `viewBox`；多子路径 `d="…Z …Z"` 拆分为多个独立轮廓
- 上限默认 5 MiB（`MAX_UPLOAD_BYTES` 可配）

### 合法性标记

- **未闭合**：`polyline` / `line` 或未以 `Z` 收尾的路径子段（红色虚线显示，不参与排程与 G-code）
- **自交**：对非相邻边做带网格空间索引的线段求交，检测交叉与 T 型接触，交点以红点标出
- **退化轮廓**：点数过少或零面积同样标记
- 所有异常轮廓被排除出切割序列，但保留在嵌套树和统计中

### 嵌套关系

对有效闭合轮廓：以代表点 + 射线法点在多边形内判定包含关系，取包围它的**最小**轮廓为父节点，构建嵌套森林（板料 → 孔 → 孔内凸台 …），并计算层级深度。bbox 预检加速判定。

### 排程算法（确定性）

1. **约束**：每个父轮廓必须在其所有子轮廓切割完毕后才能切割（先内后外，保证切内孔时工件仍与母材相连）
2. **就绪集**：所有孩子均已切完的轮廓才可切
3. **最小空移贪心**：每一步在就绪集中选择距当前刀头最近的轮廓（距离取到轮廓顶点的最近点，并把该点作为入刀点 / 路径旋转起点）
4. **确定性**：距离并列时以轮廓索引（SVG 文档顺序）打破平局；相同输入永远得到相同顺序
5. 每条轮廓切完后闭合回到入刀点，序列首尾计入 `HOME` 往返

> 算法为最近邻贪心：在满足先内后外硬约束的前提下最小化局部空移，结果确定、可复现；轮廓规模在数百以内时空移优化效果显著且耗时为亚秒级。

### G-code 导出（GRBL 风格）

- `G21 G90 G17`（毫米 / 绝对坐标），动态激光 `M4 S…` / `M5`
- 空移 `G0`（关光 + 安全抬刀 `Z`），切割 `G1 F…`
- 默认翻转 Y 轴以适配屏幕 SVG（Y 向下）到机床坐标（Y 向上）
- 可配置：起点 HOME、激光功率、切割进给、空移速度、安全高度、自定义前导指令
- 文件头注释输出轮廓数量、切割 / 空移总长度，文件尾 `$H` 回零

---

## 配置

通过环境变量或 `.env`（复制 `.env.example` 即可）配置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_PORT` | `8000` | 宿主机映射端口（compose） |
| `DATABASE_PATH` | `/data/lasercut.db` | SQLite 数据库路径 |
| `MAX_UPLOAD_BYTES` | `5242880`（5 MiB） | 单个 SVG 上传大小上限 |
| `CORS_ORIGINS` | `*` | 允许跨域来源，逗号分隔 |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | 容器内 uvicorn 监听地址 |

每个项目内的切割参数（HOME、`invert_y`、功率、进给、安全 Z、G-code 前导指令）保存在 SQLite 中，可在界面「G-code 参数」卡片修改，修改 HOME 会立即按新起点确定性重排。

---

## REST API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/projects` | multipart 上传 `file`（SVG）与可选 `name`，创建项目并返回排程 |
| `GET` | `/api/projects` | 项目列表（含轮廓数 / 异常数） |
| `GET` | `/api/projects/{id}` | 项目详情：轮廓、嵌套、顺序、路径段 |
| `POST` | `/api/projects/{id}/schedule` | 重新求解切割顺序 |
| `PATCH` | `/api/projects/{id}/settings` | 修改切割参数并重排 |
| `GET` | `/api/projects/{id}/gcode` | 下载 G-code（`text/x-gcode` 附件） |
| `DELETE` | `/api/projects/{id}` | 删除项目（级联删除轮廓） |
| `GET` | `/api/health` | 健康检查 |

---

## 目录结构

```
.
├── Dockerfile                # 多阶段：Node 构建前端 → python-slim 运行后端+托管静态文件
├── docker-compose.yml        # 一键启动（端口 8000，命名卷持久化）
├── .env.example              # 环境变量样例
├── examples/sample.svg       # 含嵌套/自交/未闭合的示例排料
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py           # FastAPI 路由、静态托管
│   │   ├── config.py         # 环境变量配置
│   │   ├── db.py             # SQLite（aiosqlite）持久化
│   │   ├── svg_parser.py     # SVG/路径/变换解析与曲线离散
│   │   ├── geometry.py       # 矩阵、自交检测（网格索引）、点在多边形内
│   │   ├── scheduler.py      # 嵌套森林 + 先内后外最近邻确定性排程
│   │   └── gcode.py          # G-code 渲染
│   ├── test_core.py          # 几何/解析/排程核心测试（纯标准库）
│   └── test_api.py           # API 端到端测试（TestClient）
└── frontend/
    ├── package.json / tsconfig.json / vite.config.ts
    ├── index.html
    └── src/ (main.ts · api.ts · types.ts · preview.ts · styles.css)
```

---

## 本地开发（不用 Docker）

```bash
# 后端
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
DATABASE_PATH=./dev.db STATIC_DIR=../frontend/dist \
  uvicorn app.main:app --reload --port 8000

# 前端（另开终端，自动代理 /api 到 8000）
cd frontend
npm install
npm run dev        # http://localhost:5173
```

运行测试：

```bash
cd backend && python test_core.py
# API 测试需开发依赖：pip install httpx
python test_api.py
```

前端类型检查 / 生产构建：

```bash
cd frontend && npm run build
```

---

## 备注与限制

- 自交检测基于离散后的折线：圆弧曲线在极极端曲率下理论上可能存在亚步长误差，默认每段 16–24 步对激光加工精度已足够。
- 嵌套判定使用轮廓顶点的点在多边形内测试；若两条轮廓共边 / 共点（贴合边），以包围盒包含作为兜底父级判定。
- 最近邻贪心是对 TSP 式最小空移目标的确定性近似；它满足全部先内后外约束、可复现，但不保证全局最优。
