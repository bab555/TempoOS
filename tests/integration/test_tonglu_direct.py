import asyncio
import json
import logging
import time
import uuid
import pytest
import httpx
from httpx import ASGITransport
from redis.asyncio import Redis

# 导入 Tonglu 的 app 和相关设置
from tonglu.main import app, lifespan
from tonglu.config import TongluSettings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 为避免测试污染，我们使用固定的测试租户
TEST_TENANT_ID = "tonglu_test_tenant"
TEST_USER_ID = "tonglu_test_user"

@pytest.fixture(scope="module")
def event_loop():
    """提供一个 module 级别的 event_loop 以便与其他 module 级别的 fixture 共享"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="module")
async def async_app_lifespan():
    """触发 Tonglu 的 lifespan (连接 DB, Redis, 启动后台任务等)"""
    async with lifespan(app):
        yield

@pytest.fixture(scope="module")
async def async_client(async_app_lifespan):
    """提供一个直接连接到 Tonglu FastAPI 实例的测试客户端"""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

@pytest.fixture(scope="module")
async def redis_client(async_app_lifespan):
    """提供一个异步的 Redis 客户端用于操作和断言底层缓存"""
    settings = TongluSettings()
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.mark.asyncio(scope="module")
async def test_tonglu_session_archive_and_restore(async_client: httpx.AsyncClient, redis_client: Redis):
    """
    测试 1: 验证 Tonglu 的 Session Evictor 持久化能力
    流程：
    1. 在 Redis 中伪造一份会话数据
    2. 直接调用 Tonglu 底层 SessionEvictor 进行 archive (保存到 PG)
    3. 清空 Redis 数据 (模拟缓存淘汰)
    4. 调用 Tonglu 的 /session/restore 接口将数据从 PG 恢复回 Redis
    """
    session_id = f"test_evict_{uuid.uuid4().hex[:8]}"
    bb_key = f"tempo:{TEST_TENANT_ID}:session:{session_id}"
    chat_key = f"tempo:{TEST_TENANT_ID}:chat:{session_id}"
    
    # 1. 制造 Redis 假数据
    await redis_client.hset(bb_key, "_chat_summary", "This is a test summary.")
    chat_msg = {"role": "user", "content": "Hello Tonglu!"}
    await redis_client.rpush(chat_key, json.dumps(chat_msg))
    
    logger.info(f"在 Redis 中创建假数据: session_id={session_id}")

    # 2. 调用 Tonglu 内部的 Evictor 手动归档 (模拟时间到了)
    evictor = getattr(app.state, "session_evictor", None)
    assert evictor is not None, "SessionEvictor 必须在配置中启用 (SESSION_EVICTOR_ENABLED=True)"
    
    success = await evictor.archive_session(TEST_TENANT_ID, session_id)
    assert success is True, "归档 Session 失败"
    logger.info("Session 成功归档入 PG 数据库。")

    # 3. 破坏现场：删除 Redis 中的相关键
    await redis_client.delete(bb_key)
    await redis_client.delete(chat_key)
    assert await redis_client.exists(bb_key) == 0, "Blackboard 数据未清空"
    assert await redis_client.exists(chat_key) == 0, "Chat 数据未清空"
    logger.info("已清空 Redis 中的 Session 缓存。")

    # 4. 调用 Tonglu API 请求恢复
    req_data = {
        "tenant_id": TEST_TENANT_ID,
        "session_id": session_id,
        "session_ttl": 3600,
        "chat_ttl": 3600
    }
    resp = await async_client.post("/session/restore", json=req_data)
    assert resp.status_code == 200, f"恢复接口调用失败: {resp.text}"
    
    resp_data = resp.json()
    assert resp_data["restored"] is True, "恢复标志位应该是 True"
    logger.info("调用 /session/restore 接口成功！")

    # 5. 验证 Redis 数据是否真正回来了
    restored_bb = await redis_client.hget(bb_key, "_chat_summary")
    restored_chat = await redis_client.lrange(chat_key, 0, -1)
    
    assert restored_bb == "This is a test summary.", "Blackboard 状态未正确恢复"
    assert len(restored_chat) == 1, "Chat 历史记录条数不对"
    
    chat_msg_restored = json.loads(restored_chat[0])
    assert chat_msg_restored["content"] == "Hello Tonglu!", "Chat 历史内容未正确恢复"
    
    logger.info("✅ 铜炉的 Redis -> PG -> Redis 持久化与恢复测试通过！")


@pytest.mark.asyncio(scope="module")
async def test_tonglu_ingest_and_query(async_client: httpx.AsyncClient):
    """
    测试 2: 验证 Tonglu 数据写入与检索能力 (Knowledge Base & RAG 核心)
    流程：
    1. 调用 /api/ingest/text 接口写入一篇公司的“测试报销规范”文本
    2. 调用 /api/query 接口，使用近似的自然语言问题进行检索，验证能否查到该记录
    """
    test_text = "【测试规定】公司2026年最新报销规定：所有员工的餐饮报销单笔不得超过500元，且必须提供增值税专用发票。"
    
    # 1. 写入数据
    ingest_req = {
        "tenant_id": TEST_TENANT_ID,
        "schema_type": "policy",
        "data": test_text,
        "metadata": {"source": "manual_test"}
    }
    
    logger.info("调用 Tonglu /api/ingest/text 接口写入规范文本...")
    ingest_resp = await async_client.post("/api/ingest/text", json=ingest_req)
    assert ingest_resp.status_code == 200, f"写入失败: {ingest_resp.text}"
    
    record_id = ingest_resp.json().get("record_id")
    assert record_id is not None, "写入未返回 record_id"
    logger.info(f"数据写入成功，record_id = {record_id}")

    # 给 Elasticsearch/PGVector 一点时间构建索引
    await asyncio.sleep(1)

    # 2. 检索数据
    query_req = {
        "tenant_id": TEST_TENANT_ID,
        "query": "员工吃饭报销有什么限制？",
        "mode": "hybrid",
        "limit": 3
    }
    
    logger.info("调用 Tonglu /api/query 接口进行语义检索...")
    query_resp = await async_client.post("/api/query", json=query_req)
    assert query_resp.status_code == 200, f"检索失败: {query_resp.text}"
    
    query_data = query_resp.json()
    results = query_data.get("results", [])
    
    # 我们期望结果中包含了刚才写入的文本内容
    found = False
    for res in results:
        if "500元" in str(res.get("data", "")) or "500元" in str(res.get("summary", "")):
            found = True
            break
            
    assert found is True, "未能在结果中检索出预期的报销规范文本"
    logger.info("✅ 铜炉的知识库文本写入与 RAG 检索测试通过！")


@pytest.mark.asyncio(scope="module")
async def test_tonglu_oss_callback_and_eventbus(async_client: httpx.AsyncClient, redis_client: Redis):
    """
    测试 3: 验证 Tonglu 的 OSS Callback 处理和向 TempoOS 抛出事件的能力
    流程：
    1. 模拟阿里云 OSS 发送 POST 形式的 callback 请求 (包含自定义变量 x:session_id)
    2. 验证 Tonglu 是否正确解析回调，并向 EventBus (Redis Pub/Sub) 抛出了 FILE_READY 事件
    """
    session_id = f"test_oss_{uuid.uuid4().hex[:8]}"
    file_id = f"file_{uuid.uuid4().hex[:8]}"
    
    pubsub = redis_client.pubsub()
    channel_pattern = f"tempo:{TEST_TENANT_ID}:events"
    await pubsub.subscribe(channel_pattern)
    
    # 模拟从 OSS 发过来的 Form Data
    oss_form_data = {
        "bucket": "test-bucket",
        "object": "tempoos/test/test_doc.txt",
        "size": "1024",
        "mimeType": "text/plain",
        "etag": "1234567890",
        "x:tenant_id": TEST_TENANT_ID,
        "x:session_id": session_id,
        "x:user_id": TEST_USER_ID,
        "x:file_id": file_id
    }
    
    logger.info("模拟触发 Tonglu /api/oss/callback 接口...")
    oss_resp = await async_client.post(
        "/api/oss/callback", 
        data=oss_form_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert oss_resp.status_code == 200, f"OSS Callback 调用失败: {oss_resp.text}"
    
    logger.info("OSS Callback 接口已接受请求，等待 Redis EventBus 广播...")
    
    # 监听 EventBus，看是否发出了 FILE_READY
    event_received = False
    try:
        async with asyncio.timeout(5.0):
            async for message in pubsub.listen():
                if message["type"] == "message":
                    event_data = json.loads(message["data"])
                    if event_data.get("type") == "FILE_READY" and event_data.get("session_id") == session_id:
                        logger.info(f"成功监听到 FILE_READY 事件！文件 ID: {event_data.get('payload', {}).get('file_id')}")
                        event_received = True
                        break
    except asyncio.TimeoutError:
        logger.warning("等待 EventBus 广播超时。")
        
    assert event_received is True, "Tonglu 收到 OSS Callback 后未发出 FILE_READY 事件！"
    
    await pubsub.unsubscribe()
    logger.info("✅ 铜炉的 OSS 文件回调处理及事件抛出测试通过！")
