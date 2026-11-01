import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from src.core.websocket_manager import ws_manager
from src.core.security import decode_token

from src.core.config import settings
from src.core.database import engine, execute_query_one
from src.core.redis import init_redis, close_redis, get_redis

from src.core.migrator import run_migrations

# Configure Structured Logger
logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("pbx.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application Lifespan Hook: Startup & Shutdown Tasks
    """
    logger.info("Initializing Multi-Tenant PBX API Control Plane...")
    # Initialize Redis connection pool
    await init_redis()

    # Automatically run pending database migrations
    await run_migrations()
    
    # Synchronize configured Sofia Gateways with FreeSWITCH
    try:
        from src.services.gateway_sync import sync_gateways_to_freeswitch
        await sync_gateways_to_freeswitch()
    except Exception as e:
        logger.warning(f"Initial gateway sync warning: {e}")
    
    # Start Redis Pub/Sub listener for cross-worker QA and telemetry events
    redis_listener_task = asyncio.create_task(ws_manager.start_redis_listener())

    # Start Virtual QA Service (FreeSWITCH ESL listener & audio analysis worker)
    from src.services.virtual_qa_service import qa_service
    try:
        await qa_service.start()
        logger.info("Virtual QA Service initialized and listening to FreeSWITCH ESL.")
    except Exception as e:
        logger.warning(f"Virtual QA service startup warning: {e}")

    yield
    
    logger.info("Shutting down Multi-Tenant PBX API Control Plane...")
    try:
        await qa_service.stop()
    except Exception:
        pass
    if redis_listener_task:
        redis_listener_task.cancel()
    # Close Redis & Database Pools
    await close_redis()
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS Middleware Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from src.api.v1 import auth, tenants, extensions, users
from src.api.v1 import audio, ivr, routing, queues, gateways, trunks, reports, conferences, call_block, contacts, business_hours, dids, hunt_groups, smtp, qa
from src.xml_curl import router as xml_curl_router


# Include API v1 Routers
app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(tenants.router, prefix=settings.API_V1_STR)
app.include_router(extensions.router, prefix=settings.API_V1_STR)
app.include_router(users.router, prefix=settings.API_V1_STR)
app.include_router(audio.router, prefix=settings.API_V1_STR)
app.include_router(ivr.router, prefix=settings.API_V1_STR)
app.include_router(routing.router, prefix=settings.API_V1_STR)
app.include_router(queues.router, prefix=settings.API_V1_STR)
app.include_router(gateways.router, prefix=settings.API_V1_STR)
app.include_router(trunks.router, prefix=settings.API_V1_STR)
app.include_router(reports.router, prefix=settings.API_V1_STR)
app.include_router(conferences.router, prefix=settings.API_V1_STR)
app.include_router(call_block.router, prefix=settings.API_V1_STR)
app.include_router(contacts.router, prefix=settings.API_V1_STR)
app.include_router(business_hours.router, prefix=settings.API_V1_STR)
app.include_router(dids.router, prefix=settings.API_V1_STR)
app.include_router(hunt_groups.router, prefix=settings.API_V1_STR)
app.include_router(smtp.router, prefix=settings.API_V1_STR)
app.include_router(qa.router, prefix=settings.API_V1_STR)

# Include FreeSWITCH mod_xml_curl Router
app.include_router(xml_curl_router.router)


@app.websocket("/ws/reports")
@app.websocket("/ws/reports/")
@app.websocket("/ws/events")
@app.websocket("/ws/events/")
async def websocket_reports_endpoint(websocket: WebSocket):
    """
    Real-time WebSocket endpoint for CDR events, reports telemetry, and live call metrics.
    Accepts query param ?token=<jwt_token> for tenant scoping.
    """
    token = websocket.query_params.get("token")
    user_tenant_id = None
    is_admin = False

    user_role = "agent"
    if token:
        try:
            payload = decode_token(token)
            if payload:
                user_tenant_id = payload.get("tenant_id")
                role = str(payload.get("role") or "").lower()
                user_role = role
                is_admin = (role in ("admin", "super_admin", "superadmin")) or (user_tenant_id is None)
        except Exception as e:
            logger.warning(f"WebSocket token decode failure: {e}")

    await ws_manager.connect(websocket, tenant_id=user_tenant_id, is_admin=is_admin, role=user_role)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket connection error: {e}")
        await ws_manager.disconnect(websocket)


@app.get("/health", tags=["Monitoring"])
async def health_check():
    """
    Liveness probe endpoint. Returns 200 if API service is running.
    """
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT
    }


@app.get("/ready", tags=["Monitoring"])
async def readiness_check():
    """
    Readiness probe endpoint. Verifies PostgreSQL and Redis connectivity.
    """
    db_ok = False
    redis_ok = False

    # Check Database
    try:
        db_res = await execute_query_one("SELECT 1 as alive")
        if db_res and db_res.get("alive") == 1:
            db_ok = True
    except Exception as err:
        logger.error(f"Readiness check - Database failed: {err}")

    # Check Redis
    try:
        r = get_redis()
        if r and await r.ping():
            redis_ok = True
    except Exception as err:
        logger.error(f"Readiness check - Redis failed: {err}")

    is_ready = db_ok  # Database is hard requirement

    response_data = {
        "status": "ready" if is_ready else "not_ready",
        "components": {
            "database": "connected" if db_ok else "disconnected",
            "redis": "connected" if redis_ok else "disconnected"
        }
    }

    if not is_ready:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=response_data)
    return response_data


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler returning clean JSON error responses with CORS headers."""
    logger.error(f"Unhandled Exception on {request.method} {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
        },
        content={
            "detail": f"Internal Server Error: {str(exc)}",
            "error": str(exc)
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
