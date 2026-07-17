# Tri9T AI Engineering - Document Test Case Generation System

A versioned document management system with intelligent QA test case generation for medical device manuals.

## Features

✅ **PDF Parsing**: Converts unstructured PDFs into hierarchical document structure  
✅ **Document Versioning**: Tracks changes between versions (v1 → v2 → v3...)  
✅ **Intelligent Search**: Full-text search across document sections  
✅ **Version-Pinned Selections**: Lock sections at specific versions to prevent breaking changes  
✅ **LLM Test Case Generation**: Auto-generate QA test cases from manual sections  
✅ **Staleness Detection**: Detect when test cases no longer match updated specs  
✅ **Change Tracking**: See exactly what changed between document versions  

## Architecture

```
FastAPI Backend
  ├── PDF Parsing (PyMuPDF + multi-strategy heading detection)
  ├── Hierarchical Structure Building
  ├── Version Matching (path-based with fuzzy fallback)
  ├── LLM Integration (Groq/Gemini with structured output)
  └── Staleness Detection (content hash + fuzzy matching)

Database (SQLite + SQLAlchemy)
  ├── documents, document_versions, nodes
  ├── node_mappings (track v1→v2 changes)
  ├── selections, selection_nodes
  ├── generations, test_cases
  └── staleness_checks
```

## Quick Start

### 1. Create a Python 3.13 virtual environment

```bash
python3.13 -m venv .venv
```

### 2. Activate the virtual environment

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

On Windows CMD:

```cmd
.\.venv\Scripts\activate.bat
```

On macOS/Linux:

```bash
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Set Environment Variables

On Windows PowerShell:

```powershell
$env:DATABASE_URL = "sqlite:///./tri9t.db"
$env:GROQ_API_KEY = "your_groq_api_key"
$env:GEMINI_API_KEY = "your_gemini_api_key"
```

On macOS/Linux:

```bash
export DATABASE_URL="sqlite:///./tri9t.db"
export GROQ_API_KEY="your_groq_api_key"
export GEMINI_API_KEY="your_gemini_api_key"
```

Get free API keys:
- **Groq**: https://console.groq.com/keys
- **Gemini**: https://ai.google.dev/

### 5. Run the Server

```bash
python -m uvicorn main:app --reload --port 8000
```

# Server runs at http://localhost:8000
# Swagger API docs at http://localhost:8000/docs

### 6. Verify Installation

```bash
curl http://127.0.0.1:8000/health
# Expected: {"status": "ok"}
```

## Troubleshooting

- If `python3.13` is not found, use the Windows launcher:
  ```bash
  py -3.13 -m venv .venv
  ```
- If `pip install` fails due to missing wheel support, ensure the virtual environment is activated and use:
  ```bash
  python -m pip install -r requirements.txt
  ```
- If `uvicorn` is not found after install, run it via Python:
  ```bash
  python -m uvicorn main:app --reload --port 8000
  ```
- If the app is not reachable on `localhost:8000`, check the terminal output for the actual bind address and port.

## API Endpoints

### Document Management

#### Ingest PDF
```http
POST /documents/ingest

Body: multipart form-data
- file: CT-200.pdf
- name: "CT-200 Manual" (optional)

Response:
{
  "status": "success",
  "document_id": "uuid-...",
  "version": 1,
  "nodes_created": 42,
  "warnings": [...]
}
```

#### List Top-Level Sections
```http
GET /documents/{doc_id}/versions/{version}/sections

Response:
{
  "document_id": "uuid-...",
  "version": 1,
  "sections": [
    {
      "id": "node-...",
      "heading": "Introduction",
      "level": 1,
      "children": [...]
    }
  ]
}
```

#### Get Specific Node
```http
GET /documents/{doc_id}/versions/{version}/nodes/{node_id}

Response:
{
  "id": "node-...",
  "heading": "Safety Warnings",
  "level": 2,
  "body_text": "...",
  "content_hash": "abc123...",
  "children": [...],
  "created_at": "2024-01-15T10:00:00Z"
}
```

#### Search Document
```http
GET /documents/{doc_id}/search?q=pressure&version=1

Response:
{
  "query": "pressure",
  "version": 1,
  "results": [...],
  "count": 5
}
```

#### Get Node Changes
```http
GET /documents/{doc_id}/nodes/{node_id}/changes

Response:
{
  "node_id": "node-...",
  "change_type": "modified",
  "similarity_score": 0.92,
  "diff_summary": "Content expanded (15 → 18 lines)",
  "matching_strategy": "exact_path"
}
```

### Test Case Generation

#### Create Selection
```http
POST /selections

Body:
{
  "document_id": "uuid-...",
  "name": "Safety Critical Sections",
  "description": "All safety-related sections",
  "node_ids": ["node-id-1", "node-id-2", "node-id-3"]
}

Response:
{
  "selection_id": "sel-...",
  "name": "Safety Critical Sections",
  "node_count": 3,
  "version_pinned_to": 1,
  "created_at": "2024-01-15T10:00:00Z"
}
```

#### Generate Test Cases
```http
POST /selections/{selection_id}/generate

Body:
{
  "selection_id": "sel-...",
  "llm_provider": "groq"  # or "gemini"
}

Response:
{
  "generation_id": "gen-...",
  "selection_id": "sel-...",
  "test_cases_created": 4,
  "generated_at": "2024-01-15T10:00:00Z"
}
```

#### List All Generations
```http
GET /selections/{selection_id}/generations

Response:
{
  "selection_id": "sel-...",
  "generations": [
    {
      "id": "gen-...",
      "generated_at": "2024-01-15T10:00:00Z",
      "test_cases": [
        {
          "id": "tc-...",
          "test_name": "Pressure Limit Validation",
          "preconditions": "Device powered on",
          "steps": ["Step 1", "Step 2"],
          "expected_result": "Device displays E3 error",
          "priority": "critical",
          "staleness_status": "fresh"
        }
      ]
    }
  ]
}
```

#### Check Staleness
```http
GET /generations/{generation_id}/staleness-report

Response:
{
  "generation_id": "gen-...",
  "generated_from_version": 1,
  "latest_document_version": 2,
  "overall_staleness": "possibly_stale",
  "test_cases": [
    {
      "test_case_id": "tc-...",
      "test_name": "Pressure Limit",
      "staleness": "definitely_stale",
      "confidence": 1.0
    }
  ]
}
```

## End-to-End Workflow Example

### Step 1: Ingest Version 1

```bash
curl -X POST "http://localhost:8000/documents/ingest" \
  -F "file=@ct200_manual_v1.pdf" \
  -F "name=CT-200 Manual"
```

Response: `{"document_id": "doc-123", "version": 1, "nodes_created": 50}`

### Step 2: Browse Document

```bash
# List top sections
curl "http://localhost:8000/documents/doc-123/versions/1/sections"

# Search for specific content
curl "http://localhost:8000/documents/doc-123/search?q=pressure"

# Get full section details
curl "http://localhost:8000/documents/doc-123/versions/1/nodes/node-456"
```

### Step 3: Create Selection

```bash
curl -X POST "http://localhost:8000/selections" \
  -H "Content-Type: application/json" \
  -d '{
    "document_id": "doc-123",
    "name": "Safety Checks",
    "node_ids": ["node-1", "node-2", "node-3"]
  }'
```

Response: `{"selection_id": "sel-789", "version_pinned_to": 1, ...}`

### Step 4: Generate Test Cases

```bash
curl -X POST "http://localhost:8000/selections/sel-789/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "selection_id": "sel-789",
    "llm_provider": "groq"
  }'
```

Response: `{"generation_id": "gen-456", "test_cases_created": 4, ...}`

### Step 5: Ingest Version 2

```bash
curl -X POST "http://localhost:8000/documents/ingest" \
  -F "file=@ct200_manual_v2.pdf" \
  -F "name=CT-200 Manual"

# Returns: {"document_id": "doc-123", "version": 2, ...}
```

### Step 6: Check Staleness

```bash
curl "http://localhost:8000/generations/gen-456/staleness-report"
```

Response shows which test cases are stale due to document changes

## Testing

### Run Unit Tests

```bash
# Parser tests (edge cases)
python parser.py

# Versioning tests
python versioning.py

# Both should show:
# ✅ test_name PASSED
# ✅ All tests passed!
```

### Run Integration Test

```bash
python test_e2e_flow.py

# Full end-to-end workflow:
# 1. Ingest v1
# 2. Create selection
# 3. Generate test cases
# 4. Ingest v2
# 5. Check staleness
```

### Postman Collection

Import `tri9t_api.postman_collection.json` into Postman for interactive testing:
- All endpoints with example requests
- Full workflow demonstrating v1→v2 + staleness

## Project Structure

```
tri9t_ai_assignment/
├── main.py                    # FastAPI application + all endpoints
├── models.py                  # SQLAlchemy ORM models
├── parser.py                  # PDF extraction + hierarchy building
├── versioning.py              # Document versioning + change detection
├── requirements.txt           # Python dependencies
├── .env.example              # Environment variables template
├── README.md                 # This file
├── APPROACH_DOCUMENT.md      # Design decisions + rationale
├── ASSIGNMENT_BREAKDOWN.md   # Detailed explanation of what to build
├── tri9t_api.postman_collection.json  # API testing collection
├── test_e2e_flow.py         # End-to-end integration test
├── test_ct200_v1.pdf        # Sample CT-200 manual (placeholder)
└── test_ct200_v2.pdf        # Sample CT-200 v2 (placeholder)
```

## Database Schema

### Core Tables

```sql
-- Root document
documents (id, name, description, created_at, updated_at)

-- Versions of document
document_versions (id, document_id, version_number, ingested_at, is_latest)

-- Individual sections/nodes
nodes (
  id, version_id, parent_id, heading, level, body_text, 
  content_hash, hierarchical_path, is_image_based, ocr_confidence
)

-- Track changes between versions
node_mappings (
  id, v1_node_id, v2_node_id, change_type, 
  similarity_score, matching_strategy, diff_summary
)

-- User-selected sections (version-pinned)
selections (id, document_id, version_pinned_to, name)
selection_nodes (id, selection_id, node_id, position_in_selection)

-- LLM-generated test cases
generations (
  id, selection_id, document_version, generated_at, 
  llm_provider, system_prompt, user_prompt, 
  raw_llm_output, parsed_test_cases
)

test_cases (
  id, generation_id, test_name, preconditions, 
  steps, expected_result, priority, staleness_status
)

-- Staleness tracking
staleness_checks (
  id, generation_id, test_case_id, checked_against_version,
  staleness_level, confidence_score, detection_method,
  original_text, current_text, checked_at
)
```

## Configuration

### Environment Variables

```bash
# Database
DATABASE_URL=sqlite:///./tri9t.db

# LLM Providers (optional)
GROQ_API_KEY=gsk_...
GEMINI_API_KEY=AIzaSyD...

# Optional: Logging
LOG_LEVEL=INFO

# Optional: CORS (if adding frontend)
CORS_ORIGINS=["http://localhost:3000"]
```

## Known Limitations

### 1. Staleness Detection
- Uses exact content hash (can't distinguish typo fixes from material changes)
- Rephrased text with same meaning = false positive (marked stale)
- ✅ Mitigated by: Content comparison showing original vs current text

### 2. Version Matching
- Uses hierarchical path (can't handle large reorganizations)
- Section rename + move = treated as deleted + created
- ✅ Mitigated by: Clear change_type and diff_summary in response

### 3. LLM Output
- Sometimes returns non-JSON or missing fields
- No auto-fix for stale test cases
- ✅ Mitigated by: Error messages, partial parsing, idempotency cache

### 4. PDF Extraction
- Image-heavy PDFs = low OCR accuracy
- Scanned PDFs = slower, less accurate
- ✅ Mitigated by: Confidence scores, warnings in response

## Improvements for Production

- [ ] Switch from SQLite to PostgreSQL
- [ ] Add Redis caching for LLM generations
- [ ] Implement JWT authentication
- [ ] Add rate limiting per user
- [ ] Implement semantic staleness detection
- [ ] Add manual node mapping admin API
- [ ] Support batch ingestion
- [ ] Add metrics/monitoring
- [ ] Implement test case templating
- [ ] Add UI for browsing + managing test cases

## Support & Debugging

### Common Issues

**Q: "PDF appears blank or has no text"**  
A: Check if PDF is image-based (scanned). View `ingestion_metadata.warnings` for OCR issues.

**Q: "Staleness report shows everything stale"**  
A: This is likely correct if document text changed. Use original_text/current_text comparison to verify.

**Q: "LLM API returns 429 (rate limited)"**  
A: Free tier has rate limits. Use free-tier Groq (~30 req/min) or wait between requests.

**Q: "Selection has wrong nodes"**  
A: Selections are version-pinned to v1. To update, create new selection with v2 nodes.

## License

This assignment is provided as-is for evaluation purposes.

## Contact

For questions about the implementation, see the Approach Document for detailed design rationale.

---


**Status**: Production-ready for assignment evaluation
