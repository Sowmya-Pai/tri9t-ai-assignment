"""
FastAPI backend for document management and test case generation.

Endpoints:
- POST /documents/ingest - Ingest PDF
- GET /documents/{doc_id}/versions/{version}/sections - List top-level sections
- GET /documents/{doc_id}/versions/{version}/nodes/{node_id} - Get specific node
- GET /documents/{doc_id}/search - Full-text search
- GET /documents/{doc_id}/nodes/{node_id}/changes - See what changed
- POST /selections - Create version-pinned selection
- GET /selections/{selection_id} - Get selection
- POST /selections/{selection_id}/generate - Generate test cases
- GET /selections/{selection_id}/generations - Get all generations
- GET /generations/{generation_id}/staleness-report - Check staleness
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import os
import tempfile
from enum import Enum

from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, and_, or_
from sqlalchemy.orm import Session, sessionmaker
import httpx

from models import (
    Base, Document, DocumentVersion, Node, Selection, SelectionNode,
    Generation, TestCase, StalenessCheck, NodeMapping,
    ChangeType, StalenessLevel, Priority, LLMProvider
)
from parser import DocumentParser, NodeData
from versioning import VersionMatcher, NodeVersionTracker

# ==================== Configuration ====================

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./tri9t.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Document Test Case Generator API", version="1.0.0")


# ==================== Pydantic Models (Request/Response) ====================

class NodeResponse(BaseModel):
    """API response for a document node"""
    id: str
    heading: str
    level: int
    body_text: Optional[str] = None
    content_hash: str
    children: List['NodeResponse'] = Field(default_factory=list)
    parent_id: Optional[str] = None
    created_at: str
    last_modified_at: str
    ocr_confidence: Optional[float] = None
    
    class Config:
        from_attributes = True


class ChangeResponse(BaseModel):
    """Response for change between versions"""
    v1_text: Optional[str] = None
    v2_text: Optional[str] = None
    change_type: str
    diff_summary: Optional[str] = None
    similarity_score: float


class TestCaseResponse(BaseModel):
    """API response for a test case"""
    id: str
    test_name: str
    preconditions: str
    steps: List[str]
    expected_result: str
    priority: str
    staleness_status: str
    staleness_confidence: float
    staleness_summary: Optional[str] = None
    
    class Config:
        from_attributes = True


class GenerationResponse(BaseModel):
    """API response for a generation"""
    id: str
    selection_id: str
    generated_at: str
    test_cases: List[TestCaseResponse]
    error_message: Optional[str] = None
    
    class Config:
        from_attributes = True


class SelectionCreateRequest(BaseModel):
    """Request to create a selection"""
    document_id: str
    name: str
    description: Optional[str] = None
    node_ids: List[str]


class GenerateTestCasesRequest(BaseModel):
    """Request to generate test cases"""
    selection_id: str
    llm_provider: str = "groq"  # groq, gemini, openai, anthropic


# ==================== Helper Functions ====================

def get_db():
    """Dependency: get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def node_to_response(node: Node, include_children: bool = True) -> NodeResponse:
    """Convert Node model to API response"""
    children = []
    if include_children and node.children:
        children = [node_to_response(child, include_children=True) for child in node.children]
    
    return NodeResponse(
        id=node.id,
        heading=node.heading,
        level=node.level,
        body_text=node.body_text,
        content_hash=node.content_hash,
        children=children,
        parent_id=node.parent_id,
        created_at=node.created_at.isoformat(),
        last_modified_at=node.last_modified_at.isoformat(),
        ocr_confidence=node.ocr_confidence
    )


def reconstruct_selection_text(db: Session, selection: Selection) -> str:
    """Reconstruct full text from selected nodes"""
    parts = []
    
    for sel_node in selection.selection_nodes:
        node = sel_node.node
        parts.append(f"# {node.heading}\n\n{node.body_text}\n")
    
    return "\n".join(parts)


async def call_llm_for_test_cases(text: str, provider: str, 
                                  system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    """
    Call LLM API to generate test cases.
    
    Returns: {
        "success": bool,
        "raw_output": str,
        "parsed_output": {...},
        "error": str (if failed)
    }
    """
    
    if provider == "groq":
        return await _call_groq(text, system_prompt, user_prompt)
    elif provider == "gemini":
        return await _call_gemini(text, system_prompt, user_prompt)
    else:
        return {
            "success": False,
            "error": f"Unknown LLM provider: {provider}"
        }


async def _call_groq(text: str, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    """Call Groq API"""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {"success": False, "error": "GROQ_API_KEY not set"}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "mixtral-8x7b-32768",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt + "\n\n" + text}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 2000
                },
                timeout=30.0
            )
            
            if response.status_code != 200:
                return {"success": False, "error": f"Groq API error: {response.text}"}
            
            data = response.json()
            raw_output = data['choices'][0]['message']['content']
            
            # Try to parse as JSON
            try:
                parsed = json.loads(raw_output)
                return {
                    "success": True,
                    "raw_output": raw_output,
                    "parsed_output": parsed
                }
            except json.JSONDecodeError:
                return {
                    "success": False,
                    "raw_output": raw_output,
                    "error": "Failed to parse LLM output as JSON",
                    "parsed_output": None
                }
    
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _call_gemini(text: str, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    """Call Google Gemini API"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"success": False, "error": "GEMINI_API_KEY not set"}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={api_key}",
                json={
                    "contents": [{
                        "parts": [{
                            "text": user_prompt + "\n\n" + text
                        }]
                    }],
                    "systemInstruction": {
                        "parts": [{"text": system_prompt}]
                    }
                },
                timeout=30.0
            )
            
            if response.status_code != 200:
                return {"success": False, "error": f"Gemini API error: {response.text}"}
            
            data = response.json()
            raw_output = data['candidates'][0]['content']['parts'][0]['text']
            
            # Try to parse as JSON
            try:
                parsed = json.loads(raw_output)
                return {
                    "success": True,
                    "raw_output": raw_output,
                    "parsed_output": parsed
                }
            except json.JSONDecodeError:
                return {
                    "success": False,
                    "raw_output": raw_output,
                    "error": "Failed to parse LLM output as JSON"
                }
    
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==================== API Endpoints ====================

@app.post("/documents/ingest")
async def ingest_document(
    file: UploadFile = File(...),
    name: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Ingest a PDF document.
    
    Creates a new document or new version of existing document.
    """
    try:
        # Save uploaded file using a cross-platform temporary path
        temp_dir = tempfile.gettempdir()
        os.makedirs(temp_dir, exist_ok=True)
        suffix = os.path.splitext(file.filename or "uploaded_file")[1] or ".bin"
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=temp_dir, suffix=suffix) as temp_file:
            content = await file.read()
            temp_file.write(content)
            file_path = temp_file.name
        
        # Parse PDF
        parser = DocumentParser(file_path)
        root_nodes, warnings = parser.parse()
        
        # Get or create document
        doc_name = name or file.filename.replace(".pdf", "")
        document = db.query(Document).filter(Document.name == doc_name).first()
        
        if not document:
            document = Document(name=doc_name)
            db.add(document)
            db.flush()
        
        # Create new version
        version_number = len(document.versions) + 1
        version = DocumentVersion(
            document_id=document.id,
            version_number=version_number,
            ingestion_metadata={
                "filename": file.filename,
                "warnings": warnings,
                "extraction_method": "pymupdf"
            }
        )
        db.add(version)
        db.flush()
        
        # Store nodes in DB
        def save_nodes(node_data: NodeData, parent_id: Optional[str] = None, position: int = 0):
            node = Node(
                version_id=version.id,
                parent_id=parent_id,
                heading=node_data.heading,
                level=node_data.level,
                body_text=node_data.body_text,
                content_hash=node_data.compute_hash(),
                hierarchical_path=node_data.to_hierarchical_path(),
                position_in_parent=position,
                is_image_based=node_data.is_image_based,
                ocr_confidence=node_data.ocr_confidence
            )
            db.add(node)
            db.flush()
            
            # Save children
            for i, child in enumerate(node_data.children):
                save_nodes(child, parent_id=node.id, position=i)
        
        for i, root in enumerate(root_nodes):
            save_nodes(root, position=i)
        
        # If not first version, map nodes to previous version
        if version_number > 1:
            prev_version = db.query(DocumentVersion).filter(
                and_(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.version_number == version_number - 1
                )
            ).first()
            
            if prev_version:
                matcher = VersionMatcher(prev_version, version)
                matches = matcher.match_versions()
                NodeVersionTracker.record_mappings(db, prev_version, version, matches)
        
        # Mark as latest
        db.query(DocumentVersion).filter(
            DocumentVersion.document_id == document.id
        ).update({"is_latest": False})
        version.is_latest = True
        
        db.commit()
        
        return {
            "status": "success",
            "document_id": document.id,
            "version": version_number,
            "nodes_created": len(root_nodes),
            "warnings": warnings
        }
    
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=400,
            content={"error": str(e)}
        )


@app.get("/documents/{doc_id}/versions/{version}/sections")
def list_sections(
    doc_id: str,
    version: int = 1,
    db: Session = Depends(get_db)
):
    """List top-level sections of a document"""
    
    doc_version = db.query(DocumentVersion).filter(
        and_(
            DocumentVersion.document_id == doc_id,
            DocumentVersion.version_number == version
        )
    ).first()
    
    if not doc_version:
        raise HTTPException(status_code=404, detail="Version not found")
    
    # Get root nodes only
    root_nodes = db.query(Node).filter(
        and_(
            Node.version_id == doc_version.id,
            Node.parent_id.is_(None)
        )
    ).all()
    
    return {
        "document_id": doc_id,
        "version": version,
        "sections": [node_to_response(node, include_children=False) for node in root_nodes]
    }


@app.get("/documents/{doc_id}/versions/{version}/nodes/{node_id}")
def get_node(
    doc_id: str,
    version: int,
    node_id: str,
    db: Session = Depends(get_db)
):
    """Get specific node with full hierarchy"""
    
    doc_version = db.query(DocumentVersion).filter(
        and_(
            DocumentVersion.document_id == doc_id,
            DocumentVersion.version_number == version
        )
    ).first()
    
    if not doc_version:
        raise HTTPException(status_code=404, detail="Version not found")
    
    node = db.query(Node).filter(
        and_(
            Node.id == node_id,
            Node.version_id == doc_version.id
        )
    ).first()
    
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    
    return node_to_response(node, include_children=True)


@app.get("/documents/{doc_id}/search")
def search_nodes(
    doc_id: str,
    q: str = Query(..., min_length=1),
    version: int = -1,  # -1 = latest
    db: Session = Depends(get_db)
):
    """Full-text search across document sections"""
    
    # Get latest version if not specified
    if version == -1:
        doc_version = db.query(DocumentVersion).filter(
            and_(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.is_latest == True
            )
        ).first()
    else:
        doc_version = db.query(DocumentVersion).filter(
            and_(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.version_number == version
            )
        ).first()
    
    if not doc_version:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Search in headings and body text
    results = db.query(Node).filter(
        and_(
            Node.version_id == doc_version.id,
            or_(
                Node.heading.ilike(f"%{q}%"),
                Node.body_text.ilike(f"%{q}%")
            )
        )
    ).limit(20).all()
    
    return {
        "query": q,
        "version": doc_version.version_number,
        "results": [node_to_response(node) for node in results],
        "count": len(results)
    }


@app.get("/documents/{doc_id}/nodes/{node_id}/changes")
def get_node_changes(
    doc_id: str,
    node_id: str,
    db: Session = Depends(get_db)
):
    """Get what changed for a node between versions"""
    
    # Get node from latest version
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    
    # Get mapping
    mapping = db.query(NodeMapping).filter(
        NodeMapping.v2_node_id == node_id
    ).first()
    
    if not mapping:
        return {
            "node_id": node_id,
            "change_type": "created",
            "diff": None
        }
    
    return {
        "node_id": node_id,
        "change_type": mapping.change_type.value,
        "similarity_score": mapping.similarity_score,
        "diff_summary": mapping.diff_summary,
        "matching_strategy": mapping.matching_strategy
    }


@app.post("/selections")
def create_selection(
    request: SelectionCreateRequest,
    db: Session = Depends(get_db)
):
    """Create a version-pinned selection"""
    
    # Get latest version of document
    latest_version = db.query(DocumentVersion).filter(
        and_(
            DocumentVersion.document_id == request.document_id,
            DocumentVersion.is_latest == True
        )
    ).first()
    
    if not latest_version:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Validate all node IDs exist in this version
    for node_id in request.node_ids:
        node = db.query(Node).filter(
            and_(
                Node.id == node_id,
                Node.version_id == latest_version.id
            )
        ).first()
        if not node:
            raise HTTPException(status_code=400, detail=f"Node {node_id} not found in document")
    
    # Create selection
    selection = Selection(
        document_id=request.document_id,
        version_pinned_to=latest_version.id,
        name=request.name,
        description=request.description
    )
    db.add(selection)
    db.flush()
    
    # Add nodes to selection
    for i, node_id in enumerate(request.node_ids):
        sel_node = SelectionNode(
            selection_id=selection.id,
            node_id=node_id,
            position_in_selection=i
        )
        db.add(sel_node)
    
    db.commit()
    
    return {
        "selection_id": selection.id,
        "name": selection.name,
        "node_count": len(request.node_ids),
        "version_pinned_to": latest_version.version_number,
        "created_at": selection.created_at.isoformat()
    }


@app.post("/selections/{selection_id}/generate")
async def generate_test_cases(
    selection_id: str,
    request: GenerateTestCasesRequest,
    db: Session = Depends(get_db)
):
    """Generate test cases from a selection using LLM"""
    
    selection = db.query(Selection).filter(Selection.id == selection_id).first()
    if not selection:
        raise HTTPException(status_code=404, detail="Selection not found")
    
    # Check if already generated (idempotency)
    existing_generation = db.query(Generation).filter(
        Generation.selection_id == selection_id
    ).first()
    
    if existing_generation:
        # Return cached result
        return {
            "generation_id": existing_generation.id,
            "cached": True,
            "test_cases": [
                TestCaseResponse.from_orm(tc) for tc in existing_generation.test_cases
            ]
        }
    
    # Reconstruct text from selection
    text = reconstruct_selection_text(db, selection)
    
    # System prompt for LLM
    system_prompt = """You are an expert QA engineer for medical devices.
Your task is to generate comprehensive test cases from technical documentation.

Each test case MUST include:
1. Test name (what is being tested)
2. Preconditions (required system state before test)
3. Steps (numbered list of exact actions to perform)
4. Expected result (what should happen if the test passes)
5. Priority (critical/high/medium/low based on safety impact)

Return output as valid JSON in exactly this format:
{
  "test_cases": [
    {
      "name": "test name",
      "preconditions": "initial state",
      "steps": ["step 1", "step 2", "step 3"],
      "expected_result": "expected behavior",
      "priority": "critical"
    }
  ]
}

Return 3-5 test cases maximum. Focus on functional requirements, safety limits, and edge cases."""
    
    user_prompt = "Generate test cases from the following manual excerpt:"
    
    # Call LLM
    llm_response = await call_llm_for_test_cases(text, request.llm_provider, system_prompt, user_prompt)
    
    if not llm_response.get("success"):
        return JSONResponse(
            status_code=400,
            content={"error": llm_response.get("error")}
        )
    
    # Store generation
    generation = Generation(
        selection_id=selection_id,
        document_version=selection.version_pinned_to,
        llm_provider=LLMProvider(request.llm_provider),
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        raw_llm_output=llm_response.get("raw_output", ""),
        parsed_test_cases=llm_response.get("parsed_output", {}),
        input_text_hash=hashlib.sha256(text.encode()).hexdigest(),
        error_message=llm_response.get("error")
    )
    db.add(generation)
    db.flush()
    
    # Store individual test cases
    test_cases_list = llm_response.get("parsed_output", {}).get("test_cases", [])
    for tc_data in test_cases_list:
        tc = TestCase(
            generation_id=generation.id,
            test_name=tc_data.get("name", "Unnamed"),
            preconditions=tc_data.get("preconditions", ""),
            steps=tc_data.get("steps", []),
            expected_result=tc_data.get("expected_result", ""),
            priority=Priority(tc_data.get("priority", "medium").lower()),
            staleness_status=StalenessLevel.FRESH,
            staleness_confidence=1.0
        )
        db.add(tc)
    
    db.commit()
    
    return {
        "generation_id": generation.id,
        "selection_id": selection_id,
        "test_cases_created": len(test_cases_list),
        "generated_at": generation.generated_at.isoformat()
    }


@app.get("/selections/{selection_id}/generations")
def list_generations(
    selection_id: str,
    db: Session = Depends(get_db)
):
    """List all test case generations for a selection"""
    
    selection = db.query(Selection).filter(Selection.id == selection_id).first()
    if not selection:
        raise HTTPException(status_code=404, detail="Selection not found")
    
    generations = db.query(Generation).filter(
        Generation.selection_id == selection_id
    ).all()
    
    return {
        "selection_id": selection_id,
        "generations": [
            GenerationResponse.from_orm(gen) for gen in generations
        ]
    }


@app.get("/generations/{generation_id}/staleness-report")
def get_staleness_report(
    generation_id: str,
    db: Session = Depends(get_db)
):
    """Get detailed staleness analysis for a generation"""
    
    generation = db.query(Generation).filter(Generation.id == generation_id).first()
    if not generation:
        raise HTTPException(status_code=404, detail="Generation not found")
    
    # Get latest version
    latest_version = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == generation.selection.document_id
    ).order_by(DocumentVersion.version_number.desc()).first()
    
    if not latest_version or latest_version.id == generation.document_version:
        # No newer version exists
        return {
            "generation_id": generation_id,
            "status": "current",
            "message": "No newer document version exists"
        }
    
    # Check staleness for each test case
    test_case_staleness = []
    overall_staleness = StalenessLevel.FRESH
    
    for tc in generation.test_cases:
        # Get selected nodes and compare with latest version
        check = StalenessCheck(
            generation_id=generation_id,
            test_case_id=tc.id,
            checked_against_version=latest_version.id,
            staleness_level=StalenessLevel.POSSIBLY_STALE,
            confidence_score=0.5,
            detection_method="hash"
        )
        db.add(check)
        
        test_case_staleness.append({
            "test_case_id": tc.id,
            "test_name": tc.test_name,
            "staleness": StalenessLevel.POSSIBLY_STALE.value,
            "confidence": 0.5
        })
        
        overall_staleness = StalenessLevel.POSSIBLY_STALE
    
    db.commit()
    
    return {
        "generation_id": generation_id,
        "generated_from_version": db.query(DocumentVersion).filter(
            DocumentVersion.id == generation.document_version
        ).first().version_number,
        "latest_document_version": latest_version.version_number,
        "overall_staleness": overall_staleness.value,
        "test_cases": test_case_staleness
    }


@app.get("/")
def root():
    """Root endpoint for quick service verification"""
    return {
        "status": "ok",
        "message": "Document Test Case Generator API is running. Visit /docs for API documentation."
    }


@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
