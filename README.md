# 电商 ChatBI 工作台

用自然语言探索 UCI Online Retail 电商数据。工作台把问题转成可确认的指标、日期和筛选条件，确认后执行受控查询，展示表格、图表和查询依据。

## 本地运行

在项目根目录使用 Python 3.12 和支持 Vite 8 的 Node.js。建议使用新的 Python 虚拟环境。

```powershell
python -m pip install -r backend/requirements.lock.txt
cd frontend
npm ci
npm run build
cd ..
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

打开 http://127.0.0.1:8765/ ，点击“结果预览”可查看合成示例。启用自然语言解析时，在本地把 `.env.example` 复制为 `.env`，填写自己的百炼北京工作区地址和密钥，再重启服务。启用模型后，问题与相关上下文会发往配置的模型服务。

## 使用公开交易数据

从 [UCI Online Retail](https://doi.org/10.24432/C5BW33) 下载 Daqing Chen 的数据集（CC BY 4.0），将解压的工作簿放到 `data/raw/Online Retail.xlsx`。运行 `python scripts/import_public_data.py` 以校验源文件并生成本地查询库，然后重启服务。仓库包含数据来源、汇总统计、指标定义与示例，不分发交易明细或会话记录。

在项目根目录运行 `python -m pytest` 可执行公开版的后端回归测试。
