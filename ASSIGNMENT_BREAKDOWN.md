# Tri9T AI - Complete Assignment Breakdown & Solution Guide

## 🎯 WHAT ARE WE BUILDING?

A **versioned document management system with intelligent QA test case generation** for a medical device manual. Think of it as "GitHub for technical documents + AI-powered test case generation + change tracking."

### The Core Problem
- Medical device manuals change over time
- QA test cases reference specific sections
- When text changes, we need to know: "Are my test cases still valid?"
- We need to generate test cases from manual sections automatically

---

## 📋 DETAILED BREAKDOWN OF EACH COMPONENT

### **1. OCR-Based Document Extraction (The Parser)**

#### WHAT: Parse CT-200 manual PDF into a hierarchical tree
#### WHY: 
- PDFs are unstructured; we need machine-readable hierarchy
- Medical devices have strict regulatory requirements
- We need to preserve exact parent-child relationships for compliance

#### HOW:
```
PDF File (flat text + images)
    ↓
OCR Engine (PyMuPDF or Tesseract)
    ↓
Text + Layout Analysis
    ↓
Pattern Recognition (detect headings by font size, indentation)
    ↓
Hierarchy Reconstruction (build tree from patterns)
    ↓
Validation (check for duplicate nodes, broken relationships)
    ↓
Database (store with parent_id, level, content_hash)
```

#### Key Challenges & Solutions:
1. **Duplicate Headings**: "Overview" appears in multiple sections
   - Solution: Use UUID + (section_path) for unique identification
   
2. **Inconsistent Formatting**: Different heading styles in different sections
   - Solution: Multi-pass parsing (font size, then indentation, then heuristics)
   
3. **Tables & Lists**: Can't just treat as plain text
   - Solution: Detect table boundaries, preserve structure
   
4. **Figures & Captions**: Need to know which figure belongs to which section
   - Solution: Spatial proximity (figure below heading = belongs to that section)

#### Test Cases (Must write 3+ to prove it works):
```python
test_duplicate_heading_creates_different_nodes()
    # Input: Two "Overview" sections at different levels
    # Output: Two different node IDs, correct parents
    
test_inconsistent_font_handling()
    # Input: Heading in bold vs. italic in same doc
    # Output: Both recognized as headings, correct hierarchy
    
test_nested_list_preservation()
    # Input: Bulleted list with sub-bullets
    # Output: Parent-child list relationships preserved
```

---

### **2. Document Versioning**

#### WHAT: Handle v1 and v2 without duplicating or losing data
#### WHY:
- Document gets updated → we re-ingest v2
- Test cases from v1 still need to reference original text
- Users need to see "what changed"

#### HOW:
**Matching Strategy: Hierarchical Path + Content Hash**
```
v1 Node: /Safety/Warnings/Pressure_Limits
Hash: abc123def456

v2 Node: /Safety/Warnings/Pressure_Limits  
Hash: abc123xyz789  (text changed!)

Decision: SAME NODE (path matches) but MODIFIED (hash differs)
```

#### Why this approach?
- **Path-based**: Structural changes (moved section) = new node
- **Hash-based**: Text changes = flag as modified
- **Fuzzy matching breaks**: Can't tell if "move" vs "delete+recreate"

#### Known Failure Modes:
- If section gets renamed AND reorganized = might miss it
- Mitigation: Manual review flag for large path changes

#### Database Structure:
```python
# nodes table
id (UUID)
document_version_id (links to version)
parent_id (UUID of parent)
heading
level
body_text
content_hash

# versions table
id
document_id
version_number (1, 2, 3...)
ingested_at
is_latest

# node_mapping table (tracks v1→v2 relationships)
v1_node_id
v2_node_id
change_type ('unchanged', 'modified', 'deleted', 'created')
diff_summary
```

---

### **3. Browse API**

#### Endpoints:
```
GET /documents/{doc_id}/versions/{version}/sections
→ List top-level sections

GET /documents/{doc_id}/versions/{version}/nodes/{node_id}
→ Get single node with:
  - heading, level, body_text
  - children (with IDs)
  - content_hash
  - created_at, last_modified_at

GET /documents/{doc_id}/search?q=pressure&version=latest
→ Full-text search across all node bodies

GET /documents/{doc_id}/nodes/{node_id}/changes
→ Show what changed between versions
  - version 1: "Pressure limit: 180 mmHg"
  - version 2: "Pressure limit: 190 mmHg"
  - flag: "MODIFIED"
```

#### Why this structure?
- Explicit version parameter prevents accidental use of stale data
- Content hash enables staleness detection later
- Search must work across hierarchy efficiently

---

### **4. Selection API**

#### WHAT: Users mark "I want to generate test cases for these sections"
#### WHY:
- Selections are persistent across versions
- Test cases need to know "I was created from node ABC version 1"
- If node ABC changes → we detect it

#### HOW:
```python
POST /selections
{
  "name": "Critical Safety Checks",
  "node_ids": ["uuid-1", "uuid-2", "uuid-3"],
  "document_version": 1,  # Version-pinned!
  "created_at": "2024-01-15T10:00:00Z"
}

Returns: selection_id = "sel-abc123"

# This selection is FROZEN to v1
# If document gets re-ingested as v2, selection still remembers v1 text
```

#### Key Design Decision:
- **Version-pinned selections** mean old selections don't break when doc changes
- Trade-off: Can't auto-update; user must manually refresh if desired

#### Database:
```python
# selections table
id (UUID)
document_id
name
created_at
version_pinned_to (which version this selection is locked to)

# selection_nodes table (junction)
selection_id
node_id
node_version_id
position_in_selection (order matters)
```

---

### **5. LLM-Powered Generation API**

#### WHAT: "Generate test cases from these sections"
#### WHY:
- QA engineers spend hours manually writing test cases
- LLMs can suggest starting points
- Still requires human review (not auto-executing)

#### HOW:
```
User submits selection → Reconstruct original text → 
Send to LLM with prompt → Parse structured output → 
Store linked to nodes → Return to user
```

#### Prompt Design (CRITICAL):
```python
SYSTEM_PROMPT = """
You are a QA engineer for medical devices.
Generate 3-5 test cases from the following manual excerpt.

Each test case MUST include:
1. Test name (what is being tested)
2. Preconditions (system state before test)
3. Steps (numbered list of actions)
4. Expected result (what should happen)
5. Priority (critical/high/medium)

Format output as JSON:
{
  "test_cases": [
    {
      "name": "...",
      "preconditions": "...",
      "steps": [...],
      "expected_result": "...",
      "priority": "..."
    }
  ]
}
"""
```

#### Error Handling Strategy:
```python
# What can go wrong?
1. LLM returns non-JSON → Retry with stricter prompt
2. Missing fields → Validation fails → Log error + return partial
3. Hallucinated requirements → Can't prevent, but mark for review
4. Rate limited → Queue job, exponential backoff
5. Same selection twice → Check cache first (idempotency)

# Duplicate Submission Policy:
- First generation: Create and store
- Second generation (same selection): Return cached result
- Rationale: Reproducible, saves API calls, deterministic UX
```

#### Database:
```python
# generations table
id (UUID)
selection_id
document_version  # Version this was generated from
generated_at
llm_provider (groq, gemini, etc)
prompt_used
raw_llm_output
parsed_test_cases (JSON)
generation_hash  # To detect if same input → different output

# test_cases table
id
generation_id
test_name
preconditions
steps (JSON)
expected_result
priority
staleness_status (fresh/stale/unknown)
```

---

### **6. Staleness Detection**

#### WHAT: "Are these test cases still valid?"
#### WHY:
- If manual says "Pressure limit: 180" and test checks "180", then changed to "190"
- Test is now STALE (doesn't match spec anymore)
- Need to alert user: "⚠️ This test case references outdated spec"

#### HOW - Three-Tier Approach:

**Tier 1: Exact Content Hash**
```
Generation created from node content_hash=abc123
Node content_hash in v2=xyz789
→ STALE (content definitely changed)
```

**Tier 2: Fuzzy Matching (if exact fails)**
```
- Calculate Levenshtein distance between v1 and v2 text
- If >90% similar → Probably same, flag as POSSIBLY_STALE
- If <50% similar → Definitely different
```

**Tier 3: Semantic Staleness (if you have time)**
```
- Extract numerical values from both versions
- "180 mmHg" vs "190 mmHg" = SEMANTICALLY_STALE
- "The device shall do X" vs "The device should do X" = probably NOT stale
```

#### Database:
```python
# staleness_checks table
id
generation_id
checked_against_node_id
checked_at_version
staleness_level ('fresh', 'possibly_stale', 'definitely_stale', 'unknown')
confidence_score (0-1)
diff_summary
last_checked_at
```

#### Honest Limitations:
- Can't distinguish "important change" from "typo fix"
- Can't auto-fix stale tests
- Levenshtein distance fails on rephrasing

---

### **7. Retrieval API**

#### Endpoints:
```
GET /selections/{selection_id}/generations
→ List all test case generations for this selection

GET /generations/{generation_id}
→ Get specific generation with:
  - test_cases (parsed)
  - staleness status for EACH test case
  - links back to original document sections

GET /generations/{generation_id}/staleness-report
→ Detailed staleness analysis:
  "generation created from node X version 1
   Node X in latest version: MODIFIED
   Staleness: HIGH (180→190 mmHg threshold)"
```

---

## 🏗️ DATA MODEL

```
documents
├── id (UUID)
├── name ("CT-200 Manual")
├── created_at

versions
├── id (UUID)
├── document_id → documents
├── version_number (1, 2, 3...)
├── ingested_at
├── is_latest

nodes
├── id (UUID)
├── version_id → versions
├── parent_id → nodes (nullable, for root sections)
├── heading
├── level (1, 2, 3, 4...)
├── body_text
├── content_hash (SHA256)
├── created_at
├── last_modified_at

node_mappings (version tracking)
├── v1_node_id → nodes
├── v2_node_id → nodes
├── change_type (unchanged/modified/deleted/created)
├── diff_summary

selections
├── id (UUID)
├── document_id → documents
├── name
├── version_pinned_to → versions
├── created_at

selection_nodes (junction)
├── selection_id → selections
├── node_id → nodes
├── node_version_id → versions
├── position

generations
├── id (UUID)
├── selection_id → selections
├── document_version → versions
├── generated_at
├── llm_provider
├── raw_output
├── parsed_output (JSON)
├── generation_hash

test_cases
├── id (UUID)
├── generation_id → generations
├── name
├── preconditions
├── steps (JSON)
├── expected_result
├── priority
├── staleness_status

staleness_checks
├── id (UUID)
├── generation_id → generations
├── checked_against_version → versions
├── staleness_level
├── confidence_score
├── last_checked_at
```

---

## ⚠️ THE DECISION LOG (Your Reasoning)

### Q1: What's most likely to silently give wrong results?

**Answer**: Staleness detection based on exact content hash.

**Why it fails**: If someone changes "180 mmHg" to "180 mmHg " (added space), the hash changes but the meaning doesn't. Conversely, if they change "shall" to "should", hash changes but it might not matter for the test.

**How to catch it**: 
- Build a "staleness dashboard" showing generations marked stale
- Manual review: Have QA engineer spot-check stale flags
- Unit tests with intentional small changes: verify staleness detection catches them

---

### Q2: Where did you choose simplicity over correctness?

**Answer**: Document version matching uses path-based hierarchy, not semantic similarity.

**What breaks in production**:
1. If Section 3.2 gets moved to Section 3.3 → treated as new node (duplicates test cases)
2. If section is deleted and re-added with same title → treated as same node (wrong!)
3. Large document reorganizations → can't tell moves from deletes

**What would you do differently**:
- Implement fuzzy title + position matching (not just exact path)
- Add manual override: "This moved node is the same as that moved node"
- Flag high-risk changes to human reviewer

---

### Q3: What input didn't you handle?

**Answer**: Binary/image content in PDFs.

**Your system does**: Falls back to OCR, but OCR of images is slow and inaccurate. If PDF has mostly diagrams (like electrical schematics), extraction produces garbage.

**What happens**: System creates nodes with empty body_text + OCR warning in metadata. User sees warning but downstream systems silently fail.

**Fix**: Explicitly detect image-heavy sections, skip them, add flag to selection API: "This selection contains 3 image-only sections with low OCR confidence."

---

## 🚀 TECH STACK DECISIONS

### Why FastAPI + SQLAlchemy?
- **FastAPI**: Fast, auto-docs (Swagger), async support for I/O-bound tasks
- **SQLAlchemy**: ORM for relational data (nodes, versions, relationships)

### Why MongoDB for LLM output?
- **Structured data**: Tree nodes = relational ✓ (SQL)
- **Unstructured data**: LLM outputs, diffs = flexible ✓ (NoSQL)
- **Why separate?** Allows independent scaling; can archive old generations without touching node tree

### Why Git?
- Version control of code (obvious)
- Approach doc changes tracked
- Commit messages = design decisions journal

---

## 📝 EXPECTED GIT COMMIT FLOW

```
commit 1: "Init: Project structure + dependencies"
commit 2: "Core: Database schema + SQLAlchemy models"
commit 3: "Parser: PDF OCR + hierarchy extraction (v1 only)"
commit 4: "Tests: Parser unit tests for edge cases"
commit 5: "Versioning: Node mapping + diff detection"
commit 6: "API: Browse endpoints (list, get, search)"
commit 7: "Selection: Version-pinned selection storage"
commit 8: "LLM: Prompt design + structured output parsing"
commit 9: "Staleness: Hash-based detection + reporting"
commit 10: "Tests: End-to-end flow (ingest v1 → select → generate → ingest v2 → check stale)"
commit 11: "Docs: Approach doc + API spec"
commit 12: "Demo: Postman collection + sample curl commands"
```

---

## ✅ SUCCESS CRITERIA

### Code Quality
- [ ] All 3+ parser edge cases have passing unit tests
- [ ] Versioning correctly maps v1 nodes to v2 nodes
- [ ] Staleness detection flags changed content (with false positives/negatives documented)

### Functionality
- [ ] Can ingest v1 PDF → browse tree → select sections → generate test cases
- [ ] Can re-ingest v2 PDF → old selections still work → staleness flags appear
- [ ] API returns proper error messages (not 500s)

### Documentation
- [ ] README with setup + run instructions
- [ ] Approach doc with all decisions explained
- [ ] Decision log with honest tradeoffs
- [ ] API spec (Swagger auto-generated by FastAPI)

### Submission
- [ ] GitHub repo with clean commit history
- [ ] Postman collection showing end-to-end flow
- [ ] Email to tri9t with links

---

## 🎓 WHAT THEY'RE REALLY TESTING

1. **Can you understand a complex system** (document + versions + staleness)?
2. **Do you make deliberate design choices** (not "I used Django because it's popular")?
3. **Can you handle ambiguity** (they don't tell you exact matching algorithm)?
4. **Do you validate your work** (tests, not just "it ran")?
5. **Can you communicate tradeoffs** (decision log with real reasoning)?

---

## ⏱️ TIME MANAGEMENT

- **Hours 0-3**: Setup + understand PDF structure
- **Hours 3-8**: Parser + basic tests
- **Hours 8-12**: Database + versioning logic
- **Hours 12-16**: Browse API + Selection API
- **Hours 16-20**: LLM integration + staleness
- **Hours 20-24**: Tests + docs + demo
- **Hours 24-25**: Approach doc + decision log (MOST IMPORTANT)

The decision log and approach doc are worth more than perfect code, because they show judgment.
