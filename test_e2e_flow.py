"""
End-to-end integration test demonstrating full workflow:
1. Ingest document v1
2. Browse document structure
3. Create selection
4. Generate test cases (requires LLM API key)
5. Ingest document v2
6. Check staleness

Run with:
    python test_e2e_flow.py

Note: Requires GROQ_API_KEY or GEMINI_API_KEY in environment
"""

import requests
import json
import time
from pathlib import Path
import os

# Configuration
BASE_URL = "http://localhost:8000"
TIMEOUT = 30

# Colors for output
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
RESET = '\033[0m'
BOLD = '\033[1m'

def print_step(step_num, title):
    print(f"\n{BOLD}{GREEN}STEP {step_num}: {title}{RESET}")
    print("-" * 60)

def print_success(msg):
    print(f"{GREEN}✓{RESET} {msg}")

def print_warning(msg):
    print(f"{YELLOW}⚠{RESET} {msg}")

def print_error(msg):
    print(f"{RED}✗{RESET} {msg}")

def print_info(msg):
    print(f"  {msg}")

def test_health():
    """Test that API is running"""
    print_step(0, "Verify API is Running")
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
        if resp.status_code == 200:
            print_success("API is healthy")
            return True
        else:
            print_error(f"API returned {resp.status_code}")
            return False
    except Exception as e:
        print_error(f"Cannot connect to API at {BASE_URL}: {e}")
        print_warning("Make sure to run: python -m uvicorn main:app --reload")
        return False

def test_ingest_v1():
    """Step 1: Ingest document v1"""
    print_step(1, "Ingest CT-200 Manual v1")
    
    # Check if sample PDF exists
    sample_pdf = Path("ct200_manual_v1.pdf")
    if not sample_pdf.exists():
        print_warning(f"Sample PDF not found: {sample_pdf}")
        print_info("Using minimal test PDF instead...")
        # Create minimal test PDF for demo
        from models import Document
        return create_test_document_v1()
    
    # Upload PDF
    try:
        with open(sample_pdf, 'rb') as f:
            files = {'file': f}
            data = {'name': 'CT-200 Manual'}
            resp = requests.post(
                f"{BASE_URL}/documents/ingest",
                files=files,
                data=data,
                timeout=TIMEOUT
            )
        
        if resp.status_code != 200:
            print_error(f"Ingest failed: {resp.status_code}")
            print_info(resp.text)
            return None
        
        result = resp.json()
        print_success(f"Document ingested successfully")
        print_info(f"Document ID: {result['document_id']}")
        print_info(f"Version: {result['version']}")
        print_info(f"Nodes created: {result['nodes_created']}")
        
        if result.get('warnings'):
            for warning in result['warnings']:
                print_warning(f"Parsing warning: {warning}")
        
        return result['document_id']
    
    except Exception as e:
        print_error(f"Failed to ingest: {e}")
        return None

def test_list_sections(doc_id):
    """Step 2: Browse document structure"""
    print_step(2, "Browse Document Structure (v1)")
    
    try:
        resp = requests.get(
            f"{BASE_URL}/documents/{doc_id}/versions/1/sections",
            timeout=TIMEOUT
        )
        
        if resp.status_code != 200:
            print_error(f"List sections failed: {resp.status_code}")
            return []
        
        result = resp.json()
        sections = result.get('sections', [])
        
        print_success(f"Found {len(sections)} top-level sections")
        
        for i, section in enumerate(sections[:5], 1):
            print_info(f"{i}. {section['heading']} (ID: {section['id'][:8]}...)")
        
        if len(sections) > 5:
            print_info(f"... and {len(sections) - 5} more")
        
        return sections
    
    except Exception as e:
        print_error(f"Failed to list sections: {e}")
        return []

def test_search(doc_id):
    """Step 3: Search document"""
    print_step(3, "Search Document")
    
    search_terms = ['safety', 'pressure', 'warning', 'procedure']
    
    for term in search_terms:
        try:
            resp = requests.get(
                f"{BASE_URL}/documents/{doc_id}/search",
                params={'q': term, 'version': 1},
                timeout=TIMEOUT
            )
            
            if resp.status_code == 200:
                result = resp.json()
                count = result.get('count', 0)
                if count > 0:
                    print_success(f"Search '{term}': found {count} matches")
                else:
                    print_warning(f"Search '{term}': no matches")
            else:
                print_warning(f"Search '{term}' failed: {resp.status_code}")
        
        except Exception as e:
            print_warning(f"Search '{term}' error: {e}")

def test_create_selection(doc_id, node_ids):
    """Step 4: Create version-pinned selection"""
    print_step(4, "Create Version-Pinned Selection")
    
    if not node_ids:
        print_warning("No nodes to select")
        return None
    
    # Select first 2-3 nodes
    selected_nodes = node_ids[:min(3, len(node_ids))]
    
    try:
        payload = {
            'document_id': doc_id,
            'name': 'Safety Critical Sections',
            'description': 'E2E test selection of safety-related sections',
            'node_ids': selected_nodes
        }
        
        resp = requests.post(
            f"{BASE_URL}/selections",
            json=payload,
            timeout=TIMEOUT
        )
        
        if resp.status_code != 200:
            print_error(f"Selection creation failed: {resp.status_code}")
            print_info(resp.text)
            return None
        
        result = resp.json()
        selection_id = result['selection_id']
        
        print_success(f"Selection created")
        print_info(f"Selection ID: {selection_id[:8]}...")
        print_info(f"Nodes selected: {len(selected_nodes)}")
        print_info(f"Version pinned to: {result['version_pinned_to']}")
        
        return selection_id
    
    except Exception as e:
        print_error(f"Failed to create selection: {e}")
        return None

def test_generate_test_cases(selection_id):
    """Step 5: Generate test cases using LLM"""
    print_step(5, "Generate Test Cases with LLM")
    
    # Check if LLM API keys are available
    has_groq = os.getenv('GROQ_API_KEY')
    has_gemini = os.getenv('GEMINI_API_KEY')
    
    if not (has_groq or has_gemini):
        print_warning("No LLM API keys found in environment")
        print_info("Skipping test case generation")
        print_info("To enable: set GROQ_API_KEY or GEMINI_API_KEY")
        return None
    
    provider = 'groq' if has_groq else 'gemini'
    
    try:
        payload = {
            'selection_id': selection_id,
            'llm_provider': provider
        }
        
        print_info(f"Calling {provider.upper()} API...")
        
        resp = requests.post(
            f"{BASE_URL}/selections/{selection_id}/generate",
            json=payload,
            timeout=60  # LLM calls might take longer
        )
        
        if resp.status_code not in [200, 503]:
            print_error(f"Generation failed: {resp.status_code}")
            print_info(resp.text)
            return None
        
        result = resp.json()
        
        if 'error' in result:
            print_warning(f"LLM error: {result['error']}")
            return None
        
        generation_id = result.get('generation_id')
        test_count = result.get('test_cases_created', 0)
        
        print_success(f"Test cases generated")
        print_info(f"Generation ID: {generation_id[:8]}..." if generation_id else "N/A")
        print_info(f"Test cases created: {test_count}")
        
        return generation_id
    
    except requests.Timeout:
        print_warning("LLM call timed out (API might be slow)")
        return None
    except Exception as e:
        print_error(f"Failed to generate test cases: {e}")
        return None

def test_ingest_v2(doc_id):
    """Step 6: Ingest updated document v2"""
    print_step(6, "Ingest CT-200 Manual v2 (with changes)")
    
    sample_pdf = Path("ct200_manual_v2.pdf")
    if not sample_pdf.exists():
        print_warning(f"Sample PDF v2 not found: {sample_pdf}")
        print_info("For demo purposes, skipping v2 ingestion")
        print_info("In production, you would upload an updated PDF")
        return False
    
    try:
        with open(sample_pdf, 'rb') as f:
            files = {'file': f}
            data = {'name': 'CT-200 Manual'}
            resp = requests.post(
                f"{BASE_URL}/documents/ingest",
                files=files,
                data=data,
                timeout=TIMEOUT
            )
        
        if resp.status_code != 200:
            print_error(f"Ingest failed: {resp.status_code}")
            return False
        
        result = resp.json()
        print_success(f"Document v2 ingested successfully")
        print_info(f"Version: {result['version']}")
        print_info(f"Nodes created: {result['nodes_created']}")
        
        return True
    
    except Exception as e:
        print_error(f"Failed to ingest v2: {e}")
        return False

def test_staleness_check(generation_id):
    """Step 7: Check staleness after v2 ingestion"""
    print_step(7, "Check Staleness Report")
    
    if not generation_id:
        print_warning("No generation ID (test cases not generated)")
        return
    
    try:
        resp = requests.get(
            f"{BASE_URL}/generations/{generation_id}/staleness-report",
            timeout=TIMEOUT
        )
        
        if resp.status_code == 404:
            print_warning("Generation not found (might not have been saved)")
            return
        
        if resp.status_code != 200:
            print_warning(f"Staleness check failed: {resp.status_code}")
            return
        
        result = resp.json()
        
        status = result.get('status', 'unknown')
        staleness = result.get('overall_staleness', 'unknown')
        
        print_success(f"Staleness check completed")
        print_info(f"Status: {status}")
        print_info(f"Overall staleness: {staleness}")
        
        test_cases = result.get('test_cases', [])
        if test_cases:
            print_info(f"Individual test cases: {len(test_cases)} checked")
            for tc in test_cases[:3]:
                staleness_level = tc.get('staleness', 'unknown')
                confidence = tc.get('confidence', 0)
                print_info(f"  - {tc['test_name']}: {staleness_level} ({confidence:.1%} confidence)")
        
        return result
    
    except Exception as e:
        print_error(f"Failed to check staleness: {e}")
        return None

def create_test_document_v1():
    """
    Create a test document directly in database instead of parsing PDF.
    Used when sample PDFs are not available.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Base, Document, DocumentVersion, Node
    import hashlib
    
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./tri9t.db")
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    # Create document
    doc = Document(name="CT-200 Manual")
    db.add(doc)
    db.flush()
    
    # Create version
    version = DocumentVersion(
        document_id=doc.id,
        version_number=1,
        is_latest=True
    )
    db.add(version)
    db.flush()
    
    # Create sample nodes
    nodes_data = [
        ("Introduction", 1, "Welcome to CT-200 manual"),
        ("Safety", 1, "Safety guidelines"),
        ("Warnings", 2, "Important warnings about device usage"),
        ("Pressure Limits", 3, "Pressure must not exceed 180 mmHg"),
        ("Operating Procedures", 1, "How to use the device"),
    ]
    
    root_nodes = []
    for i, (heading, level, body) in enumerate(nodes_data):
        if level == 1:
            node = Node(
                version_id=version.id,
                heading=heading,
                level=level,
                body_text=body,
                content_hash=hashlib.sha256(body.encode()).hexdigest(),
                hierarchical_path=f"/{heading}",
                position_in_parent=i
            )
            db.add(node)
            db.flush()
            root_nodes.append(node)
    
    db.commit()
    db.close()
    
    return doc.id

def main():
    """Run full end-to-end workflow"""
    print(f"\n{BOLD}=== Tri9T AI - End-to-End Integration Test ==={RESET}")
    print(f"API URL: {BASE_URL}")
    
    # Step 0: Health check
    if not test_health():
        return False
    
    # Step 1: Ingest v1
    doc_id = test_ingest_v1()
    if not doc_id:
        print_error("Could not ingest document - stopping")
        return False
    
    time.sleep(1)  # Brief pause
    
    # Step 2: List sections
    sections = test_list_sections(doc_id)
    node_ids = [s['id'] for s in sections] if sections else []
    
    if not node_ids:
        print_error("No nodes found - stopping")
        return False
    
    # Step 3: Search
    test_search(doc_id)
    
    # Step 4: Create selection
    selection_id = test_create_selection(doc_id, node_ids)
    if not selection_id:
        print_error("Could not create selection - stopping")
        return False
    
    # Step 5: Generate test cases (optional, requires LLM)
    generation_id = test_generate_test_cases(selection_id)
    
    # Step 6: Ingest v2 (optional, requires sample PDF)
    test_ingest_v2(doc_id)
    
    # Step 7: Check staleness (optional, requires v2)
    if generation_id:
        test_staleness_check(generation_id)
    
    # Summary
    print(f"\n{BOLD}{GREEN}=== Test Complete ==={RESET}")
    print(f"Document ID: {doc_id}")
    print(f"Selection ID: {selection_id}")
    if generation_id:
        print(f"Generation ID: {generation_id}")
    print(f"\nTo continue exploring:")
    print(f"  - Import tri9t_api.postman_collection.json into Postman")
    print(f"  - Or use curl commands with the IDs above")
    
    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
