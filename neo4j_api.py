from fastapi import FastAPI
from neo4j import GraphDatabase, basic_auth
from pydantic import BaseModel
from typing import Optional, Dict, Any

app = FastAPI(title="Neo4j 图数据库全功能接口", version="1.0")

# ===================== Neo4j 数据库配置 =====================
config = {
    "uri": "neo4j+s://509c6b62.databases.neo4j.io",
    "user": "509c6b62",
    "password": "CXUsTXOAgZc_dzWW40qnXY56mLerkZLPEHSD9uqNWQ0",
    "database": "509c6b62"
}

driver = GraphDatabase.driver(
    config["uri"],
    auth=basic_auth(config["user"], config["password"])
)

# ===================== 请求模型 =====================
class CypherRequest(BaseModel):
    cypher: str
    params: Optional[Dict[str, Any]] = {}

class CreateNodeRequest(BaseModel):
    label: str
    properties: Dict[str, Any]

class UpdateNodeRequest(BaseModel):
    label: str
    match_properties: Dict[str, Any]
    set_properties: Dict[str, Any]

class DeleteNodeRequest(BaseModel):
    label: str
    match_properties: Dict[str, Any]

# ===================== 支持多条语句 /query =====================
@app.post("/query", summary="执行任意Cypher查询（支持多条语句）")
def run_cypher(req: CypherRequest):
    try:
        # 关键：用 session 手动拆分语句执行
        with driver.session(database=config["database"]) as session:
            # 按分号拆分
            statements = req.cypher.split(';')
            all_records = []
            
            for stmt in statements:
                stmt = stmt.strip()
                if not stmt:
                    continue
                try:
                    result = session.run(stmt, **req.params)
                    records = [dict(record) for record in result]
                    all_records.extend(records)
                except Exception as e:
                    pass

        return {"success": True, "count": len(all_records), "data": all_records}
    
    except Exception as e:
        return {"success": False, "error": str(e)}

# ===================== 2. 新增节点接口 =====================
@app.post("/create/node", summary="新增节点")
def create_node(req: CreateNodeRequest):
    cypher = f"CREATE (n:{req.label} $props) RETURN n"
    try:
        with driver.session(database=config["database"]) as session:
            result = session.run(cypher, props=req.properties)
            return {"success": True, "data": [dict(r) for r in result]}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ===================== 3. 更新节点接口 =====================
@app.post("/update/node", summary="更新节点")
def update_node(req: UpdateNodeRequest):
    match_conditions = ", ".join([f"{key}: ${key}" for key in req.match_properties.keys()])
    cypher = f"""
        MATCH (n:{req.label} {{{match_conditions}}})
        SET n += $set
        RETURN n
    """
    try:
        with driver.session(database=config["database"]) as session:
            params = req.match_properties.copy()
            params["set"] = req.set_properties
            result = session.run(cypher, **params)
            records = [dict(record) for record in result]
            return {"success": True, "count": len(records), "data": records}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ===================== 4. 删除节点接口 =====================
@app.post("/delete/node", summary="删除节点（自动清理关系）")
def delete_node(req: DeleteNodeRequest):
    match_conditions = ", ".join([f"{key}: ${key}" for key in req.match_properties.keys()])
    cypher = f"""
        MATCH (n:{req.label} {{{match_conditions}}})
        DETACH DELETE n
        RETURN count(n) AS deleted_count
    """
    try:
        with driver.session(database=config["database"]) as session:
            result = session.run(cypher, **req.match_properties)
            deleted_count = result.single()["deleted_count"]
            return {
                "success": True,
                "deleted_count": deleted_count,
                "message": f"成功删除 {deleted_count} 个节点（含关联关系）"
            }
    except Exception as e:
        return {"success": False, "error": str(e)}

# ===================== 5. 快捷查询 =====================
@app.get("/list/{label}", summary="查询某类所有节点")
def list_nodes(label: str, limit: int = 50):
    cypher = f"MATCH (n:{label}) RETURN n LIMIT $limit"
    try:
        with driver.session(database=config["database"]) as session:
            result = session.run(cypher, limit=limit)
            return {"success": True, "data": [dict(r) for r in result]}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ===================== 关闭数据库连接 =====================
@app.on_event("shutdown")
def close_driver():
    driver.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)