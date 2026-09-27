from fastapi import FastAPI

app = FastAPI(title="Memory Card Voice Bot")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
