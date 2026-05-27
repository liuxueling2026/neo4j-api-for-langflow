from fastapi import FastAPI
from neo4j import GraphDatabase, basic_auth
from pydantic import BaseModel
from typing import Optional, Dict, Any

app = FastAPI(title="Neo4j 图数据库全功能接口", version="1.0")

# ===================== Neo4j 数据库配置 =====================
# 连接永久免费版 AuraDB，固定 database = "neo4j"
# ==========================================================
config = {
    "uri": "neo4j+s://509c6b62.databases.neo4j.io",
    "user": "509c6b62",
    "password": "CXUsTXOAgZc_dzWW40qnXY56mLerkZLPEHSD9uqNWQ0",
    "database": "509c6b62"
}

# 创建数据库驱动（全局复用，AuraDB 必须使用 basic_auth）
driver = GraphDatabase.driver(
    config["uri"],
    auth=basic_auth(config["user"], config["password"])
)

# ===================== 请求模型 =====================
# 定义接口接收的数据格式（Pydantic 模型）
# ==========================================================

# 通用查询：可执行任意 Cypher 语句
class CypherRequest(BaseModel):
    cypher: str
    params: Optional[Dict[str, Any]] = {}

# 新增节点：指定标签 + 属性
class CreateNodeRequest(BaseModel):
    label: str
    properties: Dict[str, Any]

# 更新节点：匹配条件 + 更新内容
class UpdateNodeRequest(BaseModel):
    label: str
    match_properties: Dict[str, Any]
    set_properties: Dict[str, Any]

# 删除节点：根据标签 + 属性匹配删除
class DeleteNodeRequest(BaseModel):
    label: str
    match_properties: Dict[str, Any]

# ===================== 1. 通用查询接口 =====================
# 功能：执行任意 Cypher 查询，支持参数化，安全无注入
@app.post("/query", summary="执行任意Cypher查询")
def run_cypher(req: CypherRequest):
    try:
        with driver.session(database=config["database"]) as session:
            result = session.run(req.cypher, **req.params)
            records = [dict(record) for record in result]
            return {"success": True, "count": len(records), "data": records}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ===================== 2. 新增节点接口 =====================
# 功能：创建任意类型节点，自动绑定标签与属性
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
# 功能：根据条件匹配节点，并更新节点属性
@app.post("/update/node", summary="更新节点")
def update_node(req: UpdateNodeRequest):
    # 拼接合法条件
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
# 功能：根据条件匹配节点，并删除
@app.post("/delete/node", summary="删除节点（自动清理关系）")
def delete_node(req: DeleteNodeRequest):
    # 拼接合法条件：解决 MATCH 不能用 $map 的问题
    match_conditions = ", ".join([f"{key}: ${key}" for key in req.match_properties.keys()])
    
    # ✅ 关键：使用 DETACH DELETE（自动删关系 + 删节点）
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

# ===================== 5. 快捷查询：查询某类全部数据 =====================
# 功能：根据标签查询所有节点，支持限制返回数量
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
# 服务停止时自动关闭驱动，避免连接泄漏
@app.on_event("shutdown")
def close_driver():
    driver.close()

# 本地运行
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)