"""
PDF extraction and hierarchical document parsing.

This module handles the complex task of converting an unstructured PDF
into a hierarchical tree of sections.
"""

import re
import hashlib
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF - OCR and text extraction
import numpy as np


@dataclass
class PageText:
    """Extracted text from a single PDF page with layout info"""
    page_num: int
    text: str
    blocks: List[Dict]  # PyMuPDF blocks (text, bbox, font info)


@dataclass
class HeadingCandidate:
    """A potential heading detected during parsing"""
    text: str
    level: int  # Inferred heading level (1-4)
    page_num: int
    position: int  # Position in text (for ordering)
    font_size: Optional[float] = None
    is_bold: bool = False
    confidence: float = 1.0


@dataclass
class NodeData:
    """Internal representation of a document node before DB storage"""
    heading: str
    level: int
    body_text: str = ""
    children: List['NodeData'] = field(default_factory=list)
    page_num: Optional[int] = None
    position_in_parent: Optional[int] = None
    is_image_based: bool = False
    ocr_confidence: Optional[float] = None
    
    def compute_hash(self) -> str:
        """Compute SHA256 hash of content for version comparison"""
        content = f"{self.heading}|{self.body_text}".encode('utf-8')
        return hashlib.sha256(content).hexdigest()
    
    def to_hierarchical_path(self, parent_path: str = "") -> str:
        """Create hierarchical path like /Safety/Warnings/Pressure"""
        path = f"{parent_path}/{self.heading}"
        return path.strip("/")


class PDFExtractor:
    """
    Extracts text and structure from PDF documents.
    Handles multiple extraction strategies and falls back gracefully.
    """
    
    def __init__(self, pdf_path: str):
        """
        Initialize extractor.
        
        Args:
            pdf_path: Path to PDF file or supported text file
        """
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"File not found: {pdf_path}")
        
        self.pages: List[PageText] = []
        if self.pdf_path.suffix.lower() in {".md", ".markdown", ".txt"}:
            self._extract_text_from_markdown()
        else:
            self.doc = fitz.open(self.pdf_path)
            self._extract_text()
    
    def _extract_text_from_markdown(self) -> None:
        """Read markdown or plain text files as a single page."""
        text = self.pdf_path.read_text(encoding="utf-8", errors="ignore")
        self.pages.append(PageText(
            page_num=0,
            text=text,
            blocks=[]
        ))

    def _extract_text(self) -> None:
        """Extract text from all pages with layout preservation"""
        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            
            # Try text extraction first (works for text-based PDFs)
            text = page.get_text()
            blocks = page.get_text("blocks")  # Structured blocks with positions
            
            if text.strip():
                self.pages.append(PageText(
                    page_num=page_num,
                    text=text,
                    blocks=blocks
                ))
            else:
                # Fallback to OCR for scanned PDFs
                try:
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better OCR
                    ocr_text = page.get_text("text")
                    self.pages.append(PageText(
                        page_num=page_num,
                        text=ocr_text,
                        blocks=[]
                    ))
                except Exception as e:
                    print(f"Warning: Could not extract page {page_num}: {e}")
    
    def get_full_text(self) -> str:
        """Get complete document text"""
        return "\n\n".join(page.text for page in self.pages)


class HeadingDetector:
    """
    Detects headings using multiple strategies:
    1. Font size analysis (largest text = heading)
    2. Formatting (bold, all-caps)
    3. Position analysis (left margin reset)
    4. Structural patterns (numbering like "3.2.1")
    """
    
    # Font size thresholds for heading detection (in points)
    HEADING_FONT_SIZES = {
        1: (20, 100),    # H1: very large
        2: (14, 20),     # H2: large
        3: (11, 14),     # H3: medium
        4: (10, 11),     # H4: small
    }
    
    # Patterns that indicate headings
    HEADING_PATTERNS = [
        r"^(\d+(?:\.\d+)*)\s+([A-Z][^\n]+)$",  # "3.2 Section Name"
        r"^([A-Z][A-Z\s\-\&]+)$",  # "ALL CAPS HEADING"
        r"^(\d+)\)\s+([A-Za-z].+)$",  # "1) Heading"
    ]
    
    def __init__(self):
        self.detected_headings: List[HeadingCandidate] = []
    
    def detect_from_blocks(self, blocks: List[Dict]) -> List[HeadingCandidate]:
        """
        Detect headings from PyMuPDF block structure.

        PyMuPDF can return blocks as either dictionaries or tuples depending on version.
        This method handles both formats so parsing works across environments.
        """
        headings = []

        for i, block in enumerate(blocks):
            if isinstance(block, tuple):
                if len(block) < 5:
                    continue
                text = str(block[4]).strip()
                block_type = block[5] if len(block) > 5 else 0
                if block_type != 0:
                    continue
            else:
                block_type = block.get('type', 0)
                if block_type != 0:
                    continue
                text = str(block.get('text', '')).strip()

            if not text:
                continue

            # Extract font info if available
            font_size = None
            is_bold = False

            if isinstance(block, dict):
                if 'lines' in block:
                    for line in block['lines']:
                        for span in line.get('spans', []):
                            font_size = span.get('size', None)
                            flags = span.get('flags', 0)
                            is_bold = bool(flags & 2)  # Bold flag
            else:
                # PyMuPDF tuple blocks do not expose font metadata, so use a simple heuristic
                if len(text.split()) <= 6 and text.isupper():
                    is_bold = True
                if len(text.split()) <= 6 and text[0].isupper():
                    font_size = 16.0
            
            # Calculate heading level based on font size
            level = self._calculate_level(text, font_size, is_bold)
            
            if level:
                heading = HeadingCandidate(
                    text=text,
                    level=level,
                    page_num=0 if isinstance(block, tuple) else block.get('page_num', 0),
                    position=i,
                    font_size=font_size,
                    is_bold=is_bold,
                    confidence=self._calculate_confidence(text, font_size, is_bold)
                )
                headings.append(heading)
        
        return headings
    
    def detect_from_text(self, text: str) -> List[HeadingCandidate]:
        """
        Fallback: detect headings using regex patterns (for OCR'd text).
        """
        headings = []
        position = 0
        
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # Try each pattern
            for pattern in self.HEADING_PATTERNS:
                match = re.match(pattern, line)
                if match:
                    level = self._infer_level_from_text(line)
                    heading = HeadingCandidate(
                        text=line,
                        level=level,
                        page_num=0,
                        position=position,
                        confidence=0.7  # Lower confidence for regex-based detection
                    )
                    headings.append(heading)
                    break
            
            position += 1
        
        return headings
    
    def _calculate_level(self, text: str, font_size: Optional[float], is_bold: bool) -> Optional[int]:
        """Determine heading level from font properties"""
        
        # Priority 1: Use font size if available
        if font_size:
            for level, (min_size, max_size) in self.HEADING_FONT_SIZES.items():
                if min_size <= font_size <= max_size:
                    return level
        
        # Priority 2: Check text patterns
        level = self._infer_level_from_text(text)
        if level:
            return level
        
        # Priority 3: Bold text might be a heading
        if is_bold and len(text) < 100:  # Reasonable heading length
            return 3
        
        return None
    
    def _infer_level_from_text(self, text: str) -> Optional[int]:
        """Infer heading level from text numbering"""
        
        # Pattern: "1 Introduction" = Level 1
        if re.match(r"^\d+\s+[A-Z]", text):
            # Count dots: "1.2.3" = level 3
            dots = text.split()[0].count('.')
            return min(dots + 1, 4)  # Max level 4
        
        # Pattern: "A. Overview" = Level 2
        if re.match(r"^[A-Z]\.\s+[A-Z]", text):
            return 2
        
        # Pattern: "i) Sub-heading" = Level 3
        if re.match(r"^[ivx]+\)\s+[A-Z]", text):
            return 3
        
        # All caps usually = Level 1
        if text.isupper() and len(text) > 3:
            return 1
        
        return None
    
    def _calculate_confidence(self, text: str, font_size: Optional[float], is_bold: bool) -> float:
        """Calculate confidence score (0-1) for heading detection"""
        confidence = 0.5
        
        if font_size and font_size > 14:
            confidence += 0.3
        if is_bold:
            confidence += 0.1
        if len(text) < 100:  # Reasonable heading length
            confidence += 0.1
        
        return min(confidence, 1.0)


class HierarchyBuilder:
    """
    Builds hierarchical tree from detected headings and text.
    
    Algorithm:
    1. Detect all headings and their levels
    2. Scan through document text sequentially
    3. For each heading found, determine its parent based on level
    4. Group body text between headings
    5. Build tree structure
    """
    
    def __init__(self):
        self.root_nodes: List[NodeData] = []
        self.node_stack: List[NodeData] = []  # Stack for tracking hierarchy
    
    def build_hierarchy(self, 
                       full_text: str, 
                       headings: List[HeadingCandidate]) -> List[NodeData]:
        """
        Build hierarchical structure from headings and text.
        
        Args:
            full_text: Complete document text
            headings: List of detected headings with levels
            
        Returns:
            List of root-level nodes (with nested children)
        """
        
        if not headings:
            # No headings detected - treat entire document as one node
            return [NodeData(
                heading="Document",
                level=1,
                body_text=full_text
            )]
        
        # Sort headings by position in text
        headings = sorted(headings, key=lambda h: h.position)
        
        # Extract text segments between headings
        segments = self._extract_text_segments(full_text, headings)
        
        # Build tree
        self.root_nodes = []
        self.node_stack = []
        
        for i, heading in enumerate(headings):
            body_text = segments.get(i, "")
            level = heading.level if heading.level is not None else 1
            node = NodeData(
                heading=heading.text,
                level=level,
                body_text=body_text,
                page_num=heading.page_num,
                ocr_confidence=heading.confidence
            )
            
            self._add_node_to_hierarchy(node)
        
        return self.root_nodes
    
    def _extract_text_segments(self, 
                              full_text: str, 
                              headings: List[HeadingCandidate]) -> Dict[int, str]:
        """
        Extract body text for each heading.
        
        Text between heading[i] and heading[i+1] belongs to heading[i].
        """
        segments = {}
        text_lines = full_text.split('\n')
        
        for i, heading in enumerate(headings):
            # Find next heading position
            if i + 1 < len(headings):
                next_heading_pos = headings[i + 1].position
            else:
                next_heading_pos = len(text_lines)
            
            # Extract lines between this heading and next
            body_lines = text_lines[heading.position + 1:next_heading_pos]
            segments[i] = '\n'.join(body_lines).strip()
        
        return segments
    
    def _add_node_to_hierarchy(self, node: NodeData) -> None:
        """Add node to hierarchy based on its level"""
        
        # Normalize missing levels before comparison
        if node.level is None:
            node.level = 1
        while self.node_stack:
            top_level = self.node_stack[-1].level if self.node_stack[-1].level is not None else 1
            if top_level >= node.level:
                self.node_stack.pop()
            else:
                break
        
        # Add as child to current top of stack, or as root
        if self.node_stack:
            parent = self.node_stack[-1]
            node.position_in_parent = len(parent.children)
            parent.children.append(node)
        else:
            self.root_nodes.append(node)
        
        # Push this node onto stack
        self.node_stack.append(node)
    
    def validate_hierarchy(self) -> List[str]:
        """
        Validate the built hierarchy and return warnings.
        
        Checks for:
        - Duplicate headings (with different parents = OK, same parent = warning)
        - Empty sections
        - Inconsistent levels
        """
        warnings = []
        heading_counts = {}
        
        def check_node(node: NodeData, parent_path: str = ""):
            path = f"{parent_path}/{node.heading}"
            
            # Track duplicate headings
            heading_counts[node.heading] = heading_counts.get(node.heading, 0) + 1
            
            # Warning: empty section
            if not node.body_text and not node.children:
                warnings.append(f"Empty section: {path}")
            
            # Check children
            for i, child in enumerate(node.children):
                if child.level <= node.level:
                    warnings.append(
                        f"Inconsistent level: {child.heading} "
                        f"(level {child.level}) under {node.heading} (level {node.level})"
                    )
                check_node(child, path)
        
        for root in self.root_nodes:
            check_node(root)
        
        # Warn about duplicates
        for heading, count in heading_counts.items():
            if count > 1:
                warnings.append(f"Duplicate heading found {count} times: {heading}")
        
        return warnings


class DocumentParser:
    """
    High-level API for parsing PDF into hierarchical structure.
    Combines extraction, detection, and hierarchy building.
    """
    
    def __init__(self, pdf_path: str):
        """Initialize parser with PDF file"""
        self.pdf_path = pdf_path
        self.extractor = PDFExtractor(pdf_path)
        self.detector = HeadingDetector()
        self.hierarchy_builder = HierarchyBuilder()
    
    def parse(self) -> Tuple[List[NodeData], List[str]]:
        """
        Parse PDF into hierarchical document.
        
        Returns:
            (root_nodes, warnings)
        """
        
        # Extract text
        full_text = self.extractor.get_full_text()
        
        # Detect headings - try multiple strategies
        headings = self._detect_headings()
        
        if not headings:
            # Fallback: treat document as single node
            print(f"Warning: No headings detected in {self.pdf_path}")
        
        # Build hierarchy
        root_nodes = self.hierarchy_builder.build_hierarchy(full_text, headings)
        
        # Validate
        warnings = self.hierarchy_builder.validate_hierarchy()
        
        return root_nodes, warnings
    
    def _detect_headings(self) -> List[HeadingCandidate]:
        """Detect headings using best strategy"""
        
        # Strategy 1: Font-based detection (most accurate)
        headings = []
        for page in self.extractor.pages:
            if page.blocks:
                headings.extend(self.detector.detect_from_blocks(page.blocks))
        
        # Strategy 2: Pattern-based fallback (if Strategy 1 yielded nothing)
        if not headings:
            full_text = self.extractor.get_full_text()
            headings = self.detector.detect_from_text(full_text)
        
        return headings


# Unit Tests for Edge Cases
# ===========================

def test_duplicate_heading_creates_different_nodes():
    """Test: Two 'Overview' sections at different levels = different nodes"""
    builder = HierarchyBuilder()
    
    headings = [
        HeadingCandidate(text="Overview", level=1, page_num=0, position=0),
        HeadingCandidate(text="Introduction", level=2, page_num=1, position=10),
        HeadingCandidate(text="Overview", level=2, page_num=2, position=20),  # Duplicate name
    ]
    
    full_text = "Overview\n" + "\n" * 9 + "Introduction\n" + "\n" * 8 + "Overview\n"
    
    nodes = builder.build_hierarchy(full_text, headings)
    
    # Should have 1 root node (Overview level 1)
    assert len(nodes) == 1
    assert nodes[0].heading == "Overview"
    
    # Root node should have 2 children
    assert len(nodes[0].children) == 2
    assert nodes[0].children[0].heading == "Introduction"
    assert nodes[0].children[1].heading == "Overview"  # Second Overview as child
    
    print("✓ test_duplicate_heading_creates_different_nodes PASSED")


def test_inconsistent_font_handling():
    """Test: Bold and italic headings both detected"""
    detector = HeadingDetector()
    
    # Simulate two blocks with different formatting
    blocks = [
        {
            'type': 0,
            'text': "Section A",
            'page_num': 0,
            'lines': [[{
                'spans': [{'size': 18, 'flags': 2}]  # Bold
            }]]
        },
        {
            'type': 0,
            'text': "Subsection B",
            'page_num': 0,
            'lines': [[{
                'spans': [{'size': 14, 'flags': 1}]  # Italic (flag 1)
            }]]
        }
    ]
    
    headings = detector.detect_from_blocks(blocks)
    
    # Both should be detected
    assert len(headings) == 2
    assert headings[0].text == "Section A"
    assert headings[0].is_bold == True
    assert headings[1].text == "Subsection B"
    
    print("✓ test_inconsistent_font_handling PASSED")


def test_nested_list_preservation():
    """Test: Lists with sub-bullets preserved"""
    builder = HierarchyBuilder()
    
    # Heading followed by bulleted list
    list_text = """
    • Item 1
      ◦ Sub-item 1.1
      ◦ Sub-item 1.2
    • Item 2
    """
    
    headings = [
        HeadingCandidate(text="Features", level=1, page_num=0, position=0),
    ]
    
    nodes = builder.build_hierarchy(list_text, headings)
    
    # Body text should contain the full list structure
    assert "Sub-item 1.1" in nodes[0].body_text
    assert "Sub-item 1.2" in nodes[0].body_text
    
    print("✓ test_nested_list_preservation PASSED")


if __name__ == "__main__":
    # Run tests
    test_duplicate_heading_creates_different_nodes()
    test_inconsistent_font_handling()
    test_nested_list_preservation()
    print("\n✅ All parser tests passed!")
