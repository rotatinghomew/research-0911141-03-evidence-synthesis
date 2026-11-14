# 重复研究证据簿

用于教研人员查看本地记录、选择业务范围、核对原始数据并保存复核材料。页面既展示研究组别的原始比较，也提供跨研究整合：选择结果指标与时间窗口后，可以看到按量表分开的总体效应、研究间差异（Q、I²、tau²、预测区间）和适用范围，并可展开每项贡献来自哪些原始组别。所有示例都是本地生成的虚构记录，不含个人信息、真实研究数据或受限材料。

整合不会为了让数字好看而合并不能合并的资料：不同量表分别报告；缺少离散程度、已声明未报告或缺少记录的研究只并列呈现，不补零；共用学员的研究按所选策略处理，三种策略并列可查。重复测量相关性未知时只做假设比较，并明确标注该值不是观测。页面同时给出固定与随机效应，不预设唯一合并方法。

## 运行

需要 Python 3.11，运行不依赖第三方服务或第三方包。推荐使用锁定基础镜像的容器；也可以在项目根目录直接启动。

```bash
python3 -m evidence_synthesis --host 127.0.0.1 --port 8080
```

浏览器打开 `http://127.0.0.1:8080`。状态默认保存在 `.state/research.sqlite3`，可用 `--state` 指定独立目录。页面提供范围选择、表格和图、原始记录查看、跨研究整合与敏感性分析、完整数据导入、保存和导出。保存的是数据版本、选择条件与当时的描述（含整合结果），不会因后续导入变化。

```bash
docker build -t evidence_synthesis:local .
docker run --rm -p 8080:8080 -v evidence_synthesis-state:/app/.state evidence_synthesis:local
```

## 检查

```bash
python3 -m unittest discover -s tests -v
```

回归覆盖数据口径、合并条件、未报告与缺记录的区分、量表分离、重复证据处理、相关性假设、敏感性分析、HTTP 交互、非法导入回滚、不可变版本与重启后的历史读取。测试使用临时状态目录，不改变演示数据。详见 `docs/data-contract.md`、`docs/http.md` 和 `docs/walkthrough.md`。如果扩展运行依赖，请同时在依赖清单和镜像中固定版本。

## 构建与浏览器回归

服务只使用 Python 标准库。下列固定版本工具用于构建和自动浏览器检查，不是运行服务的前置依赖。

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-build.txt -r requirements-test.txt
python3 -m playwright install --with-deps chromium
python3 -m build --wheel --no-isolation
python3 tests/browser_smoke.py
```

自动浏览器检查在随机本地端口启动真实应用，使用临时数据库验证范围切换、总体效应与贡献展开、并列状态、敏感性展开、原始记录、保存复开、导出和幂等导入；结束后关闭浏览器和服务。它不要求预先启动服务器，也不依赖外部数据。`npm start` 是现有 Python 服务的启动别名，支持附加 `--host`、`--port` 参数；`npm run test:browser` 调用同一浏览器回归。npm 锁文件不引入 Node 运行包。

支持 CPython 3.11 系列，产品镜像仍由不可变 digest 固定至同一 Python 3.11.14 镜像；运行兼容性不依赖补丁版本专有行为。上述构建工具和测试工具均通过固定版本清单预装，正式启动和浏览器操作无须下载。
