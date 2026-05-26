from fastapi import FastAPI
from neo4j import GraphDatabase
from pydantic import BaseModel

app = FastAPI()

# ===================== 你的 Neo4j 配置（不变）=====================
config = {
    "uri": "neo4j+s://4221baaa.databases.neo4j.io",
    "user": "4221baaa",
    "password": "jkhsfQsFqdhjZsn3ybfyfcMkWkH5j_0XWh2DistajE0",
    "database": "4221baaa"
}
# =================================================================

# 创建连接
driver = GraphDatabase.driver(
    config["uri"],
    auth=(config["user"], config["password"])
)

# 接收请求的格式
class QueryRequest(BaseModel):
    cypher: str
    params: dict = {}

# 接口：执行 Cypher 查询
@app.post("/query")
def run_cypher(req: QueryRequest):
    try:
        with driver.session(database=config["database"]) as session:
            result = session.run(req.cypher, **req.params)
            return {"success": True, "data": [dict(r) for r in result]}
    except Exception as e:
        return {"success": False, "error": str(e)}

# 本地测试用（云端会自动忽略，不影响）
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)