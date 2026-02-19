# Plan 10：铜炉处理 Pipeline 与查询引擎
> **Task ID**: `10_tonglu_pipeline`
> **目标**: 实现文件解析器、数据摄入 Pipeline（20 并发）、混合查询引擎
> **依赖**: `09_tonglu_scaffold`（骨架 + PG + LLM Service 就绪）
> **预估**: 4 天
> **参考**: `TONGLU_V2_DEV_GUIDE.md` 第 4 章

---

## 背景

Plan 09 完成了铜炉的"地基"（目录、数据库、LLM 封装）。本 Plan 实现铜炉的两大核心能力：

1. **数据摄入 Pipeline**: 文件/文本进来 → 解析 → LLM 提取字段 → 向量化 → 入库
2. **查询引擎**: SQL 精确查询 + 向量语义检索 + 混合模式

Phase 1 采用简单线性 Pipeline + asyncio Semaphore 控制 20 并发，不使用 LangGraph。

---

## 步骤

### 1. 文件解析器基类 (`tonglu/parsers/base.py`)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ParseResult:
    """解析器输出"""
    text: str               # 提取的文本内容
    metadata: dict = None   # 解析元信息（页数、表格数等）
    tables: list = None     # 提取的表格数据（如有）

class BaseParser(ABC):
    @abstractmethod
    async def parse(self, content_ref: str, **kwargs) -> ParseResult:
        """解析文件/内容，返回文本"""
        ...
    
    @abstractmethod
    def supports(self, file_name: str, source_type: str) -> bool:
        """判断是否支持该文件类型"""
        ...
```

### 2. PDF 解析器 (`tonglu/parsers/pdf_parser.py`)

```python
import pdfplumber

class PDFParser(BaseParser):
    async def parse(self, content_ref: str, **kwargs) -> ParseResult:
        # pdfplumber 是阻塞 IO，建议放到线程池，避免阻塞事件循环
        def _read_pdf(path: str) -> ParseResult:
            text_parts = []
            tables = []
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
                    page_tables = page.extract_tables()
                    if page_tables:
                        tables.extend(page_tables)
                pages = len(pdf.pages)
            return ParseResult(
                text="\n".join(text_parts),
                metadata={"pages": pages, "has_tables": len(tables) > 0},
                tables=tables,
            )

        return await asyncio.to_thread(_read_pdf, content_ref)
    
    def supports(self, file_name: str, source_type: str) -> bool:
        return file_name and file_name.lower().endswith(".pdf")
```

### 3. Excel 解析器 (`tonglu/parsers/excel_parser.py`)

```python
import openpyxl

class ExcelParser(BaseParser):
    async def parse(self, content_ref: str, **kwargs) -> ParseResult:
        wb = await asyncio.to_thread(openpyxl.load_workbook, content_ref, data_only=True)
        text_parts = []
        tables = []
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            rows = []
            for row in ws.iter_rows(values_only=True):
                row_data = [str(cell) if cell is not None else "" for cell in row]
                rows.append(row_data)
                text_parts.append(" | ".join(row_data))
            tables.append({"sheet": sheet, "rows": rows})
        return ParseResult(
            text="\n".join(text_parts),
            metadata={"sheets": wb.sheetnames, "total_rows": sum(len(t["rows"]) for t in tables)},
            tables=tables,
        )
    
    def supports(self, file_name: str, source_type: str) -> bool:
        return file_name and file_name.lower().endswith((".xlsx", ".xls"))
```

### 4. 图片解析器 (`tonglu/parsers/image_parser.py`)

```python
class ImageParser(BaseParser):
    """通过 DashScope Qwen-VL 解析图片内容"""
    
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service
    
    async def parse(self, content_ref: str, **kwargs) -> ParseResult:
        # 调用 Qwen-VL 提取图片中的文字和结构化信息
        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "image": content_ref},
                {"type": "text", "text": "请提取图片中的所有文字内容和关键信息，"
                                          "包括表格、数字、日期、金额等。以纯文本形式输出。"}
            ]
        }]
        text = await self.llm.call(task_type="vision", messages=messages)
        return ParseResult(
            text=text,
            metadata={"source": "qwen-vl", "image_path": content_ref},
        )
    
    def supports(self, file_name: str, source_type: str) -> bool:
        if not file_name:
            return False
        return file_name.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".tiff"))
```

### 5. 解析器注册表

```python
class ParserRegistry:
    """根据文件类型选择解析器"""
    
    def __init__(self, llm_service: LLMService):
        self._parsers: list[BaseParser] = [
            PDFParser(),
            ExcelParser(),
            ImageParser(llm_service),
        ]
    
    def get_parser(self, file_name: str, source_type: str) -> BaseParser:
        for parser in self._parsers:
            if parser.supports(file_name, source_type):
                return parser
        # 兜底：纯文本直接返回
        return TextParser()


class TextParser(BaseParser):
    """兜底解析器：直接把 content_ref 当文本返回"""

    async def parse(self, content_ref: str, **kwargs) -> ParseResult:
        return ParseResult(text=content_ref, metadata={"parser": "text"})

    def supports(self, file_name: str, source_type: str) -> bool:
        return source_type in ("text", "event") or not file_name
```

### 6. 摄入 Pipeline (`tonglu/pipeline/ingestion.py`)

这是铜炉的核心处理流程：

```python
import asyncio
from dataclasses import dataclass
from uuid import UUID

@dataclass
class IngestionResult:
    source_id: UUID
    record_id: UUID
    status: str       # "ready" / "error"
    error: str = None

class IngestionPipeline:
    """数据摄入流水线 — 20 并发控制"""
    
    def __init__(self, parser_registry, llm_service, repo, max_concurrent: int = 20):
        self._parsers = parser_registry
        self._llm = llm_service
        self._repo = repo
        self._semaphore = asyncio.Semaphore(max_concurrent)
    
    async def process(self, source_type: str, content_ref: str,
                      file_name: str = None, tenant_id: str = None,
                      schema_type: str = None, metadata: dict = None) -> IngestionResult:
        """处理单条数据"""
        async with self._semaphore:
            source = None
            try:
                # Step 1: 保存原始数据
                source = await self._repo.save_source(DataSource(
                    tenant_id=tenant_id, source_type=source_type,
                    file_name=file_name, content_ref=content_ref,
                    metadata=metadata or {},
                ))
                
                # Step 2: 解析内容
                parser = self._parsers.get_parser(file_name, source_type)
                parse_result = await parser.parse(content_ref)
                
                # Step 3: LLM 识别类型（如果未指定）
                if not schema_type:
                    schema_type = await self._detect_type(parse_result.text)
                
                # Step 4: LLM 字段提取 + 摘要
                extracted = await self._extract_fields(parse_result.text, schema_type)
                
                # Step 5: 向量化
                vectors = await self._llm.embed([extracted["summary"]])
                
                # Step 6: 持久化
                record = await self._repo.save_record(DataRecord(
                    tenant_id=tenant_id, source_id=source.id,
                    schema_type=schema_type, data=extracted["fields"],
                    summary=extracted["summary"], status="ready",
                ))
                await self._repo.save_vectors([DataVector(
                    record_id=record.id,
                    chunk_content=extracted["summary"],
                    embedding=vectors[0],
                )])
                
                return IngestionResult(source.id, record.id, "ready")
                
            except Exception as e:
                # 记录错误，更新状态（Phase 1：返回错误即可；Phase 2 可写入 processing_log）
                return IngestionResult(source.id if source else None, None, "error", str(e))
    
    async def process_batch(self, items: list[dict]) -> list[IngestionResult]:
        """批量处理，受 semaphore 控制并发上限"""
        tasks = [self.process(**item) for item in items]
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _detect_type(self, text: str) -> str:
        """LLM 识别数据类型"""
        messages = [{
            "role": "user",
            "content": f"判断以下文本属于哪种业务数据类型。"
                       f"只返回类型名称，可选值：invoice, contract, contact, "
                       f"quotation, meeting_note, report, other\n\n{text[:500]}"
        }]
        result = await self._llm.call(task_type="route", messages=messages)
        return result.strip().lower()
    
    async def _extract_fields(self, text: str, schema_type: str) -> dict:
        """LLM 提取字段 + 生成摘要"""
        messages = [{
            "role": "system",
            "content": f"你是一个数据提取专家。从文本中提取 {schema_type} 类型的关键字段，"
                       f"并生成一段 50 字以内的摘要。"
                       f"返回 JSON 格式：{{\"fields\": {{...}}, \"summary\": \"...\"}}"
        }, {
            "role": "user",
            "content": text[:3000]  # 截断防止 token 过多
        }]
        result = await self._llm.call(task_type="extract", messages=messages)
        return json.loads(result)
```

### 7. 查询引擎 (`tonglu/query/engine.py`)

```python
class QueryEngine:
    """统一查询引擎 — SQL + 向量 + 混合"""
    
    def __init__(self, repo: DataRepository, llm_service: LLMService):
        self._repo = repo
        self._llm = llm_service
    
    async def query(self, intent: str, mode: str = "hybrid",
                    filters: dict = None, tenant_id: str = None,
                    limit: int = 20) -> list[dict]:
        if mode == "sql":
            return await self._sql_query(intent, filters, tenant_id, limit)
        elif mode == "vector":
            return await self._vector_query(intent, tenant_id, limit)
        else:  # hybrid
            sql_results = await self._sql_query(intent, filters, tenant_id, limit)
            vec_results = await self._vector_query(intent, tenant_id, limit)
            return self._merge_and_rank(sql_results, vec_results, limit)
    
    async def _sql_query(self, intent: str, filters: dict,
                         tenant_id: str, limit: int) -> list[dict]:
        """基于 JSONB 字段的精确查询"""
        # 如果 filters 已经是结构化的，直接查
        if filters:
            return await self._repo.list_records(
                tenant_id=tenant_id,
                schema_type=filters.get("schema_type"),
                offset=0, limit=limit,
                # 额外 JSONB 条件由 repo 层处理
                data_filters=filters.get("data_conditions"),
            )
        # 否则用 LLM 将自然语言转为查询条件
        conditions = await self._intent_to_filters(intent)
        return await self._repo.list_records(
            tenant_id=tenant_id, **conditions, limit=limit
        )
    
    async def _vector_query(self, intent: str, tenant_id: str, limit: int) -> list[dict]:
        """语义向量检索"""
        query_embedding = (await self._llm.embed([intent]))[0]
        return await self._repo.vector_search(
            embedding=query_embedding, tenant_id=tenant_id, limit=limit
        )
    
    async def _intent_to_filters(self, intent: str) -> dict:
        """LLM 将自然语言查询意图转为结构化过滤条件"""
        messages = [{
            "role": "system",
            "content": "将用户的查询意图转为 JSON 过滤条件。"
                       "可用字段：schema_type, data_conditions (JSONB 路径条件)。"
                       "返回 JSON 格式。"
        }, {
            "role": "user",
            "content": intent
        }]
        result = await self._llm.call(task_type="route", messages=messages)
        return json.loads(result)
    
    def _merge_and_rank(self, sql_results: list, vec_results: list,
                        limit: int) -> list[dict]:
        """合并 SQL 和向量结果，去重 + 排序"""
        seen = set()
        merged = []
        # SQL 结果优先（精确匹配权重高）
        for r in sql_results:
            rid = str(r.get("id", ""))
            if rid not in seen:
                seen.add(rid)
                r["_match_type"] = "sql"
                merged.append(r)
        # 向量结果补充
        for r in vec_results:
            rid = str(r.get("id", ""))
            if rid not in seen:
                seen.add(rid)
                r["_match_type"] = "vector"
                merged.append(r)
        return merged[:limit]
```

---

## 测试

### 解析器测试

- `tests/test_pdf_parser.py`: 用测试 PDF 验证文本和表格提取
- `tests/test_excel_parser.py`: 用测试 Excel 验证多 sheet 解析
- `tests/test_image_parser.py`: Mock Qwen-VL 调用，验证图片解析流程

### Pipeline 测试

- `tests/test_ingestion_pipeline.py`:
  - 端到端：输入文本 → 验证 source/record/vector 三表写入
  - 并发：同时提交 30 个任务，验证 semaphore 限制为 20
  - 错误处理：模拟 LLM 调用失败，验证 status="error" 且不影响其他任务
  - 类型识别：不传 schema_type，验证 LLM 自动识别

### 查询引擎测试

- `tests/test_query_engine.py`:
  - SQL 模式：插入测试数据，按 schema_type 和 JSONB 条件查询
  - Vector 模式：插入测试向量，验证语义相似度检索
  - Hybrid 模式：验证合并去重逻辑
  - 自然语言转条件：Mock LLM，验证 intent → filters 转换

---

## 验收

- [ ] PDF 文件可解析出文本和表格
- [ ] Excel 文件可解析出多 sheet 数据
- [ ] 图片文件可通过 Qwen-VL 提取文字内容
- [ ] 摄入 Pipeline 端到端跑通：文件 → 解析 → LLM 提取 → 向量化 → 三表写入
- [ ] 同时提交 30 个文件，实际并行不超过 20 个
- [ ] 未指定 schema_type 时，LLM 可自动识别数据类型
- [ ] SQL 查询可按字段精确检索
- [ ] Vector 查询可按语义相似度检索
- [ ] Hybrid 查询可合并两种结果并去重
- [ ] 单条数据处理失败不影响批量中的其他数据
- [ ] 所有单元测试通过
