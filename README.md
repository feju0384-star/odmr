# NV Measurement Console

这是一个参考 LabOne 交互方式的 NV 色心测量系统原型，当前前端已经迁移到 `Vite + React + Mantine + Plotly`。

## 项目结构

- `backend/`：FastAPI 后端，负责设备发现、连接、参数保存和模拟数据
- `frontend/`：Vite React 前端，负责页面路由、表单、图表和交互
- `main.py`：后端启动入口

## 前端页面

- `/device`：设备连接页，扫描并连接锁相和微波源
- `/lockin`：锁相设置页，按通道配置参数并显示实时 X/Y/R 曲线
- `/microwave`：微波设置页，配置 CW、扫频和 FM 调制
- `/odmr`：ODMR 扫描页，支持软件同步和 Aux1 电压映射两种模式

## 启动方式

先启动后端：

```bash
python -m pip install -r requirements.txt
python main.py
```

再启动前端开发服务器：

```bash
cd frontend
npm install
npm run dev
```

默认访问：

- 后端：`http://127.0.0.1:8000`
- 前端：`http://127.0.0.1:5173`

## 当前状态

- 锁相设备发现基于 `zhinst-toolkit`
- 微波源资源发现基于 `PyVISA`
- 锁相、微波和 ODMR 页面都已接到现有后端接口
- 图表已经改成 Plotly

## 仍需继续细化

- 按你的 Zurich 型号补真实节点映射
- 按你的微波源型号补真实 SCPI 指令
- 继续把锁相页做得更像 LabOne 的模块化工作台
