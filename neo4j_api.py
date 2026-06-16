from fastapi import FastAPI, Request  # 新增Request导入
from neo4j import GraphDatabase, basic_auth
from pydantic import BaseModel
from typing import Optional, Dict, Any, Union

app = FastAPI(title="Neo4j 图数据库全功能接口", version="1.0")

# ===================== Neo4j 数据库配置 =====================
config = {
    "uri": "neo4j+s://4221baaa.databases.neo4j.io",
    "user": "4221baaa",
    "password": "jkhsfQsFqdhjZsn3ybfyfcMkWkH5j_0XWh2DistajE0",
    "database": "4221baaa"
}

driver = GraphDatabase.driver(
    config["uri"],
    auth=basic_auth(config["user"], config["password"])
)

# ===================== Pydantic 请求模型 =====================
# 1. 基础字段实体模型（用于查询、新增Field节点）
class FieldMappingRequest(BaseModel):
    id: int
    system: str
    object: str
    objectdescription: str
    company: str
    fieldname: str
    fieldlabel: Optional[str] = None
    sourcefieldid: Optional[str] = ""
    targetfieldid: Optional[str] = ""
    fielddescription: str
    datatype: str
    mappededr: Optional[str] = ""
    subjectarea: Optional[str] = ""
    createdate: str
    updatedate: str
    fielddescription_upd: str

# 2. 新增：映射/冲突关系写入专用模型（匹配关系Cypher所有入参）
class FieldRelationRequest(BaseModel):
    action: str  # maps_to / conflict / skip
    confidence: float
    srcSystem: str
    srcObject: str
    srcField: str
    tgtSystem: str
    tgtObject: str
    tgtField: str
    mappingType: Optional[str] = ""
    source: Optional[str] = ""
    reasoning: Optional[str] = ""
    dataTypeMismatch: bool
    srcFieldType: str

# 3. 原有通用模型
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

# ===================== 接口1：字段检索接口【修改兼容MCP + 修复Cypher语法】 =====================
@app.post("/field/mapping/search", summary="检索字段映射、等价对象、冲突关系")
async def search_field_mapping(raw_req: Request):
    # 1. 读取原始完整请求报文
    full_body = await raw_req.json()

    # 2. 兼容MCP Client自动嵌套的外层 params
    if "params" in full_body and isinstance(full_body["params"], dict):
        input_data = full_body["params"]
    else:
        # Swagger/普通REST调用，直接取顶层JSON
        input_data = full_body

    # 3. 用模型校验参数
    req = FieldMappingRequest(**input_data)

    search_cypher = """
CALL {
  OPTIONAL MATCH (s:Object {system: $system, name: $object})-[:SEMANTIC_EQUIVALENT]->(t:Object {system: 'LSC'})
  RETURN collect(DISTINCT { system: t.system, object: t.name }) AS candidates
}
CALL {
  OPTIONAL MATCH (src:Field {system: $system, object: $object, name: $fieldname})
  OPTIONAL MATCH (src)-[m:MAPS_TO]->(mapped:Field)
  OPTIONAL MATCH (src)-[c:CONFLICTS_WITH]->(conflict:Field)
  RETURN
    src.name AS sourceField,
    src.object AS sourceObject,
    src.system AS sourceSystem,
    src.displayName AS sourceFieldLabel,
    src.type AS sourceFieldType,
    filter(
      item IN collect(DISTINCT CASE WHEN m IS NOT NULL THEN {
        target: m.system + '.' + m.object + '.' + m.name,
        targetSystem: m.system,
        targetObject: m.object,
        targetField: m.name,
        confidence: m.confidence,
        mappingType: m.mappingType,
        source: m.source,
        validatedBy: m.validatedBy,
        isPrimary: m.isPrimary,
        updatedAt: toString(m.updatedAt)
      } END)
      WHERE item IS NOT NULL
    ) AS existingMappings,
    filter(
      item IN collect(DISTINCT CASE WHEN c IS NOT NULL THEN {
        target: c.system + '.' + c.object + '.' + c.name,
        conflictType: c.conflictType,
        severity: c.severity,
        description: c.conflictDescription,
        resolutionStatus: c.resolutionStatus
      } END)
      WHERE item IS NOT NULL
    ) AS conflicts
}
RETURN candidates, sourceField, sourceObject, sourceSystem, existingMappings, conflicts,
  coalesce(sourceFieldLabel, $fieldname) + '. ' + $fielddescription AS searchQuery,
  sourceFieldLabel, sourceFieldType
    """
    cypher_params = {
        "system": req.system,
        "object": req.object,
        "fieldname": req.fieldname,
        "fielddescription": req.fielddescription
    }
    try:
        with driver.session(database=config["database"]) as session:
            result = session.run(search_cypher, **cypher_params)
            records = [dict(record) for record in result]
        return {"success": True, "count": len(records), "data": records}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ===================== 接口2：新增【映射/冲突关系写入接口】兼容MCP =====================
@app.post("/field/mapping/relation-upsert", summary="写入MAPS_TO/CONFLICTS_WITH关系")
async def upsert_field_relation(raw_req: Request):
    # 读取原始请求体，兼容MCP外层params包裹
    full_body = await raw_req.json()
    if "params" in full_body and isinstance(full_body["params"], dict):
        input_data = full_body["params"]
    else:
        input_data = full_body
    # 校验模型
    req = FieldRelationRequest(**input_data)

    relation_cypher = """
WITH $action AS action, $confidence AS conf,
     $srcSystem AS srcSys, $srcObject AS srcObj, $srcField AS srcFld,
     $tgtSystem AS tgtSys, $tgtObject AS tgtObj, $tgtField AS tgtFld,
     $mappingType AS mapType, $source AS src, $reasoning AS reason,
     $dataTypeMismatch AS dtMismatch, $srcFieldType AS srcType

OPTIONAL MATCH (s:Field {system: srcSys, object: srcObj, name: srcFld})
OPTIONAL MATCH (t:Field {system: tgtSys, object: tgtObj, name: tgtFld})
  WHERE tgtObj <> '' AND tgtFld <> ''

FOREACH (_ IN CASE WHEN action = 'maps_to' AND conf >= 0.7 AND s IS NOT NULL AND t IS NOT NULL THEN [1] ELSE [] END |
  MERGE (s)-[r:MAPS_TO]->(t)
  SET r.confidence = conf, r.mappingType = mapType, r.source = src,
      r.reasoning = reason, r.isPrimary = true,
      r.dataTypeMismatch = dtMismatch,
      r.createdAt = coalesce(r.createdAt, datetime()), r.updatedAt = datetime()
)

FOREACH (_ IN CASE WHEN action = 'maps_to' AND conf >= 0.5 AND conf < 0.7 AND s IS NOT NULL AND t IS NOT NULL THEN [1] ELSE [] END |
  MERGE (s)-[r:MAPS_TO]->(t)
  SET r.confidence = conf, r.mappingType = mapType, r.source = src,
      r.reasoning = reason, r.isPrimary = false, r.requiresReview = true,
      r.dataTypeMismatch = dtMismatch,
      r.createdAt = coalesce(r.createdAt, datetime()), r.updatedAt = datetime()
)

FOREACH (_ IN CASE WHEN action = 'conflict' AND s IS NOT NULL AND t IS NOT NULL THEN [1] ELSE [] END |
  MERGE (s)-[c:CONFLICTS_WITH]->(t)
  SET c.conflictDescription = reason, c.severity = 'medium',
      c.resolutionStatus = 'open',
      c.detectedAt = coalesce(c.detectedAt, datetime()),
      c.createdAt = coalesce(c.createdAt, datetime())
)

RETURN
  srcObj AS `Veeva Entity Name`,
  srcFld AS `Veeva Field Name`,
  coalesce(CASE WHEN s IS NOT NULL THEN s.type ELSE srcType END, srcType, '') AS `Veeva Field Type`,
  CASE WHEN dtMismatch = true THEN 'Yes' ELSE '' END AS `Data Type Mismatch`,
  CASE
    WHEN action = 'maps_to' AND conf >= 0.7 AND t IS NOT NULL THEN 'Yes'
    WHEN action = 'maps_to' AND conf >= 0.5 AND conf < 0.7 AND t IS NOT NULL THEN 'Pending Review'
    WHEN action = 'skip' THEN 'Skip'
    WHEN action = 'maps_to' AND conf < 0.5 THEN 'No'
    ELSE 'No'
  END AS `Mapped?`,
  CASE WHEN t IS NOT NULL THEN tgtObj ELSE tgtObj END AS `Life Sciences EntityName`,
  CASE WHEN t IS NOT NULL THEN coalesce(t.displayName, tgtFld) ELSE tgtFld END AS `Life Sciences Field Label`,
  CASE WHEN t IS NOT NULL THEN coalesce(t.apiName, t.name, tgtFld) ELSE tgtFld END AS `Life Sciences Field API Name`,
  CASE WHEN t IS NOT NULL THEN coalesce(t.type, '') ELSE '' END AS `Life Sciences FieldType`,
  CASE WHEN t IS NOT NULL THEN coalesce(t.specifications, '') ELSE '' END AS `Any specifications for field type`,
  conf AS `Confidence`,
  CASE
    WHEN action = 'maps_to' AND conf >= 0.7 THEN ''
    WHEN action = 'maps_to' AND conf >= 0.5 AND conf < 0.7 THEN 'Yes'
    WHEN action = 'skip' THEN ''
    ELSE 'Yes'
  END AS `Requires Review`,
  reason AS `Additional Comments/Recommendations`
    """
    # 模型自动转参数字典，直接传入Cypher
    cypher_params = req.model_dump()
    try:
        with driver.session(database=config["database"]) as session:
            result = session.run(relation_cypher, **cypher_params)
            records = [dict(record) for record in result]
        return {
            "success": True,
            "count": len(records),
            "data": records
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# ===================== 原有通用底层接口（无修改） =====================
@app.post("/query", summary="执行任意Cypher查询（支持多条语句）")
def run_cypher(req: CypherRequest):
    try:
        with driver.session(database=config["database"]) as session:
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
                except Exception:
                    continue
        return {"success": True, "count": len(all_records), "data": all_records}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/create/node", summary="新增节点")
def create_node(req: CreateNodeRequest):
    cypher = f"CREATE (n:{req.label} $props) RETURN n"
    try:
        with driver.session(database=config["database"]) as session:
            result = session.run(cypher, props=req.properties)
            return {"success": True, "data": [dict(r) for r in result]}
    except Exception as e:
        return {"success": False, "error": str(e)}

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

@app.get("/list/{label}", summary="查询某类所有节点")
def list_nodes(label: str, limit: int = 50):
    cypher = f"MATCH (n:{label}) RETURN n LIMIT $limit"
    try:
        with driver.session(database=config["database"]) as session:
            result = session.run(cypher, limit=limit)
            return {"success": True, "data": [dict(r) for r in result]}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.on_event("shutdown")
def close_driver():
    driver.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)